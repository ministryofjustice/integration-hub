import io
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from pull_from_presigned_url import access
from unit.support import presigned

KEY = f"test-recipient/{'a' * 64}/run-access.bin"
BUCKET = "pickup-test-bucket"
CONTENT = b"Synthetic access-control fixture: run\n"


def quiet(function, *args):
    with redirect_stdout(io.StringIO()):
        return function(*args)


class AnonymousObjectTests(unittest.TestCase):
    def check(self, status, body=b""):
        with patch("pull_from_presigned_url.access.anonymous_get", return_value=(status, {}, body)):
            quiet(access.verify_anonymous_object_refused, BUCKET, KEY, CONTENT)

    def test_only_a_403_without_the_contents_passes(self):
        self.check(403, b"<Error>AccessDenied</Error>")

    def test_a_404_is_not_a_refusal(self):
        """A missing object would also give 404, so it proves nothing about access."""
        with self.assertRaisesRegex(RuntimeError, "404, not 403"):
            self.check(404)

    def test_a_redirect_or_success_fails(self):
        for status in (200, 302):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.check(status)

    def test_returned_contents_fail_whatever_the_status(self):
        with self.assertRaisesRegex(RuntimeError, "returned the pickup file's contents"):
            self.check(403, b"prefix " + CONTENT)

    def test_the_url_names_the_bucket_and_encodes_the_key(self):
        self.assertEqual(
            access.object_url("b", "r/a b/c(1).bin"),
            "https://b.s3.eu-west-2.amazonaws.com/r/a%20b/c%281%29.bin",
        )


class AnonymousRequestTests(unittest.TestCase):
    def test_an_unreachable_endpoint_is_an_error_not_a_refusal(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("down")
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "Could not reach"):
                access.anonymous_get("https://b.s3.eu-west-2.amazonaws.com/k")

    def test_an_http_error_status_is_returned_with_its_body(self):
        error = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, io.BytesIO(b"denied"))
        opener = MagicMock()
        opener.open.side_effect = error
        with patch("urllib.request.build_opener", return_value=opener):
            status, _, body = access.anonymous_get("https://x")
        self.assertEqual((status, body), (403, b"denied"))


class HarnessRoleTests(unittest.TestCase):
    def check(self, error):
        with patch("pull_from_presigned_url.access.aws_cli_failure", return_value=error):
            quiet(access.verify_harness_cannot_read, BUCKET, KEY)

    def test_access_denied_passes(self):
        self.check("An error occurred (AccessDenied) when calling the GetObject operation")

    def test_a_successful_read_fails(self):
        with self.assertRaisesRegex(RuntimeError, "could read"):
            self.check(None)

    def test_another_failure_is_not_a_refusal(self):
        """A missing key or a bad credential must not pass as 'access denied'."""
        for error in ("An error occurred (NoSuchKey)", "Unable to locate credentials"):
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, "not with AccessDenied"):
                self.check(error)


GROUPS = {"group-a"}


def grant(scope="s3://pickup-bucket/test-recipient/*", permission="READ", group="group-a", kind="DIRECTORY_GROUP"):
    return {"GrantScope": scope, "Permission": permission, "Grantee": {"GranteeType": kind, "GranteeIdentifier": group}}


class GrantTests(unittest.TestCase):
    def check(self, grants, expected=GROUPS):
        quiet(access.verify_grants_are_read_only_for_expected_groups, "pickup-bucket", "test-recipient", expected, grants)

    def test_a_read_grant_to_the_expected_group_passes(self):
        self.check([grant(), grant(scope="s3://pickup-bucket/other/*", group="someone-else")])

    def test_no_grant_at_all_fails(self):
        with self.assertRaisesRegex(RuntimeError, "No access grant"):
            self.check([grant(scope="s3://pickup-bucket/other/*")])

    def test_more_than_read_fails(self):
        for permission in ("READWRITE", "WRITE"):
            with self.subTest(permission=permission), self.assertRaisesRegex(RuntimeError, "more than READ"):
                self.check([grant(permission=permission)])

    def test_a_wider_scope_reaches_the_folder_too(self):
        with self.assertRaisesRegex(RuntimeError, "differ from the expected groups"):
            self.check([grant(), grant(scope="s3://pickup-bucket/*", group="everyone")])

    def test_only_directory_groups_are_allowed(self):
        with self.assertRaisesRegex(RuntimeError, "not for directory groups"):
            self.check([grant(kind="IAM", group="arn:aws:iam::1:role/x")])

    def test_unexpected_and_missing_groups_both_fail(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected \\['group-b'\\], missing \\['group-a'\\]"):
            self.check([grant(group="group-b")])


class ExpectedGroupTests(unittest.TestCase):
    def test_groups_are_read_from_the_environment(self):
        self.assertEqual(access.expected_group_ids({access.AUTHORISED_GROUPS_VARIABLE: " a, b ,"}), {"a", "b"})

    def test_a_missing_setting_is_an_error(self):
        with self.assertRaisesRegex(ValueError, access.AUTHORISED_GROUPS_VARIABLE):
            access.expected_group_ids({})


class AccessControlTestRun(unittest.TestCase):
    CHECKS = ("verify_anonymous_object_refused", "verify_harness_cannot_read")

    def run_test(self, delivery, grants_failure=None):
        """Run the whole test with AWS stubbed; return the checks' mocks."""
        mocks = {}
        for name in ("upload_to_incoming", "list_access_grants", *self.CHECKS):
            mocks[name] = patch(f"pull_from_presigned_url.access.{name}").start()
        mocks["verify_grants"] = patch(
            "pull_from_presigned_url.access.verify_grants_are_read_only_for_expected_groups",
            side_effect=grants_failure,
        ).start()
        patch("pull_from_presigned_url.access.expected_group_ids", return_value=GROUPS).start()
        patch("pull_from_presigned_url.access.wait_for_delivery", **delivery).start()
        self.addCleanup(patch.stopall)
        with redirect_stdout(io.StringIO()):
            try:
                access.run_access_control_test(presigned())
            finally:
                self.mocks = mocks

    def test_all_checks_run_against_the_delivered_key(self):
        self.run_test({"return_value": KEY})
        self.mocks["verify_grants"].assert_called_once()
        self.mocks["verify_anonymous_object_refused"].assert_called_once()
        self.assertEqual(self.mocks["verify_anonymous_object_refused"].call_args.args[:2], (BUCKET, KEY))
        self.mocks["verify_harness_cannot_read"].assert_called_once()

    def test_a_copy_that_never_arrives_fails_before_any_check_can_pass(self):
        with self.assertRaises(TimeoutError):
            self.run_test({"side_effect": TimeoutError("not delivered")})
        for name in self.CHECKS:
            self.mocks[name].assert_not_called()

    def test_wrong_grants_stop_the_test_before_anything_is_uploaded(self):
        with self.assertRaisesRegex(RuntimeError, "wider"):
            self.run_test({"return_value": KEY}, grants_failure=RuntimeError("wider"))
        self.mocks["upload_to_incoming"].assert_not_called()

    def test_main_exits_non_zero_when_a_check_fails(self):
        with patch("pull_from_presigned_url.access.run_access_control_test",
                   side_effect=RuntimeError("exposed")), \
                patch("pull_from_presigned_url.access.select_route", return_value=presigned()), \
                patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(access.main(), 1)


if __name__ == "__main__":
    unittest.main()
