import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from common import writer
from common.aws import CLEAN_BUCKET, INCOMING_BUCKET
from common.checks import lifecycle_expiry_days, verify_retention_lifecycle
from unit.support import hosted, sftp

SOURCE = {"bucket": CLEAN_BUCKET, "key": "k", "versionId": "v1", "sizeBytes": 3}


class InvokeTests(unittest.TestCase):
    def invoke(self, response, payload, expect_failure=False):
        def fake_cli(service, operation, *arguments, timeout=30):
            Path(arguments[-1]).write_text(json.dumps(payload(arguments)))
            return response

        with patch("common.writer.aws_cli", side_effect=fake_cli) as cli:
            return writer.invoke_lambda_with_sqs_record(
                "fn", {"a": 1}, "arn:queue", self.path, expect_failure
            ), cli

    def setUp(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "response.json"

    def test_success_needs_an_empty_failure_list(self):
        self.invoke({}, lambda a: {"batchItemFailures": []})
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            self.invoke({}, lambda a: {"batchItemFailures": [{"itemIdentifier": "x"}]})

    def test_expected_failure_must_name_exactly_this_message(self):
        def payload(arguments):
            record = json.loads(arguments[arguments.index("--payload") + 1])["Records"][0]
            return {"batchItemFailures": [{"itemIdentifier": record["messageId"]}]}

        self.invoke({}, payload, expect_failure=True)
        with self.assertRaisesRegex(RuntimeError, "did not reject"):
            self.invoke({}, lambda a: {"batchItemFailures": [{"itemIdentifier": "other"}]}, expect_failure=True)
        with self.assertRaisesRegex(RuntimeError, "did not reject"):
            self.invoke({}, lambda a: {"batchItemFailures": []}, expect_failure=True)


class DuplicateActionTests(unittest.TestCase):
    def run_test(self, route, versions, retained=True):
        calls = []
        heads = iter(versions)
        patches = [
            patch("common.writer.run_delivery_test"),
            patch("common.writer.current_clean_version", return_value=SOURCE),
            patch("common.writer.dispatch_secret_reference", return_value=("arn:secret", "sv")),
            patch("common.writer.account_id", return_value="123456789012"),
            patch("common.writer.head_destination_version", side_effect=lambda *a: next(heads)),
            patch("common.writer.invoke_writer", side_effect=lambda *a, **k: calls.append(a[1])),
            patch("common.writer.verify_source_retained",
                  side_effect=None if retained else RuntimeError("gone")),
            patch("time.sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with redirect_stdout(io.StringIO()) as output:
            writer.run_duplicate_action_test(route, timeout_seconds=10)
        return calls, output.getvalue()

    def test_one_request_is_sent_twice_and_nothing_extra_is_written(self):
        for route in (sftp(), hosted()):
            with self.subTest(route=route.name):
                calls, output = self.run_test(route, ["version:A", "version:B", "version:B"])
                self.assertEqual(len(calls), 2)
                self.assertIs(calls[0], calls[1])
                self.assertEqual(calls[0]["detail"]["data"]["action"], {"name": route.name})
                self.assertIn("did not copy the file twice", output)
                patch.stopall()

    def test_a_second_copy_fails(self):
        with self.assertRaisesRegex(RuntimeError, "another destination version"):
            self.run_test(sftp(), ["version:A", "version:B", "version:C"])

    def test_a_first_request_that_copies_nothing_fails(self):
        with self.assertRaisesRegex(RuntimeError, "did not create"):
            self.run_test(sftp(), ["version:A", "version:A"])

    def test_a_lost_source_fails(self):
        with self.assertRaisesRegex(RuntimeError, "gone"):
            self.run_test(sftp(), ["version:A", "version:B", "version:B"], retained=False)


class SourceRetentionTests(unittest.TestCase):
    def verify(self, versions):
        with patch("common.writer.aws_s3", return_value={"Versions": versions}):
            with redirect_stdout(io.StringIO()):
                writer.verify_source_retained(SOURCE)

    def test_the_exact_version_must_still_be_listed(self):
        self.verify([{"Key": "k", "VersionId": "v1"}])
        with self.assertRaises(RuntimeError):
            self.verify([{"Key": "k", "VersionId": "v2"}, {"Key": "other", "VersionId": "v1"}])


class RejectionTests(unittest.TestCase):
    def test_both_bad_sources_are_rejected_and_nothing_is_listed_as_delivered(self):
        events = []
        with patch("common.writer.dispatch_secret_reference", return_value=("a", "v")), \
                patch("common.writer.account_id", return_value="1"), \
                patch("common.writer.invoke_writer", side_effect=lambda r, e, *a, **k: events.append((e, k))), \
                patch("common.writer.list_keys", return_value=set()), redirect_stdout(io.StringIO()):
            writer.run_rejected_request_test(sftp())
        self.assertEqual(len(events), 2)
        self.assertTrue(all(k["expect_failure"] for _, k in events))
        buckets = [e["detail"]["data"]["object"]["bucket"] for e, _ in events]
        self.assertEqual(buckets, [CLEAN_BUCKET, INCOMING_BUCKET])

    def test_a_delivery_after_rejection_fails(self):
        with patch("common.writer.dispatch_secret_reference", return_value=("a", "v")), \
                patch("common.writer.account_id", return_value="1"), \
                patch("common.writer.invoke_writer"), \
                patch("common.writer.list_keys", return_value={"x"}), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "delivered a file"):
                writer.run_rejected_request_test(sftp())


class LifecycleTests(unittest.TestCase):
    RULE = {"Status": "Enabled", "Filter": {"Prefix": "pickup/"}, "Expiration": {"Days": 7}}

    def test_only_enabled_untagged_rules_covering_the_prefix_count(self):
        rules = [
            self.RULE,
            {**self.RULE, "Status": "Disabled", "Expiration": {"Days": 1}},
            {**self.RULE, "Filter": {"Prefix": "pickup/", "Tag": {}}, "Expiration": {"Days": 2}},
            {**self.RULE, "Filter": {"Prefix": "other/"}, "Expiration": {"Days": 3}},
            {"Status": "Enabled", "Filter": {}, "Expiration": {"Days": 30}},
            {"Status": "Enabled", "Filter": {"Prefix": "pickup/"}, "NoncurrentVersionExpiration": {}},
        ]
        self.assertEqual(lifecycle_expiry_days(rules, "pickup/"), [7, 30])

    def test_retention_check_is_skipped_visibly_without_expected_days(self):
        with patch("common.checks.aws_s3") as aws, redirect_stdout(io.StringIO()) as output:
            verify_retention_lifecycle(hosted())
        aws.assert_not_called()
        self.assertIn("SKIPPED", output.getvalue())

    def test_retention_check_passes_or_fails_on_the_rules(self):
        route = hosted_with_retention(7)
        with patch("common.checks.aws_s3", return_value={"Rules": [self.RULE]}), redirect_stdout(io.StringIO()):
            verify_retention_lifecycle(route)
        with patch("common.checks.aws_s3", return_value={"Rules": []}):
            with self.assertRaisesRegex(RuntimeError, "No enabled lifecycle rule"):
                verify_retention_lifecycle(route)


def hosted_with_retention(days):
    from common.routes import hosted_pickup_route
    return hosted_pickup_route({
        "FILE_TRANSFER_HOSTED_PICKUP_BUCKET": "b",
        "FILE_TRANSFER_HOSTED_PICKUP_RETENTION_DAYS": str(days),
    })


if __name__ == "__main__":
    unittest.main()
