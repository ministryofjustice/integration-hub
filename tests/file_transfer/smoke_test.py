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


def run_smoke_test(timeout_seconds=300, poll_seconds=5):
    """Upload a unique fixture and verify that the service delivers identical bytes.

    After upload, poll for the exact destination key within timeout_seconds.
    Raise an error for failed AWS calls, missing delivery or changed contents.
    Remove local temporary files; leave AWS object cleanup to retention rules.
    """
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Timeout and polling interval must be positive.")

    run_token = uuid.uuid4().hex
    relative_key = f"{run_token}/payload.txt"
    incoming_key = INCOMING_PREFIX + relative_key
    destination_key = DESTINATION_PREFIX + relative_key
    fixture = f"Integration Hub file-transfer smoke test: {run_token}\n".encode()
    print(f"Run token: {run_token}", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-smoke-") as directory:
        source = Path(directory) / "source.txt"
        delivered = Path(directory) / "delivered.txt"
        source.write_bytes(fixture)
        aws_s3(
            "put-object", "--bucket", INCOMING_BUCKET,
            "--key", incoming_key, "--body", str(source),
        )
        print(f"Uploaded {incoming_key}; waiting for {destination_key}", flush=True)
        deadline = time.monotonic() + timeout_seconds

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for the exact delivered object.")
            listing = aws_s3(
                "list-objects-v2", "--bucket", DESTINATION_BUCKET,
                "--prefix", destination_key, timeout=min(30, remaining),
            )
            if any(item["Key"] == destination_key for item in listing.get("Contents", [])):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out before downloading the delivered object.")
                aws_s3(
                    "get-object", "--bucket", DESTINATION_BUCKET,
                    "--key", destination_key, str(delivered),
                    timeout=min(30, remaining),
                )
                if delivered.read_bytes() != fixture:
                    raise RuntimeError("Delivered contents do not match the uploaded fixture.")
                print("PASS: delivered contents match the uploaded fixture.", flush=True)
                return
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_seconds, remaining))


if __name__ == "__main__":
    try:
        run_smoke_test()
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)