import json
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness import (
    CLEAN_BUCKET,
    DESTINATION_BUCKET,
    DESTINATION_PREFIX,
    INCOMING_PREFIX,
    REGION,
    aws_cli,
    aws_s3,
    run_concurrent_delivery_test,
    run_delivery_test,
)


DISPATCH_SECRET_NAME = (
    "integration-hub-file-transfer/file-dispatch/test-harness/push-to-s3/"
)
WRITER_FUNCTION_NAME = "integration-hub-file-transfer-push-to-s3"
WRITER_QUEUE_NAME = "integration-hub-file-transfer-push-to-s3"
SEED_FILE_NAME = "duplicate-idempotency-seed.bin"


def build_duplicate_action_request(
    account_id, source_object, secret_arn, secret_version_id, requested_at=None
):
    """Build a request telling the writer which exact file and configuration to use.

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
                "action": {"name": "push-to-s3"},
                "actionExecutionId": action_execution_id,
                "requestedAt": timestamp,
                "notifications": [],
                "configurationReference": {
                    "secretArn": secret_arn,
                    "secretVersionId": secret_version_id,
                },
            },
        },
    }


def _current_clean_version(object_key, deadline):
    """Wait for the seed file in clean storage and return its exact saved version.

    Match the full S3 key, not just a similar filename. The writer needs the
    version ID so it copies this particular saved file.
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


def _head_destination_version(object_key):
    """Read the destination's current version ID without downloading the contents.

    We compare this before and after writer calls to detect another copy.
    """
    response = aws_s3(
        "head-object", "--bucket", DESTINATION_BUCKET, "--key", object_key
    )
    version_id = response.get("VersionId")
    if not version_id:
        raise RuntimeError("Destination bucket did not return a version ID.")
    return version_id


def _invoke_writer(event, queue_arn, response_path):
    """Call the deployed writer directly and fail if it reports a processing error.

    The input looks like an SQS delivery because that is what the writer expects,
    but no message is sent to SQS. This does not test queue retries or redelivery.
    """
    # Each call has a new message ID, but the delivery action in event stays the same.
    record = {
        "messageId": str(uuid.uuid4()),
        "receiptHandle": "l3-idempotency-test",
        "body": json.dumps(event, separators=(",", ":")),
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
        "md5OfBody": "",
        "eventSource": "aws:sqs",
        "eventSourceARN": queue_arn,
        "awsRegion": REGION,
    }
    response = aws_cli(
        "lambda", "invoke",
        "--function-name", WRITER_FUNCTION_NAME,
        "--invocation-type", "RequestResponse",
        "--cli-binary-format", "raw-in-base64-out",
        "--payload", json.dumps({"Records": [record]}, separators=(",", ":")),
        str(response_path),
        timeout=60,
    )
    if response.get("FunctionError"):
        raise RuntimeError(f"Writer invocation failed: {response['FunctionError']}.")
    # Lambda writes its response to this local file; an empty failure list means success.
    payload = json.loads(response_path.read_text())
    if payload.get("batchItemFailures") != []:
        raise RuntimeError("Writer did not acknowledge the idempotency test request.")


def run_duplicate_action_test(timeout_seconds=300):
    """Check that repeating one delivery action does not create a second copy.

    Normal delivery creates version A. A new action must create version B.
    Repeating that same action must leave B unchanged, rather than create C.
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
        "L3 idempotency seed",
        seed_scenarios,
        timeout_seconds=min(180, timeout_seconds),
        poll_seconds=3,
        run_token=run_token,
    )

    deadline = time.monotonic() + timeout_seconds
    source_key = f"{INCOMING_PREFIX}{run_token}/{SEED_FILE_NAME}"
    source_object = _current_clean_version(source_key, deadline)
    # DescribeSecret gives us the configuration ID and versions, not secret contents.
    secret = aws_cli(
        "secretsmanager", "describe-secret", "--secret-id", DISPATCH_SECRET_NAME
    )
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
    account_id = aws_cli("sts", "get-caller-identity").get("Account")
    if not account_id:
        raise RuntimeError("Could not determine the AWS account ID.")
    queue_arn = f"arn:aws:sqs:{REGION}:{account_id}:{WRITER_QUEUE_NAME}"
    # Build once: building again would create a different action, not a duplicate.
    event = build_duplicate_action_request(
        account_id, source_object, secret["ARN"], secret_version_id
    )
    destination_key = f"{DESTINATION_PREFIX}{run_token}/{SEED_FILE_NAME}"
    initial_version = _head_destination_version(destination_key)

    with tempfile.TemporaryDirectory(prefix="file-transfer-idempotency-") as directory:
        response_path = Path(directory) / "writer-response.json"
        # The first new action must really copy; otherwise the duplicate check is meaningless.
        _invoke_writer(event, queue_arn, response_path)
        copied_version = _head_destination_version(destination_key)
        if copied_version == initial_version:
            raise RuntimeError("First action request did not create a destination version.")

        # Repeating the action should be acknowledged without writing another version.
        _invoke_writer(event, queue_arn, response_path)
        duplicate_version = _head_destination_version(destination_key)
        if duplicate_version != copied_version:
            raise RuntimeError("Duplicate action request created another destination version.")

    print("PASS: duplicate action request did not copy the file twice.", flush=True)


def run_l3_cli():
    """Run concurrent delivery, then duplicate handling; return 1 if either fails."""
    try:
        run_concurrent_delivery_test(timeout_seconds=120, poll_seconds=2)
        run_duplicate_action_test(timeout_seconds=180)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_l3_cli())