import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from common import aws, levels
from common.fixtures import MIB, smoke_scenarios
from unit.support import FakeS3TestCase


class PerformanceTests(FakeS3TestCase):
    def test_completed_report_groups_samples_and_uses_total_verified_time_for_rates(self):
        durations = {
            "put-object": iter((4, 1, 2, 3)),
            "list-objects-v2": iter((1, 1, 1, 1)),
            "get-object": iter((5, 3, 5, 2)),
        }

        def timed(operation, *arguments, timeout=30):
            self.elapsed += next(durations[operation])
            return self.s3(operation, *arguments, timeout=timeout)

        with tempfile.TemporaryDirectory() as directory, \
                patch("common.delivery.aws_s3", side_effect=timed):
            report_path, summary_path = Path(directory) / "l4.json", Path(directory) / "summary.md"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = levels.run_performance_cli(
                    "push-to-s3", ["--report", str(report_path), "--summary", str(summary_path)]
                )
            report = json.loads(report_path.read_text())
            summary = summary_path.read_text()

        self.assertEqual((result, report["status"]), (0, "completed"))
        self.assertEqual(len(report["measurements"]), report["expected_files"])
        self.assertEqual(sum(sample["size_bytes"] for sample in report["measurements"]),
                         report["expected_bytes"])
        self.assertEqual(report["baselines"], [
            {
                "size_bytes": MIB,
                "sample_count": 3,
                "min_observed_delivery_seconds": 2,
                "median_observed_delivery_seconds": 3,
                "max_observed_delivery_seconds": 5,
                "effective_mib_per_second": 3 / 23,
            },
            {
                "size_bytes": 16 * MIB,
                "sample_count": 1,
                "min_observed_delivery_seconds": 4,
                "median_observed_delivery_seconds": 4,
                "max_observed_delivery_seconds": 4,
                "effective_mib_per_second": 16 / 6,
            },
        ])
        self.assertIn("| 1 | 3 | 2.000 | 3.000 | 5.000 | 0.130 |", summary)
        self.assertIn("| 16 | 1 | 4.000 | 4.000 | 4.000 | 2.667 |", summary)

    def test_a_failure_keeps_only_verified_samples(self):
        calls = {"puts": 0}
        original = self.s3

        def corrupt_after_first(operation, *arguments, timeout=30):
            if operation == "put-object":
                calls["puts"] += 1
                original.corrupt = calls["puts"] > 1
            return original(operation, *arguments, timeout=timeout)

        with tempfile.TemporaryDirectory() as directory, \
                patch("common.delivery.aws_s3", side_effect=corrupt_after_first):
            report_path, summary_path = Path(directory) / "l4.json", Path(directory) / "s.md"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = levels.run_performance_cli(
                    "push-to-s3", ["--report", str(report_path), "--summary", str(summary_path)]
                )
            report = json.loads(report_path.read_text())
            self.assertEqual(result, 1)
            self.assertEqual((report["status"], len(report["measurements"])), ("failed", 1))
            self.assertIn("do not match", report["error"])
            self.assertEqual(report["baselines"], [{
                "size_bytes": MIB,
                "sample_count": 1,
                "min_observed_delivery_seconds": 0,
                "median_observed_delivery_seconds": 0,
                "max_observed_delivery_seconds": 0,
                "effective_mib_per_second": None,
            }])
            self.assertIn("1/4", summary_path.read_text())
            self.assertIn("| 1 | 1 | 0.000 | 0.000 | 0.000 | n/a |", summary_path.read_text())

    def test_a_timeout_writes_a_failed_report(self):
        self.s3.never_deliver = True
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "l4.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = levels.run_performance_cli("push-to-s3", ["--report", str(report_path)])
            report = json.loads(report_path.read_text())
        self.assertEqual((result, report["status"], report["measurements"]), (1, "failed", []))
        self.assertIn("Timed out", report["error"])


class CliTests(unittest.TestCase):
    def test_configuration_errors_fail_with_a_clear_message_and_no_aws_calls(self):
        for route_name, environment in (
            ("push-to-s3-with-hosted-pickup", {"FILE_TRANSFER_HOSTED_PICKUP_RETENTION_DAYS": "-1"}),
            ("pull-from-presigned-url", {"FILE_TRANSFER_PRESIGNED_RECIPIENT_ID": "invalid/recipient"}),
        ):
            with self.subTest(route=route_name), patch.dict("os.environ", environment, clear=True), \
                    patch("common.delivery.aws_s3") as aws_call, \
                    redirect_stderr(io.StringIO()) as errors:
                result = levels.run_delivery_cli(route_name, "L1", smoke_scenarios)
            self.assertEqual(result, 1)
            self.assertIn("FAIL:", errors.getvalue())
            aws_call.assert_not_called()

class AwsCommandTests(unittest.TestCase):
    def test_failures_are_errors_not_a_missing_object(self):
        for error in (
            subprocess.CalledProcessError(254, "aws", stderr="secret detail"),
            subprocess.TimeoutExpired("aws", 1),
            FileNotFoundError(),
        ):
            with self.subTest(error=type(error).__name__), patch("common.aws.subprocess.run", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    aws.aws_s3("list-objects-v2")
            self.assertNotIn("secret detail", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
