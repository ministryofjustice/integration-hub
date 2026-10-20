import io
import json
import subprocess
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import harness
from functional_test import functional_scenarios
from smoke_test import smoke_scenarios


class HarnessTests(unittest.TestCase):
    def setUp(self):
        """Give each test a fresh simulated S3 service and clock."""
        self.elapsed = 0
        self.uploaded = b""
        self.upload_metadata = {}
        self.upload_content_type = ""
        self.incoming_key = ""
        self.destination_key = ""
        self.listings = 0
        self.calls = []
        self.corrupt_delivery = False
        self.never_deliver = False
        self.local_paths = []
        aws_patch = patch.object(harness, "aws_s3", side_effect=self.fake_aws)
        self.aws = aws_patch.start()
        self.addCleanup(aws_patch.stop)
        clock_patch = patch.object(harness.time, "monotonic", side_effect=lambda: self.elapsed)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        sleep_patch = patch.object(harness.time, "sleep", side_effect=self.advance_time)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def fake_concurrent_aws(self, operation, *arguments, timeout=30):
        self.assertGreater(timeout, 0)
        bucket = arguments[arguments.index("--bucket") + 1]
        if operation == "put-object":
            self.assertEqual(bucket, harness.INCOMING_BUCKET)
            self.upload_barrier.wait(timeout=5)
            incoming_key = arguments[arguments.index("--key") + 1]
            source = Path(arguments[arguments.index("--body") + 1])
            relative_key = incoming_key[len(harness.INCOMING_PREFIX):]
            with self.concurrent_lock:
                self.uploaded_by_key[relative_key] = source.read_bytes()
            return {}
        self.assertEqual(bucket, harness.DESTINATION_BUCKET)
        if operation == "list-objects-v2":
            prefix = arguments[arguments.index("--prefix") + 1]
            with self.concurrent_lock:
                return {
                    "Contents": [
                        {"Key": harness.DESTINATION_PREFIX + key}
                        for key in self.uploaded_by_key
                        if key.startswith(prefix[len(harness.DESTINATION_PREFIX):])
                    ]
                }
        if operation == "get-object":
            destination_key = arguments[arguments.index("--key") + 1]
            relative_key = destination_key[len(harness.DESTINATION_PREFIX):]
            destination = Path(arguments[-1])
            with self.concurrent_lock:
                destination.write_bytes(self.uploaded_by_key[relative_key])
                self.downloaded_keys.append(destination_key)
            return {}
        self.fail(f"Unexpected AWS operation: {operation}")

    def advance_time(self, seconds):
        self.elapsed += seconds

    def fake_aws(self, operation, *arguments, timeout=30):
        """Simulate upload, exact-key listing, download, and HeadObject."""
        self.calls.append(operation)
        self.assertGreater(timeout, 0)
        bucket = arguments[arguments.index("--bucket") + 1]
        if operation == "put-object":
            self.assertEqual(bucket, harness.INCOMING_BUCKET)
            self.incoming_key = arguments[arguments.index("--key") + 1]
            self.assertTrue(self.incoming_key.startswith(harness.INCOMING_PREFIX))
            self.listings = 0
            self.destination_key = (
                harness.DESTINATION_PREFIX
                + self.incoming_key[len(harness.INCOMING_PREFIX):]
            )
            source = Path(arguments[arguments.index("--body") + 1])
            self.local_paths.append(source)
            self.uploaded = source.read_bytes()
            if "--metadata" in arguments:
                self.upload_metadata = json.loads(arguments[arguments.index("--metadata") + 1])
            if "--content-type" in arguments:
                self.upload_content_type = arguments[arguments.index("--content-type") + 1]
            return {}
        self.assertEqual(bucket, harness.DESTINATION_BUCKET)
        if operation == "list-objects-v2":
            self.assertEqual(arguments[arguments.index("--prefix") + 1], self.destination_key)
            self.listings += 1
            if self.never_deliver:
                return {}
            if self.listings == 1:
                return {"Contents": [{"Key": self.destination_key + ".old"}]}
            return {"Contents": [{"Key": self.destination_key}]}
        if operation == "get-object":
            self.assertEqual(arguments[arguments.index("--key") + 1], self.destination_key)
            destination = Path(arguments[-1])
            self.local_paths.append(destination)
            destination.write_bytes(b"incorrect" if self.corrupt_delivery else self.uploaded)
            return {}
        if operation == "head-object":
            self.assertEqual(arguments[arguments.index("--key") + 1], self.destination_key)
            return {
                "ContentLength": len(self.uploaded),
                "ContentType": self.upload_content_type,
                "Metadata": self.upload_metadata,
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "test-destination-key",
            }
        self.fail(f"Unexpected AWS operation: {operation}")

    def test_l1_checks_one_small_file_and_compares_contents(self):
        output = io.StringIO()
        with redirect_stdout(output):
            harness.run_delivery_test(
                "L1 smoke checks", smoke_scenarios, timeout_seconds=10, poll_seconds=1
            )
        self.assertEqual(
            self.calls,
            ["put-object", "list-objects-v2", "list-objects-v2", "get-object"],
        )
        self.assertEqual(self.elapsed, 1)
        self.assertTrue(all(not path.exists() for path in self.local_paths))
        messages = output.getvalue()
        self.assertIn("PASS: small text delivered unchanged", messages)
        self.assertIn("PASS: all 1 file-transfer scenarios completed.", messages)
        self.assertNotIn("zero-byte file", messages)

    def test_l2_checks_integrity_metadata_and_encryption(self):
        output = io.StringIO()
        with redirect_stdout(output):
            harness.run_delivery_test(
                "L2 functional integrity checks",
                functional_scenarios,
                verify_metadata=True,
                timeout_seconds=10,
                poll_seconds=1,
            )
        expected_calls = [
            operation
            for _ in range(4)
            for operation in (
                "put-object", "list-objects-v2", "list-objects-v2", "get-object",
                "head-object",
            )
        ]
        self.assertEqual(self.calls, expected_calls)
        self.assertEqual(self.elapsed, 4)
        self.assertTrue(all(not path.exists() for path in self.local_paths))
        messages = output.getvalue()
        for scenario in ["zero-byte file", "binary file", "2 MiB file", "special-character filename"]:
            self.assertIn(f"PASS: {scenario} delivered unchanged", messages)
        self.assertIn("PASS: all 4 file-transfer scenarios completed.", messages)
        self.assertEqual(
            self.upload_metadata,
            {"file-transfer-test-run-id": self.incoming_key.split("/")[2]},
        )
        self.assertEqual(self.upload_content_type, "application/octet-stream")
        self.assertIn("metadata and KMS encryption verified", messages)
        self.assertNotIn("small text delivered unchanged", messages)
        self.assertIn("special chars + (1).txt", messages)

    def test_l3_uploads_and_verifies_concurrent_files_independently(self):
        self.upload_barrier = threading.Barrier(3)
        self.concurrent_lock = threading.Lock()
        self.uploaded_by_key = {}
        self.downloaded_keys = []
        aws_patch = patch.object(harness, "aws_s3", side_effect=self.fake_concurrent_aws)
        aws_patch.start()
        self.addCleanup(aws_patch.stop)
        output = io.StringIO()
        with redirect_stdout(output):
            harness.run_concurrent_delivery_test(timeout_seconds=10, poll_seconds=1)

        self.assertEqual(len(self.uploaded_by_key), 3)
        self.assertEqual(len(self.downloaded_keys), 3)
        self.assertEqual(len(set(self.downloaded_keys)), 3)
        self.assertIn("PASS: all 3 concurrent files arrived independently.", output.getvalue())

    def test_missing_delivery_times_out_without_download(self):
        self.never_deliver = True
        with self.assertRaisesRegex(TimeoutError, "Timed out"):
            harness.run_delivery_test(
                "L1 smoke checks", smoke_scenarios, timeout_seconds=2, poll_seconds=1
            )
        self.assertEqual(self.elapsed, 2)
        self.assertNotIn("get-object", self.calls)
        self.assertTrue(all(not path.exists() for path in self.local_paths))

    def test_content_mismatch_fails(self):
        self.corrupt_delivery = True
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "do not match"):
                harness.run_delivery_test(
                    "L1 smoke checks", smoke_scenarios, timeout_seconds=10, poll_seconds=1
                )

    def test_aws_errors_fail_immediately(self):
        self.aws.side_effect = RuntimeError("AWS access denied")
        with self.assertRaisesRegex(RuntimeError, "access denied"):
            harness.run_delivery_test("L1 smoke checks", smoke_scenarios)
        self.assertEqual(self.aws.call_count, 1)

    def test_each_run_uses_a_new_key(self):
        harness.run_delivery_test("L1 smoke checks", smoke_scenarios)
        first_key = self.incoming_key
        first_fixture = self.uploaded
        harness.run_delivery_test("L1 smoke checks", smoke_scenarios)
        self.assertNotEqual(first_key, self.incoming_key)
        self.assertNotEqual(first_fixture, self.uploaded)

    def test_invalid_timeouts_make_no_aws_calls(self):
        for timeout, interval in [(0, 1), (1, 0), (-1, 1)]:
            with self.subTest(timeout=timeout, interval=interval):
                with self.assertRaises(ValueError):
                    harness.run_delivery_test(
                        "L1 smoke checks",
                        smoke_scenarios,
                        timeout_seconds=timeout,
                        poll_seconds=interval,
                    )
        self.aws.assert_not_called()


class AwsCommandTests(unittest.TestCase):
    @patch("harness.subprocess.run")
    def test_command_uses_json_region_and_timeout(self, run):
        run.return_value.stdout = '{"Contents": []}'
        self.assertEqual(harness.aws_s3("list-objects-v2", timeout=4), {"Contents": []})
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["aws", "s3api", "list-objects-v2"])
        self.assertIn("eu-west-2", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("harness.subprocess.run")
    def test_failed_commands_are_not_treated_as_missing_objects(self, run):
        for error in [
            subprocess.CalledProcessError(1, "aws", stderr="sensitive details"),
            subprocess.TimeoutExpired("aws", 4),
            FileNotFoundError("aws"),
        ]:
            with self.subTest(error=type(error).__name__):
                run.side_effect = error
                with self.assertRaisesRegex(RuntimeError, "AWS list-objects-v2 failed or timed out"):
                    harness.aws_s3("list-objects-v2")


if __name__ == "__main__":
    unittest.main()