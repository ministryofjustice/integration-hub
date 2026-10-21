import json
import math
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


INCOMING_BUCKET = "integration-hub-file-transfer-test-incoming"
CLEAN_BUCKET = "integration-hub-file-transfer-test-clean"
DESTINATION_BUCKET = "integration-hub-file-transfer-test-test-harness"
INCOMING_PREFIX = "test-harness/push-to-s3/"
DESTINATION_PREFIX = "push-to-s3/"
REGION = "eu-west-2"


def aws_cli(service, operation, *arguments, timeout=30):
    """Run an AWS CLI command with a time limit and return parsed JSON output.

    Use the AWS CLI's current credentials and avoid exposing command output in
    error messages.
    """
    try:
        result = subprocess.run(
            [
                "aws", service, operation, *arguments,
                "--region", REGION,
                "--output", "json",
                "--no-cli-pager",
                "--cli-connect-timeout", "10",
                "--cli-read-timeout", str(math.ceil(timeout)),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"AWS {operation} failed or timed out.") from error
    return json.loads(result.stdout)


def aws_s3(operation, *arguments, timeout=30):
    """Run an S3 command through the shared AWS command/error handling helper."""
    return aws_cli("s3api", operation, *arguments, timeout=timeout)


def run_delivery_test(test_name, scenarios_for_run, verify_metadata=False,
                      timeout_seconds=300, poll_seconds=5, run_token=None,
                      measurements=None):
    """Upload test files one at a time and check each arrives without changes.

    scenarios_for_run supplies the filenames and expected bytes. When requested,
    also check the content type, run-ID metadata, and presence of KMS encryption.
    Return the run ID so another check can find the same files later.
    If a measurements list is supplied, append timings only for verified files.
    """
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Timeout and polling interval must be positive.")

    # A unique S3 path prevents an older run's files from satisfying this test.
    run_token = run_token or uuid.uuid4().hex
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
            upload_started = time.monotonic()
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
                # S3 prefix searches can include similar names; only the exact key counts.
                if any(item["Key"] == destination_key for item in listing.get("Contents", [])):
                    delivery_observed = time.monotonic()
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
                    if measurements is not None:
                        # Observation includes upload and polling, not just service processing.
                        measurements.append({
                            "run_token": run_token,
                            "filename": filename,
                            "size_bytes": len(fixture),
                            "observed_delivery_seconds": delivery_observed - upload_started,
                            "verified_seconds": time.monotonic() - upload_started,
                        })
                    print(f"PASS: {scenario} delivered unchanged ({len(fixture)} bytes).", flush=True)
                    break
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(poll_seconds, remaining))

    print(f"PASS: all {len(scenarios)} file-transfer scenarios completed.", flush=True)
    return run_token


def run_concurrent_delivery_test(file_count=3, timeout_seconds=300, poll_seconds=5):
    """Upload several different files at once and check each arrives unchanged.

    Every file has its own expected contents and destination path, so a missing
    file or contents mixed up between files makes the test fail.
    """
    if file_count < 2:
        raise ValueError("Concurrent delivery needs at least two files.")
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Timeout and polling interval must be positive.")

    run_token = uuid.uuid4().hex
    scenarios = [
        (
            f"concurrent file {index}",
            f"concurrent-{index}.bin",
            f"{run_token}:{index}\n".encode() + bytes([index]) * 4096,
        )
        for index in range(1, file_count + 1)
    ]
    print("File-transfer test: L3 concurrent delivery.", flush=True)
    print("Environment: test | Route: direct upload to S3", flush=True)
    print(f"Test run ID: {run_token}", flush=True)
    print(f"Concurrent files: {file_count} | Overall timeout: {timeout_seconds:g} seconds", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-resilience-") as directory:
        deadline = time.monotonic() + timeout_seconds
        expected = {}
        uploads = []
        for index, (scenario, filename, fixture) in enumerate(scenarios, start=1):
            relative_key = f"{run_token}/{filename}"
            incoming_key = INCOMING_PREFIX + relative_key
            destination_key = DESTINATION_PREFIX + relative_key
            source = Path(directory) / f"source-{index}"
            source.write_bytes(fixture)
            expected[destination_key] = (scenario, fixture, Path(directory) / f"delivered-{index}")
            uploads.append((scenario, incoming_key, source))

        # Separate workers allow uploads to overlap instead of waiting for each other.
        with ThreadPoolExecutor(max_workers=file_count) as executor:
            futures = []
            for scenario, incoming_key, source in uploads:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out before uploading concurrent fixtures.")
                print(f"Uploading {scenario}: s3://{INCOMING_BUCKET}/{incoming_key}", flush=True)
                futures.append(
                    executor.submit(
                        aws_s3,
                        "put-object",
                        "--bucket", INCOMING_BUCKET,
                        "--key", incoming_key,
                        "--body", str(source),
                        timeout=min(30, remaining),
                    )
                )
            try:
                for future in as_completed(futures, timeout=max(deadline - time.monotonic(), 0)):
                    # Surface upload errors from the worker instead of ignoring them.
                    future.result()
            except TimeoutError as error:
                raise TimeoutError("Timed out uploading concurrent fixtures.") from error

        expected_keys = set(expected)
        prefix = f"{DESTINATION_PREFIX}{run_token}/"
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for concurrent file delivery.")
            listing = aws_s3(
                "list-objects-v2", "--bucket", DESTINATION_BUCKET,
                "--prefix", prefix, timeout=min(30, remaining),
            )
            delivered_keys = {
                item["Key"] for item in listing.get("Contents", [])
            }
            if expected_keys.issubset(delivered_keys):
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_seconds, remaining))

        for destination_key, (scenario, fixture, delivered) in expected.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out before downloading {scenario}.")
            aws_s3(
                "get-object", "--bucket", DESTINATION_BUCKET,
                "--key", destination_key, str(delivered),
                timeout=min(30, remaining),
            )
            if delivered.read_bytes() != fixture:
                raise RuntimeError(f"Concurrent delivery changed contents for {scenario}.")
            print(f"PASS: {scenario} arrived unchanged at {destination_key}.", flush=True)

    print(f"PASS: all {file_count} concurrent files arrived independently.", flush=True)


def run_cli(test_name, scenarios_for_run, verify_metadata=False):
    """Run L1 or L2 from the command line; return 0 for success or 1 for failure.

    The workflow uses this exit code to decide whether its test step passed.
    """
    try:
        run_delivery_test(test_name, scenarios_for_run, verify_metadata=verify_metadata)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


def run_concurrent_cli():
    """Run just the concurrent-delivery check with a workflow-friendly exit code."""
    try:
        run_concurrent_delivery_test()
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0