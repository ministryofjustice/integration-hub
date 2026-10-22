import io
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import harness
import performance_test
from functional_test import functional_scenarios
import resilience_test
from resilience_test import build_duplicate_action_request
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
        """Simulate S3 for overlapping uploads, keeping each file's bytes separately.

        The barrier waits for all uploads to start. A serial implementation would
        time out here. The lock protects shared test data while workers update it.
        """
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
        """Move the simulated clock forward so timeout tests need no real waiting."""
        self.elapsed += seconds

    def test_duplicate_action_request_has_stable_idempotency_key(self):
        """Check that the request's duplicate-detection key uses its action ID.

        Also verify that the source file and configuration version are included.
        This builds a request locally; it does not call the deployed writer.
        """
        source_object = {
            "bucket": harness.CLEAN_BUCKET,
            "key": "test-harness/push-to-s3/run/file.bin",
            "versionId": "source-version",
            "sizeBytes": 12,
        }
        request = build_duplicate_action_request(
            "123456789012",
            source_object,
            "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-abcdef",
            "secret-version",
        )
        data = request["detail"]["data"]
        action_id = data["actionExecutionId"]

        self.assertEqual(data["object"], source_object)
        self.assertEqual(data["action"], {"name": "push-to-s3"})
        self.assertEqual(
            request["detail"]["metadata"]["idempotencyKey"],
            f"action-request:{action_id}",
        )
        self.assertEqual(data["configurationReference"]["secretVersionId"], "secret-version")

    def test_l3_duplicate_action_sends_identical_request_twice(self):
        """Check the L3 test reuses one request and accepts a copy followed by no copy.

        AWS calls are replaced with local fakes. The simulated versions are A,
        then B, then B again. This checks the test logic, not live AWS behaviour.
        """
        source_object = {
            "bucket": harness.CLEAN_BUCKET,
            "key": "test-harness/push-to-s3/run/duplicate-idempotency-seed.bin",
            "versionId": "source-version",
            "sizeBytes": 12,
        }
        secret = {
            "ARN": "arn:aws:secretsmanager:eu-west-2:123456789012:secret:dispatch-AbCdE1",
            "VersionIdsToStages": {"secret-version": ["AWSCURRENT"]},
        }
        invocations = []
        with (
            patch.object(resilience_test, "run_delivery_test") as seed,
            patch.object(
                resilience_test,
                "_current_clean_version",
                return_value=source_object,
            ),
            patch.object(resilience_test, "aws_cli", side_effect=[secret, {"Account": "123456789012"}]),
            patch.object(
                resilience_test,
                "_head_destination_version",
                side_effect=["seed-version", "copied-version", "copied-version"],
            ),
            patch.object(
                resilience_test,
                "_invoke_writer",
                side_effect=lambda event, queue_arn, response_path: invocations.append(event),
            ),
        ):
            with redirect_stdout(io.StringIO()):
                resilience_test.run_duplicate_action_test(timeout_seconds=10)

        seed.assert_called_once()
        self.assertEqual(len(invocations), 2)
        self.assertEqual(invocations[0], invocations[1])
        self.assertEqual(
            invocations[0]["detail"]["metadata"]["idempotencyKey"],
            f"action-request:{invocations[0]['detail']['data']['actionExecutionId']}",
        )

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

    def test_l4_records_size_grouped_baselines(self):
        """Run all L4 fixtures against fake S3 and verify exact sizes and timing maths."""
        with redirect_stdout(io.StringIO()):
            report = performance_test.run_performance_test()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["expected_bytes"], 19 * performance_test.MIB)
        self.assertEqual(
            [sample["size_bytes"] for sample in report["measurements"]],
            [size * performance_test.MIB for size in (1, 1, 1, 16)],
        )
        self.assertEqual(len({sample["filename"] for sample in report["measurements"]}), 4)
        self.assertTrue(all(sample["run_token"] == report["run_token"] for sample in report["measurements"]))
        small, large = report["baselines"]
        self.assertEqual(small["sample_count"], 3)
        self.assertEqual(small["median_observed_delivery_seconds"], 2)
        self.assertEqual(small["effective_mib_per_second"], 0.5)
        self.assertEqual(large["sample_count"], 1)
        self.assertEqual(large["effective_mib_per_second"], 8)
        self.assertTrue(all(not path.exists() for path in self.local_paths))

    def test_l4_failure_writes_partial_report_and_returns_failure(self):
        """A corrupted second file must fail, retaining only the first verified sample."""
        def fake_partial_delivery(operation, *arguments, timeout=30):
            """Switch corruption on when the second upload starts."""
            if operation == "put-object":
                self.corrupt_delivery = self.calls.count("put-object") >= 1
            return self.fake_aws(operation, *arguments, timeout=timeout)

        self.aws.side_effect = fake_partial_delivery
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "l4.json"
            summary_path = Path(directory) / "summary.md"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = performance_test.run_l4_cli([
                    "--report", str(report_path), "--summary", str(summary_path),
                ])
            report = json.loads(report_path.read_text())
            self.assertEqual(result, 1)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(len(report["measurements"]), 1)
            self.assertIn("do not match", report["error"])
            self.assertIn("**failed**", summary_path.read_text())
            self.assertIn("1/4", summary_path.read_text())

    def test_l4_cli_writes_completed_report(self):
        """A successful run writes machine-readable evidence and a readable summary."""
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "l4.json"
            with redirect_stdout(io.StringIO()) as output:
                result = performance_test.run_l4_cli(["--report", str(report_path)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(report_path.read_text())["status"], "completed")
            self.assertIn("Performance numbers are informational", output.getvalue())

    def test_l4_summary_handles_empty_or_zero_duration_samples(self):
        """Do not invent rates when there are no verified files or no elapsed time."""
        self.assertEqual(performance_test.summarise_measurements([]), [])
        baselines = performance_test.summarise_measurements([{
            "size_bytes": performance_test.MIB,
            "observed_delivery_seconds": 0,
            "verified_seconds": 0,
        }])
        self.assertIsNone(baselines[0]["effective_mib_per_second"])

    def test_l4_rate_uses_total_bytes_and_time_not_average_individual_rates(self):
        """Different sample durations must be weighted by elapsed time when reporting rate."""
        measurements = [
            {
                "size_bytes": performance_test.MIB,
                "observed_delivery_seconds": observed,
                "verified_seconds": verified,
            }
            for observed, verified in [(1, 2), (3, 6), (2, 4)]
        ]
        baseline = performance_test.summarise_measurements(measurements)[0]
        self.assertEqual(baseline["min_observed_delivery_seconds"], 1)
        self.assertEqual(baseline["median_observed_delivery_seconds"], 2)
        self.assertEqual(baseline["max_observed_delivery_seconds"], 3)
        self.assertEqual(baseline["effective_mib_per_second"], 3 / 12)

    def test_l4_timeout_writes_failed_report_without_successful_samples(self):
        """An informational baseline must still report a missing delivery as a failure."""
        self.never_deliver = True
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "l4.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = performance_test.run_l4_cli(["--report", str(report_path)])
            report = json.loads(report_path.read_text())
            self.assertEqual(result, 1)
            self.assertEqual(self.elapsed, performance_test.TIMEOUT_SECONDS)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["measurements"], [])
            self.assertEqual(report["baselines"], [])
            self.assertIn("Timed out", report["error"])

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


class AwsCommandTests(unittest.TestCase):
    @patch("harness.subprocess.run")
    def test_command_uses_json_region_and_timeout(self, run):
        """Inspect the built AWS command without running it; check output and time limits."""
        run.return_value.stdout = '{"Contents": []}'
        self.assertEqual(harness.aws_s3("list-objects-v2", timeout=4), {"Contents": []})
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["aws", "s3api", "list-objects-v2"])
        self.assertIn("eu-west-2", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("harness.subprocess.run")
    def test_socket_read_timeout_matches_operation_budget(self, run):
        run.return_value.stdout = '{}'
        for timeout, read_timeout in [(4, "4"), (60, "60"), (0.5, "1")]:
            with self.subTest(timeout=timeout):
                harness.aws_cli("lambda", "invoke", timeout=timeout)
                command = run.call_args.args[0]
                self.assertEqual(
                    command[command.index("--cli-read-timeout") + 1],
                    read_timeout,
                )
                self.assertEqual(run.call_args.kwargs["timeout"], timeout)

    @patch("harness.subprocess.run")
    def test_failed_commands_are_not_treated_as_missing_objects(self, run):
        """Check CLI errors, timeouts, and a missing AWS executable all fail clearly.

        These problems must not look like a successful search that found no file.
        """
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