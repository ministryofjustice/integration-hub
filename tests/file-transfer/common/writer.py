import json
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common.aws import CLEAN_BUCKET, INCOMING_BUCKET, REGION, aws_cli, aws_s3
from common.delivery import list_keys, run_delivery_test

SEED_FILE_NAME = "duplicate-idempotency-seed.bin"


def build_action_request(
    account_id, source_object, secret_arn, secret_version_id, action_name,
    requested_at=None, notifications=(),
):
    """Build a request telling a delivery component which exact file and configuration to use.

    Each call creates a new action ID. To test duplicate handling, build once
    and reuse the returned request, including its action ID, for both calls.
    The configuration reference identifies stored instructions without their contents.
    """
    action_execution_id = str(uuid.uuid4())
    requested_at = requested_at or datetime.now(timezone.utc)
    timestamp = requested_at.isoformat().replace("+00:00", "Z")
    return {
        "version": "0",
        "id": str(uuid.uuid4()),
        "detail-type": "FileActionExecutionRequested.v1",
        "source": "uk.gov.justice.service.managed-file-transfer",
        "account": account_id,
        "time": timestamp,
        "region": REGION,
        "detail": {
            "metadata": {
                "correlationId": str(uuid.uuid4()),
                "causationId": str(uuid.uuid4()),
                "idempotencyKey": f"action-request:{action_execution_id}",
            },
            "data": {
                "fileId": str(uuid.uuid4()),
                "object": dict(source_object),
                "action": {"name": action_name},
                "actionExecutionId": action_execution_id,
                "requestedAt": timestamp,
                "notifications": list(notifications),
                "configurationReference": {
                    "secretArn": secret_arn,
                    "secretVersionId": secret_version_id,
                },
            },
        },
    }


def current_clean_version(object_key, deadline):
    """Wait for the file in clean storage and return its exact saved version.

    Match the full S3 key, not just a similar filename. Writers need the
    version ID so they copy this particular saved file.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out finding the clean source version.")
        result = aws_s3(
            "list-object-versions", "--bucket", CLEAN_BUCKET,
            "--prefix", object_key, timeout=min(30, remaining),
        )
        for version in result.get("Versions", []):
            if version.get("Key") == object_key and version.get("IsLatest"):
                return {
                    "bucket": CLEAN_BUCKET,
                    "key": object_key,
                    "versionId": version["VersionId"],
                    "sizeBytes": version["Size"],
                }
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(2, remaining))


def verify_source_retained(source_object):
    """Check the exact clean version still exists after delivery.

    Delivery copies the clean file and keeps it; only the lifecycle rule may expire it.
    """
    aws_s3(
        "head-object", "--bucket", source_object["bucket"],
        "--key", source_object["key"], "--version-id", source_object["versionId"],
    )
    print("PASS: the exact clean source version was retained.", flush=True)


def head_destination_version(route, object_key):
    """Read the destination's current version without downloading the contents.

    We compare this before and after writer calls to detect another copy. A
    versioned bucket reports its version ID. Otherwise fall back to the ETag and
    last-modified time, which S3 reports to the nearest second.
    """
    response = aws_s3(
        "head-object", "--bucket", route.destination_bucket, "--key", object_key
    )
    version_id = response.get("VersionId")
    if version_id:
        return f"version:{version_id}"
    etag, modified = response.get("ETag"), response.get("LastModified")
    if not etag or not modified:
        raise RuntimeError("Destination object has no version ID, ETag and modified time.")
    return f"etag:{etag}@{modified}"


def invoke_lambda_with_sqs_record(function_name, body, queue_arn, response_path, expect_failure=False):
    """Call a deployed SQS-triggered function directly and return its response and message ID.

    The input looks like an SQS delivery because that is what the function expects,
    but no message is sent to SQS. This does not test queue retries or redelivery.
    A processing error is a failure unless expect_failure is set, in which case
    the function must report exactly this message as failed.
    """
    # Each call has a new message ID, but the delivery action in body stays the same.
    message_id = str(uuid.uuid4())
    record = {
        "messageId": message_id,
        "receiptHandle": "file-transfer-test",
        "body": json.dumps(body, separators=(",", ":")),
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
        "md5OfBody": "",
        "eventSource": "aws:sqs",
        "eventSourceARN": queue_arn,
        "awsRegion": REGION,
    }
    response = aws_cli(
        "lambda", "invoke",
        "--function-name", function_name,
        "--invocation-type", "RequestResponse",
        "--cli-binary-format", "raw-in-base64-out",
        "--payload", json.dumps({"Records": [record]}, separators=(",", ":")),
        str(response_path),
        timeout=60,
    )
    if response.get("FunctionError"):
        raise RuntimeError(f"{function_name} invocation failed: {response['FunctionError']}.")
    # Lambda writes its response to this local file; an empty failure list means success.
    payload = json.loads(Path(response_path).read_text())
    failures = payload.get("batchItemFailures")
    if expect_failure:
        if failures != [{"itemIdentifier": message_id}]:
            raise RuntimeError("The function did not reject the request as expected.")
    elif failures != []:
        raise RuntimeError("The function did not acknowledge the test request.")
    return payload, message_id


def invoke_writer(route, event, queue_arn, response_path, expect_failure=False):
    """Call the route's deployed writer with an SQS-shaped event."""
    return invoke_lambda_with_sqs_record(
        route.writer_function_name, event, queue_arn, response_path, expect_failure
    )


def dispatch_secret_reference(secret_name):
    """Return the dispatch secret ARN and current version ID, without its contents.

    DescribeSecret gives us the configuration ID and versions, not secret contents.
    """
    secret = aws_cli("secretsmanager", "describe-secret", "--secret-id", secret_name)
    secret_version_id = next(
        (
            version_id
            for version_id, stages in secret.get("VersionIdsToStages", {}).items()
            if "AWSCURRENT" in stages
        ),
        None,
    )
    if not secret_version_id:
        raise RuntimeError("Dispatch secret has no AWSCURRENT version.")
    return secret["ARN"], secret_version_id


def account_id():
    account = aws_cli("sts", "get-caller-identity").get("Account")
    if not account:
        raise RuntimeError("Could not determine the AWS account ID.")
    return account


def writer_queue_arn(route):
    """Name the writer's queue as the source of a direct invocation."""
    account = account_id()
    return account, f"arn:aws:sqs:{REGION}:{account}:{route.writer_queue_name}"


def run_duplicate_action_test(route, timeout_seconds=300):
    """Check that repeating one delivery action does not create a second copy.

    Normal delivery creates version A. A new action must create version B.
    Repeating that same action must leave B unchanged, rather than create C.
    The clean source version must still exist afterwards.
    """
    run_token = uuid.uuid4().hex

    def seed_scenarios(token):
        """Create the small starting file that both writer calls will refer to."""
        return [
            (
                "idempotency seed file",
                SEED_FILE_NAME,
                f"Synthetic L3 idempotency source: {token}\n".encode(),
            )
        ]

    # Use the normal route first so a real scanned file exists in the clean bucket.
    run_delivery_test(
        route, "L3 idempotency seed", seed_scenarios,
        timeout_seconds=min(180, timeout_seconds), poll_seconds=3, run_token=run_token,
    )

    deadline = time.monotonic() + timeout_seconds
    source_key = f"{route.incoming_prefix}{run_token}/{SEED_FILE_NAME}"
    source_object = current_clean_version(source_key, deadline)
    secret_arn, secret_version_id = dispatch_secret_reference(route.dispatch_secret_name)
    account, queue_arn = writer_queue_arn(route)
    # Build once: building again would create a different action, not a duplicate.
    event = build_action_request(
        account, source_object, secret_arn, secret_version_id, action_name=route.name,
    )
    destination_key = f"{route.destination_prefix}{run_token}/{SEED_FILE_NAME}"
    initial_version = head_destination_version(route, destination_key)
    if initial_version.startswith("etag:"):
        # Without version IDs, a rewrite within the same second would look unchanged.
        time.sleep(2)

    with tempfile.TemporaryDirectory(prefix="file-transfer-idempotency-") as directory:
        response_path = Path(directory) / "writer-response.json"
        # The first new action must really copy; otherwise the duplicate check is meaningless.
        invoke_writer(route, event, queue_arn, response_path)
        copied_version = head_destination_version(route, destination_key)
        if copied_version == initial_version:
            raise RuntimeError("First action request did not create a destination version.")
        if copied_version.startswith("etag:"):
            time.sleep(2)

        # Repeating the action should be acknowledged without writing another version.
        invoke_writer(route, event, queue_arn, response_path)
        duplicate_version = head_destination_version(route, destination_key)
        if duplicate_version != copied_version:
            raise RuntimeError("Duplicate action request created another destination version.")

    print("PASS: duplicate action request did not copy the file twice.", flush=True)
    verify_source_retained(source_object)


def run_rejected_request_test(route, timeout_seconds=120):
    """Check the writer fails closed when a request names an unauthorised source.

    The writer must reject the request before it reads the dispatch secret or
    touches the destination, and report it as a failed record. Nothing may be
    delivered. This deliberately increments the writer's failed-record metric.
    """
    run_token = uuid.uuid4().hex
    # TODO: also reject a lookalike prefix, a "../" key and another transfer identity.
    # Needs the harness role and test dispatch entries to cover a second prefix first.
    cases = [
        (
            "source key outside the authorised prefix",
            {"bucket": CLEAN_BUCKET, "key": f"test-harness/unauthorised/{run_token}/file.bin"},
        ),
        (
            "source bucket that is not the clean bucket",
            {"bucket": INCOMING_BUCKET, "key": f"{route.incoming_prefix}{run_token}/file.bin"},
        ),
    ]
    deadline = time.monotonic() + timeout_seconds
    secret_arn, secret_version_id = dispatch_secret_reference(route.dispatch_secret_name)
    account, queue_arn = writer_queue_arn(route)
    print("File-transfer test: L3 unauthorised request is rejected.", flush=True)
    print(f"Environment: test | Route: {route.label}", flush=True)
    print(f"Test run ID: {run_token}", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-rejection-") as directory:
        response_path = Path(directory) / "writer-response.json"
        for description, source in cases:
            event = build_action_request(
                account,
                {**source, "versionId": "unauthorised-test-version", "sizeBytes": 1},
                secret_arn, secret_version_id, action_name=route.name,
            )
            invoke_writer(route, event, queue_arn, response_path, expect_failure=True)
            print(f"PASS: writer rejected a {description}.", flush=True)

        if list_keys(route, route.run_prefix(run_token), deadline,
                     "Timed out before checking that nothing was delivered."):
            raise RuntimeError("An unauthorised request delivered a file.")
    print("PASS: rejected requests delivered nothing.", flush=True)
