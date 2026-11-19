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
from unit.support import FakeS3TestCase, hosted, presigned


class PerformanceTests(FakeS3TestCase):
    """A one-second poll makes every file's observed delivery take one simulated second."""

    def setUp(self):
        super().setUp()
        patcher = patch("common.levels.POLL_SECONDS", 1)
        patcher.start()
        self.addCleanup(patcher.stop)

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
            self.assertIn("1/4", summary_path.read_text())

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
        for route_name in ("push-to-s3-with-hosted-pickup", "pull-from-presigned-url"):
            with self.subTest(route=route_name), patch.dict("os.environ", {}, clear=True), \
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
