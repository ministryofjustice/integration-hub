import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from push_to_s3 import resilience as s3_resilience
from push_to_s3_with_hosted_pickup import resilience as hosted_resilience
from unit.support import HOSTED_ENVIRONMENT


class ResilienceWiringTests(unittest.TestCase):
    def run_cli(self, module, environment, argv=()):
        patches = {
            "concurrent": patch("common.resilience.run_concurrent_delivery_test"),
            "overwrite": patch("common.resilience.run_overwrite_test"),
            "duplicate": patch("common.resilience.run_duplicate_action_test"),
            "rejection": patch("common.resilience.run_rejected_request_test"),
            "retention": patch("push_to_s3_with_hosted_pickup.resilience.verify_retention_lifecycle"),
        }
        with patch.dict("os.environ", environment, clear=True):
            mocks = {name: p.start() for name, p in patches.items()}
            try:
                with redirect_stdout(io.StringIO()):
                    result = module.main(list(argv))
            finally:
                patch.stopall()
        return result, mocks

    def test_rejection_checks_are_opt_in_for_writer_routes(self):
        for module, environment in (
            (s3_resilience, {}), (hosted_resilience, HOSTED_ENVIRONMENT),
        ):
            with self.subTest(module=module.__name__):
                result, mocks = self.run_cli(module, environment)
                self.assertEqual(result, 0)
                self.assertFalse(mocks["rejection"].called)
                result, mocks = self.run_cli(module, environment, ["--include-rejection-check"])
                self.assertTrue(mocks["rejection"].called)

    def test_a_failing_check_stops_the_run_with_a_failure_code(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("common.resilience.run_concurrent_delivery_test", side_effect=RuntimeError("boom")), \
                patch("common.resilience.run_duplicate_action_test") as duplicate, \
                redirect_stderr(io.StringIO()):
            self.assertEqual(s3_resilience.main([]), 1)
        duplicate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
