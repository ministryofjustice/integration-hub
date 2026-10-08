import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import smoke_test


class SmokeTestTests(unittest.TestCase):
    def setUp(self):
        """Give each test a fresh simulated S3 service and clock, with no AWS calls."""
        self.elapsed = 0
        self.uploaded = b""
        self.incoming_key = ""
        self.destination_key = ""
        self.listings = 0
        self.calls = []
        self.corrupt_delivery = False
        self.never_deliver = False
        self.local_paths = []
        aws_patch = patch.object(smoke_test, "aws_s3", side_effect=self.fake_aws)
        self.aws = aws_patch.start()
        self.addCleanup(aws_patch.stop)
        clock_patch = patch.object(smoke_test.time, "monotonic", side_effect=lambda: self.elapsed)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        sleep_patch = patch.object(smoke_test.time, "sleep", side_effect=self.advance_time)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def advance_time(self, seconds):
        """Move the simulated clock forward instead of making the test really wait."""
        self.elapsed += seconds

    def fake_aws(self, operation, *arguments, timeout=30):
        """Simulate upload, listing and download while checking buckets and keys.

        First return a similarly named object to check exact-key matching,
        then deliver the fixture unless the test requests missing or corrupt data.
        """
        self.calls.append(operation)
        self.assertGreater(timeout, 0)
        bucket = arguments[arguments.index("--bucket") + 1]
        if operation == "put-object":
            self.assertEqual(bucket, smoke_test.INCOMING_BUCKET)
            self.incoming_key = arguments[arguments.index("--key") + 1]
            self.assertTrue(self.incoming_key.startswith(smoke_test.INCOMING_PREFIX))
            self.destination_key = (
                smoke_test.DESTINATION_PREFIX
                + self.incoming_key[len(smoke_test.INCOMING_PREFIX):]
            )
            source = Path(arguments[arguments.index("--body") + 1])
            self.local_paths.append(source)
            self.uploaded = source.read_bytes()
            return {}
        self.assertEqual(bucket, smoke_test.DESTINATION_BUCKET)
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
        self.fail(f"Unexpected AWS operation: {operation}")

    def test_waits_for_exact_key_and_compares_contents(self):
        """Reject a similar key, accept the exact delivery and remove local files."""
        output = io.StringIO()
        with redirect_stdout(output):
            smoke_test.run_smoke_test(timeout_seconds=10, poll_seconds=1)
        self.assertEqual(self.calls, ["put-object", "list-objects-v2", "list-objects-v2", "get-object"])
        self.assertEqual(self.elapsed, 1)
        self.assertTrue(all(not path.exists() for path in self.local_paths))
        messages = output.getvalue()
        stages = ["[1/5]", "[2/5]", "[3/5]", "[4/5]", "[5/5]", "PASS:"]
        positions = [messages.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("up to 10 seconds", messages)
        self.assertIn(
            f"Delivered file: s3://{smoke_test.DESTINATION_BUCKET}/{self.destination_key}",
            messages,
        )

    def test_missing_delivery_times_out_without_download(self):
        """Stop at the deadline when no file arrives, without attempting a download."""
        self.never_deliver = True
        with self.assertRaisesRegex(TimeoutError, "Timed out"):
            smoke_test.run_smoke_test(timeout_seconds=2, poll_seconds=1)
        self.assertEqual(self.elapsed, 2)
        self.assertNotIn("get-object", self.calls)
        self.assertTrue(all(not path.exists() for path in self.local_paths))

    def test_content_mismatch_fails(self):
        """Fail when a delivered file exists but its bytes differ from the fixture."""
        self.corrupt_delivery = True
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "do not match"):
                smoke_test.run_smoke_test(timeout_seconds=10, poll_seconds=1)
        self.assertNotIn("PASS:", output.getvalue())

    def test_aws_errors_fail_immediately(self):
        """Stop after an AWS error rather than continuing to poll for delivery."""
        self.aws.side_effect = RuntimeError("AWS access denied")
        with self.assertRaisesRegex(RuntimeError, "access denied"):
            smoke_test.run_smoke_test()
        self.assertEqual(self.aws.call_count, 1)

    def test_each_run_uses_a_new_key(self):
        """Give successive runs different keys and contents to avoid stale results."""
        smoke_test.run_smoke_test()
        first_key = self.incoming_key
        first_fixture = self.uploaded
        smoke_test.run_smoke_test()
        self.assertNotEqual(first_key, self.incoming_key)
        self.assertNotEqual(first_fixture, self.uploaded)

    def test_invalid_timeouts_make_no_aws_calls(self):
        """Reject nonpositive timing settings before contacting AWS."""
        for timeout, interval in [(0, 1), (1, 0), (-1, 1)]:
            with self.subTest(timeout=timeout, interval=interval):
                with self.assertRaises(ValueError):
                    smoke_test.run_smoke_test(timeout, interval)
        self.aws.assert_not_called()


class AwsCommandTests(unittest.TestCase):
    @patch("smoke_test.subprocess.run")
    def test_command_uses_json_region_and_timeout(self, run):
        """Check CLI construction and JSON parsing without launching the AWS CLI."""
        run.return_value.stdout = '{"Contents": []}'
        self.assertEqual(smoke_test.aws_s3("list-objects-v2", timeout=4), {"Contents": []})
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["aws", "s3api", "list-objects-v2"])
        self.assertIn("eu-west-2", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("smoke_test.subprocess.run")
    def test_failed_commands_are_not_treated_as_missing_objects(self, run):
        """Surface CLI failures, timeouts and a missing executable as errors."""
        for error in [
            subprocess.CalledProcessError(1, "aws", stderr="sensitive details"),
            subprocess.TimeoutExpired("aws", 4),
            FileNotFoundError("aws"),
        ]:
            with self.subTest(error=type(error).__name__):
                run.side_effect = error
                with self.assertRaisesRegex(RuntimeError, "AWS list-objects-v2 failed or timed out"):
                    smoke_test.aws_s3("list-objects-v2")


if __name__ == "__main__":
    unittest.main()