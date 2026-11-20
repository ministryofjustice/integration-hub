"""Access-control checks for pull-from-presigned-url.

The notifier keeps a private copy of each clean file at
<recipient>/<pickup id>/<filename>. Only people in an authorised group may
download it, through the web portal. These checks show the copy is not reachable
by anyone else.

Automated here:
  1. the access grants on the recipient's folder are read-only and go only to the
     expected groups;
  2. an anonymous request for the object is refused (403);
  3. this test's own AWS role, which is in no authorised group, cannot read it.

Not automated, because they need a real sign-in to the portal: an authorised group
member can download the file, and a signed-in user outside the group cannot. The
portal is a single-page app that serves the same page to everyone and prompts for
sign-in in the browser, so an anonymous HTTP request says nothing about access.
"""
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

from common.aws import INCOMING_BUCKET, REGION, aws_cli, aws_cli_failure
from common.delivery import upload_to_incoming, wait_for_delivery
from common.levels import run_checks
from common.routes import PULL_FROM_PRESIGNED_URL, select_route

MAX_BODY_BYTES = 1_000_000
AUTHORISED_GROUPS_VARIABLE = "FILE_TRANSFER_PRESIGNED_AUTHORISED_GROUP_IDS"


def expected_group_ids(environ=None):
    """Return MP's expected test group IDs, with an optional local override."""
    environ = os.environ if environ is None else environ
    configured = environ.get(AUTHORISED_GROUPS_VARIABLE, "1622e2b4-9011-7000-b2fc-87228b7308cd")
    groups = {item.strip() for item in configured.split(",") if item.strip()}
    if not groups:
        raise ValueError(f"Set {AUTHORISED_GROUPS_VARIABLE} to the comma-separated authorised group IDs.")
    return groups


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments, **keywords):
        return None


def anonymous_get(url, timeout=20):
    """Request url with no credentials and without following redirects.

    Return the status, headers and the start of the body. A network failure raises,
    because an unreachable endpoint proves nothing about access.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "file-transfer-access-test"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(MAX_BODY_BYTES)
    except (urllib.error.URLError, OSError) as error:
        raise RuntimeError(f"Could not reach {urlparse(url).netloc}.") from error


def object_url(bucket, key):
    return f"https://{bucket}.s3.{REGION}.amazonaws.com/{quote(key, safe='/')}"


def verify_anonymous_object_refused(bucket, key, content):
    """An unauthenticated request must get 403, not a file, an error page or a redirect.

    A 404 is not accepted: it would also pass if the object were missing.
    """
    status, _, body = anonymous_get(object_url(bucket, key))
    if content in body:
        raise RuntimeError("An anonymous request returned the pickup file's contents.")
    if status != 403:
        raise RuntimeError(f"An anonymous object request returned {status}, not 403.")
    print("PASS: an anonymous request for the pickup object was refused.", flush=True)


def list_access_grants():
    """Return every grant in the account's S3 Access Grants instance."""
    account_id = aws_cli("sts", "get-caller-identity")["Account"]
    grants, token = [], None
    while True:
        extra = ["--next-token", token] if token else []
        page = aws_cli("s3control", "list-access-grants", "--account-id", account_id, "--no-paginate", *extra)
        grants.extend(page.get("AccessGrantsList", []))
        token = page.get("NextToken")
        if not token:
            return grants


def grant_covers(scope, folder):
    """Whether a grant scope gives access to anything under folder (an s3:// URI ending '/')."""
    if scope.endswith("*"):
        return folder.startswith(scope[:-1]) or scope[:-1].startswith(folder)
    return scope.startswith(folder)


def verify_grants_are_read_only_for_expected_groups(bucket, recipient, expected_groups, grants):
    """Every grant reaching the recipient's folder must be READ for an expected group.

    A grant that is wider than the folder counts, because it reaches the folder too.
    Finding no grant at all fails, so a missing configuration cannot pass.
    """
    folder = f"s3://{bucket}/{recipient}/"
    relevant = [grant for grant in grants if grant_covers(grant.get("GrantScope", ""), folder)]
    if not relevant:
        raise RuntimeError("No access grant covers the recipient's pickup folder.")
    not_read = [grant["GrantScope"] for grant in relevant if grant.get("Permission") != "READ"]
    if not_read:
        raise RuntimeError(f"{len(not_read)} grant(s) on the pickup folder give more than READ.")
    not_groups = [grant["GrantScope"] for grant in relevant if grant["Grantee"].get("GranteeType") != "DIRECTORY_GROUP"]
    if not_groups:
        raise RuntimeError(f"{len(not_groups)} grant(s) on the pickup folder are not for directory groups.")
    granted = {grant["Grantee"]["GranteeIdentifier"] for grant in relevant}
    if granted != expected_groups:
        raise RuntimeError(
            f"The pickup folder's grantees differ from the expected groups: "
            f"unexpected {sorted(granted - expected_groups)}, missing {sorted(expected_groups - granted)}."
        )
    print(f"PASS: {len(relevant)} grant(s) on the pickup folder are READ-only for the expected groups.", flush=True)


def verify_harness_cannot_read(bucket, key):
    """This role is in no authorised group, so reading the pickup copy must be denied.

    It can list the bucket to find the key, so S3 reports AccessDenied for the read
    rather than a missing key. Any other failure is not evidence of a refusal.
    """
    with tempfile.TemporaryDirectory(prefix="file-transfer-access-") as directory:
        error = aws_cli_failure(
            "s3api", "get-object", "--bucket", bucket, "--key", key, str(Path(directory) / "out")
        )
    if error is None:
        raise RuntimeError("The test role could read the pickup copy; it should be denied.")
    if "AccessDenied" not in error:
        raise RuntimeError("Reading the pickup copy failed, but not with AccessDenied.")
    print("PASS: the test role, outside any authorised group, was denied the pickup copy.", flush=True)


def run_access_control_test(route, timeout_seconds=300, poll_seconds=3):
    """Deliver one unique file, then check nobody outside an authorised group can reach it.

    Failing to find the pickup copy in time fails the test, so it can never pass
    because there was nothing to protect.
    """
    run_token = uuid.uuid4().hex
    name = f"{run_token}-access.bin"
    relative_key = f"{run_token}/{name}"
    content = f"Synthetic access-control fixture: {run_token}\n".encode()
    print("File-transfer test: pickup access control.", flush=True)
    print(f"Environment: test | Route: {route.label}", flush=True)
    print(f"Test run ID: {run_token}", flush=True)
    deadline = time.monotonic() + timeout_seconds
    verify_grants_are_read_only_for_expected_groups(
        route.destination_bucket, route.recipient_id, expected_group_ids(), list_access_grants()
    )
    with tempfile.TemporaryDirectory(prefix="file-transfer-access-source-") as directory:
        source = Path(directory) / "source"
        source.write_bytes(content)
        print(f"Source: s3://{INCOMING_BUCKET}/{route.incoming_prefix}{relative_key}", flush=True)
        upload_to_incoming(route, relative_key, source, deadline)
    print("Waiting for the pickup copy.", flush=True)
    key = wait_for_delivery(route, relative_key, deadline, poll_seconds, delivered_name=name)
    verify_anonymous_object_refused(route.destination_bucket, key, content)
    verify_harness_cannot_read(route.destination_bucket, key)


def main():
    return run_checks(lambda: run_access_control_test(select_route(PULL_FROM_PRESIGNED_URL)))


if __name__ == "__main__":
    sys.exit(main())
