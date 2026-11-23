import io
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from common import delivery as harness
from common.fixtures import functional_scenarios
from common.fixtures import smoke_scenarios
from unit.support import HarnessTestCase


class HarnessTests(HarnessTestCase):
    def test_l1_checks_one_small_file_and_compares_contents(self):
        """Check L1 uploads and verifies one file, ignores similar keys, and cleans up.

        The fake first returns a filename ending in .old; the runner must keep
        waiting for the exact file instead of reporting an early success.
        """
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
        """Check L2 runs all four fixtures and requests the extra object checks.

        The fake preserves bytes and metadata and reports KMS encryption, so the
        runner should pass and remove its temporary local files.
        """
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
        """Check three uploads overlap and all three distinct files are downloaded.

        The simulated S3 service waits for all upload workers to reach the barrier
        and returns the bytes saved for each file's own key.
        """
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
        """Check a file that never arrives causes a timeout, not a false pass."""
        self.never_deliver = True
        with self.assertRaisesRegex(TimeoutError, "Timed out"):
            harness.run_delivery_test(
                "L1 smoke checks", smoke_scenarios, timeout_seconds=2, poll_seconds=1
            )
        self.assertEqual(self.elapsed, 2)
        self.assertNotIn("get-object", self.calls)
        self.assertTrue(all(not path.exists() for path in self.local_paths))

    def test_content_mismatch_fails(self):
        """Check that finding the file is not enough: changed contents must fail."""
        self.corrupt_delivery = True
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "do not match"):
                harness.run_delivery_test(
                    "L1 smoke checks", smoke_scenarios, timeout_seconds=10, poll_seconds=1
                )

    def test_delivery_measurements_separate_observation_from_verification(self):
        """Use a fake clock to check upload/poll timing excludes the later download."""
        measurements = []

        def timed_aws(operation, *arguments, timeout=30):
            """Simulate upload, listing and download taking different amounts of time."""
            self.elapsed += {"put-object": 2, "list-objects-v2": 1, "get-object": 3}[operation]
            return self.fake_aws(operation, *arguments, timeout=timeout)

        self.aws.side_effect = timed_aws
        with redirect_stdout(io.StringIO()):
            run_token = harness.run_delivery_test(
                "L4 timing probe", smoke_scenarios, timeout_seconds=20,
                poll_seconds=1, measurements=measurements,
            )
        self.assertEqual(measurements, [{
            "run_token": run_token,
            "filename": "payload.txt",
            "size_bytes": len(self.uploaded),
            "observed_delivery_seconds": 5,
            "verified_seconds": 8,
        }])

    def test_failed_delivery_does_not_record_a_verified_measurement(self):
        """Do not report missing or corrupted files as successful timing samples."""
        for failure in ("never_deliver", "corrupt_delivery"):
            with self.subTest(failure=failure):
                self.never_deliver = failure == "never_deliver"
                self.corrupt_delivery = failure == "corrupt_delivery"
                measurements = []
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises((TimeoutError, RuntimeError)):
                        harness.run_delivery_test(
                            "L4 timing probe", smoke_scenarios, timeout_seconds=2,
                            poll_seconds=1, measurements=measurements,
                        )
                self.assertEqual(measurements, [])

    def test_aws_errors_fail_immediately(self):
        """Check an AWS error stops the test immediately instead of polling forever."""
        self.aws.side_effect = RuntimeError("AWS access denied")
        with self.assertRaisesRegex(RuntimeError, "access denied"):
            harness.run_delivery_test("L1 smoke checks", smoke_scenarios)
        self.assertEqual(self.aws.call_count, 1)

    def test_each_run_uses_a_new_key(self):
        """Check separate runs use different paths and contents to avoid stale passes."""
        harness.run_delivery_test("L1 smoke checks", smoke_scenarios)
        first_key = self.incoming_key
        first_fixture = self.uploaded
        harness.run_delivery_test("L1 smoke checks", smoke_scenarios)
        self.assertNotEqual(first_key, self.incoming_key)
        self.assertNotEqual(first_fixture, self.uploaded)

    def test_invalid_timeouts_make_no_aws_calls(self):
        """Check invalid time limits are rejected before any AWS operation is attempted."""
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


if __name__ == "__main__":
    unittest.main()
