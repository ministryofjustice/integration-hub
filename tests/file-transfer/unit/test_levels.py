import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from common import aws
from common import levels as performance_test
from unit.support import HarnessTestCase


class PerformanceTests(HarnessTestCase):
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


class AwsCommandTests(unittest.TestCase):
    @patch("common.aws.subprocess.run")
    def test_command_uses_json_region_and_timeout(self, run):
        """Inspect the built AWS command without running it; check output and time limits."""
        run.return_value.stdout = '{"Contents": []}'
        self.assertEqual(aws.aws_s3("list-objects-v2", timeout=4), {"Contents": []})
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["aws", "s3api", "list-objects-v2"])
        self.assertIn("eu-west-2", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("common.aws.subprocess.run")
    def test_socket_read_timeout_matches_operation_budget(self, run):
        run.return_value.stdout = '{}'
        for timeout, read_timeout in [(4, "4"), (60, "60"), (0.5, "1")]:
            with self.subTest(timeout=timeout):
                aws.aws_cli("lambda", "invoke", timeout=timeout)
                command = run.call_args.args[0]
                self.assertEqual(
                    command[command.index("--cli-read-timeout") + 1],
                    read_timeout,
                )
                self.assertEqual(run.call_args.kwargs["timeout"], timeout)

    @patch("common.aws.subprocess.run")
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
                    aws.aws_s3("list-objects-v2")


if __name__ == "__main__":
    unittest.main()
