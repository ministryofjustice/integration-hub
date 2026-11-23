import hashlib
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from common import routing
from lifecycle import investigation as investigation_test
from lifecycle import quarantine as quarantine_test


class InvestigationTests(unittest.TestCase):
    def run_with_routing(self, area):
        uploaded = {}
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "test-document.pdf"
            fixture.write_bytes(b"%PDF-1.4\nmock routing fixture")

            def fake_s3(operation, *arguments, timeout):
                bucket = arguments[arguments.index("--bucket") + 1]
                self.assertGreater(timeout, 0)
                self.assertTrue(bucket.startswith("integration-hub-file-transfer-development-"))
                if operation == "put-object":
                    self.assertTrue(bucket.endswith("-incoming"))
                    self.assertEqual(Path(arguments[arguments.index("--body") + 1]), fixture)
                    uploaded["key"] = arguments[arguments.index("--key") + 1]
                    self.assertRegex(
                        uploaded["key"],
                        r"\Atest-harness/investigation/[0-9a-f]{32}/test-document\.pdf\Z",
                    )
                    return {}
                self.assertEqual(operation, "list-objects-v2")
                key = arguments[arguments.index("--prefix") + 1]
                self.assertEqual(key, uploaded["key"])
                return {"Contents": [{"Key": key if bucket.endswith("-" + area) else key + ".bak"}]}

            stderr = io.StringIO()
            with patch.object(investigation_test, "FIXTURE", fixture), \
                 patch.object(routing, "aws_s3", side_effect=fake_s3), \
                 redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                status = investigation_test.main(["--environment", "development"])
        return status, stderr.getvalue()

    def test_investigation_accepts_exact_key(self):
        self.assertEqual(self.run_with_routing("investigation"), (0, ""))

    def test_investigation_rejects_wrong_bucket(self):
        for area in ("clean", "quarantine"):
            with self.subTest(area=area):
                status, error = self.run_with_routing(area)
                self.assertEqual(status, 1)
                self.assertIn(f"Fixture arrived in {area}, not investigation", error)


class QuarantineTests(unittest.TestCase):
    def run_with_download(self, contents, area, local_contents=None):
        fixture_bytes = b"x" * 68
        uploaded = {}
        local_dir = tempfile.TemporaryDirectory()
        self.addCleanup(local_dir.cleanup)
        local_fixture = Path(local_dir.name) / "eicar.txt"
        if local_contents is not None:
            local_fixture.write_bytes(local_contents)

        def fake_curl(command, **kwargs):
            self.assertEqual(command[0], "curl")
            self.assertEqual(command[-1], "https://secure.eicar.org/eicar.com")
            if contents is None:
                raise subprocess.CalledProcessError(22, command)
            Path(command[command.index("--output") + 1]).write_bytes(contents)

        def fake_s3(operation, *arguments, timeout):
            bucket = arguments[arguments.index("--bucket") + 1]
            if operation == "put-object":
                self.assertEqual(bucket, "integration-hub-file-transfer-development-incoming")
                uploaded["path"] = Path(arguments[arguments.index("--body") + 1])
                self.assertEqual(uploaded["path"].read_bytes(), fixture_bytes)
                uploaded["key"] = arguments[arguments.index("--key") + 1]
                filename = r"eicar\.txt" if local_contents == fixture_bytes else r"eicar\.com"
                self.assertRegex(uploaded["key"], rf"\Atest-harness/quarantine/[0-9a-f]{{32}}/{filename}\Z")
                return {}
            self.assertEqual(operation, "list-objects-v2")
            key = arguments[arguments.index("--prefix") + 1]
            self.assertEqual(key, uploaded["key"])
            return {"Contents": [{"Key": key}]} if bucket.endswith("-" + area) else {}

        stderr = io.StringIO()
        with patch.object(quarantine_test, "LOCAL_EICAR_FIXTURE", local_fixture), \
             patch.object(quarantine_test.subprocess, "run", side_effect=fake_curl) as curl, \
             patch.object(quarantine_test, "EICAR_SHA256", hashlib.sha256(fixture_bytes).hexdigest()), \
             patch.object(routing, "aws_s3", side_effect=fake_s3) as s3, \
             redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            status = quarantine_test.main(["--environment", "development"])
        if local_contents == fixture_bytes:
            curl.assert_not_called()
            self.assertEqual(uploaded["path"], local_fixture)
            self.assertTrue(local_fixture.exists())
        elif uploaded:
            curl.assert_called_once()
            self.assertFalse(uploaded["path"].exists())
        return status, stderr.getvalue(), s3

    def test_quarantine_accepts_exact_key(self):
        status, error, _ = self.run_with_download(b"x" * 68, "quarantine")
        self.assertEqual(status, 0)
        self.assertIn("Trying curl", error)

    def test_quarantine_prefers_valid_local_fixture(self):
        status, error, _ = self.run_with_download(None, "quarantine", local_contents=b"x" * 68)
        self.assertEqual((status, error), (0, ""))

    def test_quarantine_falls_back_when_local_fixture_is_invalid(self):
        status, error, _ = self.run_with_download(b"x" * 68, "quarantine", local_contents=b"invalid")
        self.assertEqual(status, 0)
        self.assertIn("does not match the EICAR fixture", error)
        self.assertIn("Trying curl", error)

    def test_quarantine_rejects_wrong_bucket(self):
        for area in ("clean", "investigation"):
            with self.subTest(area=area):
                status, error, _ = self.run_with_download(b"x" * 68, area)
                self.assertEqual(status, 1)
                self.assertIn(f"Fixture arrived in {area}, not quarantine", error)

    def test_quarantine_does_not_upload_unexpected_download(self):
        status, error, s3 = self.run_with_download(b"y" * 68, "quarantine")
        self.assertEqual(status, 1)
        self.assertIn("does not match the EICAR fixture", error)
        s3.assert_not_called()

    def test_quarantine_does_not_upload_when_curl_fails(self):
        status, error, s3 = self.run_with_download(None, "quarantine")
        self.assertEqual(status, 1)
        self.assertIn("Could not download the EICAR fixture with curl", error)
        s3.assert_not_called()


class TerminalRoutingTests(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0
        aws_patch = patch.object(routing, "aws_s3")
        self.aws = aws_patch.start()
        self.addCleanup(aws_patch.stop)
        clock_patch = patch.object(routing.time, "monotonic", side_effect=lambda: self.elapsed)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        sleep_patch = patch.object(routing.time, "sleep", side_effect=self.advance_time)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def advance_time(self, seconds):
        """Move the simulated clock forward so timeout tests need no real waiting."""
        self.elapsed += seconds

    def mock_terminal_listing(self, keys_at_time):
        poll_times = []

        def fake_s3(operation, *arguments, timeout):
            if operation == "put-object":
                return {}
            self.assertEqual(operation, "list-objects-v2")
            bucket = arguments[arguments.index("--bucket") + 1]
            if bucket != "integration-hub-file-transfer-test-investigation":
                return {}
            key = arguments[arguments.index("--prefix") + 1]
            poll_times.append(self.elapsed)
            return {"Contents": [{"Key": item} for item in keys_at_time(key, self.elapsed)]}

        self.aws.side_effect = fake_s3
        return poll_times

    def test_terminal_routing_waits_for_exact_key_after_first_poll(self):
        poll_times = self.mock_terminal_listing(
            lambda key, elapsed: [key] if elapsed >= 1 else [key + ".bak"]
        )
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output):
            fixture = Path(directory) / "fixture.txt"
            fixture.write_text("mock routing fixture")
            routing.run_terminal_routing_test(
                "test", fixture, "investigation", "text/plain", timeout_seconds=2, poll_seconds=1,
            )

        self.assertEqual(poll_times, [0, 1])
        self.assertEqual(self.elapsed, 1)
        self.assertIn("PASS: fixture arrived", output.getvalue())

    def test_terminal_routing_times_out_without_exact_key(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.txt"
            fixture.write_text("mock routing fixture")
            for listing in ("empty", "similar key only"):
                with self.subTest(listing=listing):
                    self.elapsed = 0
                    poll_times = self.mock_terminal_listing(
                        lambda key, elapsed: [key + ".bak"] if listing == "similar key only" else []
                    )
                    output = io.StringIO()
                    with redirect_stdout(output):
                        with self.assertRaisesRegex(TimeoutError, "Timed out waiting for .*investigation"):
                            routing.run_terminal_routing_test(
                                "test", fixture, "investigation", "text/plain",
                                timeout_seconds=2, poll_seconds=1,
                            )

                    self.assertEqual(poll_times, [0, 1])
                    self.assertEqual(self.elapsed, 2)
                    self.assertNotIn("PASS:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
