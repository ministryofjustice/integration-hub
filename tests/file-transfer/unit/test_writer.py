import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from common import aws
from common import writer as resilience_test
from common.writer import build_duplicate_action_request
from unit.support import HarnessTestCase


class DuplicateActionTests(HarnessTestCase):
    def test_duplicate_action_request_has_stable_idempotency_key(self):
        """Check that the request's duplicate-detection key uses its action ID.

        Also verify that the source file and configuration version are included.
        This builds a request locally; it does not call the deployed writer.
        """
        source_object = {
            "bucket": aws.CLEAN_BUCKET,
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
            "bucket": aws.CLEAN_BUCKET,
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


if __name__ == "__main__":
    unittest.main()
