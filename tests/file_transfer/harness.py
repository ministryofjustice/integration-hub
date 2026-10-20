import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


INCOMING_BUCKET = "integration-hub-file-transfer-test-incoming"
DESTINATION_BUCKET = "integration-hub-file-transfer-test-test-harness"
INCOMING_PREFIX = "test-harness/push-to-s3/"
DESTINATION_PREFIX = "push-to-s3/"
REGION = "eu-west-2"


def aws_s3(operation, *arguments, timeout=30):
    """Run an S3 API command with a time limit and return its parsed JSON response.

    Use the AWS CLI's current credentials. Report command failures without
    including potentially sensitive CLI output in the error message.
    """
    try:
        result = subprocess.run(
            [
                "aws", "s3api", operation, *arguments,
                "--region", REGION,
                "--output", "json",
                "--no-cli-pager",
                "--cli-connect-timeout", "10",
                "--cli-read-timeout", "10",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"AWS {operation} failed or timed out.") from error
    return json.loads(result.stdout)


def run_delivery_test(test_name, scenarios_for_run, verify_metadata=False,
                      timeout_seconds=300, poll_seconds=5):
    """Upload fixtures and verify exact-key delivery within one time limit."""
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Timeout and polling interval must be positive.")

    run_token = uuid.uuid4().hex
    scenarios = scenarios_for_run(run_token)
    print(f"File-transfer test: {test_name}.", flush=True)
    print("Environment: test | Route: direct upload to S3", flush=True)
    print(f"Test run ID: {run_token}", flush=True)
    print(f"Scenarios: {len(scenarios)} | Overall timeout: {timeout_seconds:g} seconds", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-test-") as directory:
        deadline = time.monotonic() + timeout_seconds
        for index, (scenario, filename, fixture) in enumerate(scenarios, start=1):
            relative_key = f"{run_token}/{filename}"
            incoming_key = INCOMING_PREFIX + relative_key
            destination_key = DESTINATION_PREFIX + relative_key
            source = Path(directory) / f"source-{index}"
            delivered = Path(directory) / f"delivered-{index}"
            source.write_bytes(fixture)
            print(f"[{index}/{len(scenarios)}] Uploading {scenario} ({len(fixture)} bytes).", flush=True)
            print(f"Source: s3://{INCOMING_BUCKET}/{incoming_key}", flush=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out before uploading {scenario}.")
            upload_arguments = [
                "--bucket", INCOMING_BUCKET,
                "--key", incoming_key,
                "--body", str(source),
            ]
            if verify_metadata:
                upload_arguments.extend([
                    "--content-type", "application/octet-stream",
                    "--metadata", json.dumps({"file-transfer-test-run-id": run_token}),
                ])
            aws_s3(
                "put-object", *upload_arguments,
                timeout=min(30, remaining),
            )
            print(f"Waiting for exact destination key: s3://{DESTINATION_BUCKET}/{destination_key}", flush=True)

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for {scenario} delivery.")
                listing = aws_s3(
                    "list-objects-v2", "--bucket", DESTINATION_BUCKET,
                    "--prefix", destination_key, timeout=min(30, remaining),
                )
                if any(item["Key"] == destination_key for item in listing.get("Contents", [])):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out before downloading {scenario}.")
                    aws_s3(
                        "get-object", "--bucket", DESTINATION_BUCKET,
                        "--key", destination_key, str(delivered),
                        timeout=min(30, remaining),
                    )
                    if delivered.read_bytes() != fixture:
                        raise RuntimeError(f"Delivered contents do not match the {scenario} fixture.")
                    if verify_metadata:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(f"Timed out before checking {scenario} metadata and encryption.")
                        head = aws_s3(
                            "head-object", "--bucket", DESTINATION_BUCKET,
                            "--key", destination_key, timeout=min(30, remaining),
                        )
                        if head.get("ContentType") != "application/octet-stream":
                            raise RuntimeError(f"Delivered {scenario} has unexpected content type.")
                        metadata = head.get("Metadata", {})
                        if metadata.get("file-transfer-test-run-id") != run_token:
                            raise RuntimeError(f"Delivered {scenario} metadata was not preserved.")
                        if (
                            head.get("ServerSideEncryption") != "aws:kms"
                            or not head.get("SSEKMSKeyId")
                        ):
                            raise RuntimeError(f"Delivered {scenario} is not encrypted with KMS.")
                        print(f"PASS: {scenario} metadata and KMS encryption verified.", flush=True)
                    print(f"PASS: {scenario} delivered unchanged ({len(fixture)} bytes).", flush=True)
                    break
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(poll_seconds, remaining))

    print(f"PASS: all {len(scenarios)} file-transfer scenarios completed.", flush=True)


def run_cli(test_name, scenarios_for_run, verify_metadata=False):
    try:
        run_delivery_test(test_name, scenarios_for_run, verify_metadata=verify_metadata)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0