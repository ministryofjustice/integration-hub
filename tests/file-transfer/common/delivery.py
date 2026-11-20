import json
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common.aws import INCOMING_BUCKET, aws_s3


def _check_timing(timeout_seconds, poll_seconds):
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Timeout and polling interval must be positive.")


def _remaining(deadline, message):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(message)
    return remaining


def upload_to_incoming(route, relative_key, source_path, deadline, metadata=None, content_type=None):
    """Upload one file to the route's incoming prefix and return the incoming key."""
    incoming_key = route.incoming_prefix + relative_key
    arguments = ["--bucket", INCOMING_BUCKET, "--key", incoming_key, "--body", str(source_path)]
    if content_type:
        arguments.extend(["--content-type", content_type])
    if metadata:
        arguments.extend(["--metadata", json.dumps(metadata)])
    remaining = _remaining(deadline, f"Timed out before uploading {relative_key}.")
    aws_s3("put-object", *arguments, timeout=min(30, remaining))
    return incoming_key


def list_keys(route, prefix, deadline, message="Timed out listing the destination."):
    """Return the keys under prefix in the route's destination bucket."""
    listing = aws_s3(
        "list-objects-v2", "--bucket", route.destination_bucket,
        "--prefix", prefix, timeout=min(30, _remaining(deadline, message)),
    )
    return {item["Key"] for item in listing.get("Contents", [])}


def wait_for_delivery(route, relative_key, deadline, poll_seconds, delivered_name=None):
    """Poll until the route has delivered relative_key and return the destination key.

    Only an exact match counts, because S3 prefix searches also list similar names.
    """
    while True:
        keys = list_keys(
            route, route.listing_prefix(relative_key), deadline,
            f"Timed out waiting for {relative_key} to be delivered.",
        )
        found = route.find_delivery(keys, relative_key, delivered_name)
        if found:
            return found
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))


def download(route, destination_key, path, deadline):
    aws_s3(
        "get-object", "--bucket", route.destination_bucket,
        "--key", destination_key, str(path),
        timeout=min(30, _remaining(deadline, "Timed out before downloading the destination file.")),
    )


def head_destination(route, destination_key, deadline):
    return aws_s3(
        "head-object", "--bucket", route.destination_bucket, "--key", destination_key,
        timeout=min(30, _remaining(deadline, "Timed out before reading destination metadata.")),
    )


def verify_copied_object_headers(head, scenario, run_token):
    """Check content type, run-ID metadata and KMS encryption on a delivered object."""
    if head.get("ContentType") != "application/octet-stream":
        raise RuntimeError(f"Delivered {scenario} has unexpected content type.")
    if head.get("Metadata", {}).get("file-transfer-test-run-id") != run_token:
        raise RuntimeError(f"Delivered {scenario} metadata was not preserved.")
    if head.get("ServerSideEncryption") != "aws:kms" or not head.get("SSEKMSKeyId"):
        raise RuntimeError(f"Delivered {scenario} is not encrypted with KMS.")


def run_delivery_test(route, test_name, scenarios_for_run, verify_metadata=False,
                      timeout_seconds=300, poll_seconds=5, run_token=None,
                      measurements=None, on_delivered=None):
    """Upload test files one at a time and check each arrives without changes.

    scenarios_for_run supplies the filenames and expected bytes. When requested,
    also check the content type, run-ID metadata, and presence of KMS encryption.
    on_delivered(destination_key, scenario, filename, fixture, run_token, deadline)
    lets a route add its own checks once the bytes have been verified.
    Return the run ID so another check can find the same files later.
    If a measurements list is supplied, append timings only for verified files.
    """
    _check_timing(timeout_seconds, poll_seconds)
    if verify_metadata and not route.verifies_object_metadata:
        raise ValueError(f"The {route.name} route cannot verify object metadata and encryption.")

    # A unique S3 path prevents an older run's files from satisfying this test.
    run_token = run_token or uuid.uuid4().hex
    scenarios = scenarios_for_run(run_token)
    print(f"File-transfer test: {test_name}.", flush=True)
    print(f"Environment: test | Route: {route.label}", flush=True)
    print(f"Test run ID: {run_token}", flush=True)
    print(f"Scenarios: {len(scenarios)} | Overall timeout: {timeout_seconds:g} seconds", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-test-") as directory:
        deadline = time.monotonic() + timeout_seconds
        for index, (scenario, filename, fixture) in enumerate(scenarios, start=1):
            relative_key = f"{run_token}/{filename}"
            source = Path(directory) / f"source-{index}"
            delivered = Path(directory) / f"delivered-{index}"
            source.write_bytes(fixture)
            print(f"[{index}/{len(scenarios)}] Uploading {scenario} ({len(fixture)} bytes).", flush=True)
            print(f"Source: s3://{INCOMING_BUCKET}/{route.incoming_prefix}{relative_key}", flush=True)
            upload_started = time.monotonic()
            upload_to_incoming(
                route, relative_key, source, deadline,
                content_type="application/octet-stream" if verify_metadata else None,
                metadata={"file-transfer-test-run-id": run_token} if verify_metadata else None,
            )
            print(f"Waiting for delivery to {route.label}.", flush=True)
            destination_key = wait_for_delivery(route, relative_key, deadline, poll_seconds)
            delivery_observed = time.monotonic()
            download(route, destination_key, delivered, deadline)
            if delivered.read_bytes() != fixture:
                raise RuntimeError(f"Delivered contents do not match the {scenario} fixture.")
            if verify_metadata:
                verify_copied_object_headers(
                    head_destination(route, destination_key, deadline), scenario, run_token
                )
                print(f"PASS: {scenario} metadata and KMS encryption verified.", flush=True)
            if on_delivered:
                on_delivered(destination_key, scenario, filename, fixture, run_token, deadline)
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

    print(f"PASS: all {len(scenarios)} file-transfer scenarios completed.", flush=True)
    return run_token


def run_concurrent_delivery_test(route, file_count=3, timeout_seconds=300, poll_seconds=5):
    """Upload several different files at once and check each arrives unchanged.

    Every file has its own expected contents and destination path, so a missing
    file or contents mixed up between files makes the test fail.
    """
    if file_count < 2:
        raise ValueError("Concurrent delivery needs at least two files.")
    _check_timing(timeout_seconds, poll_seconds)

    run_token = uuid.uuid4().hex
    scenarios = [
        (
            f"concurrent file {index}",
            # Pickup routes find a delivery by filename alone, so each name must be unique to the run.
            f"{run_token}-concurrent-{index}.bin",
            f"{run_token}:{index}\n".encode() + bytes([index]) * 4096,
        )
        for index in range(1, file_count + 1)
    ]
    print("File-transfer test: L3 concurrent delivery.", flush=True)
    print(f"Environment: test | Route: {route.label}", flush=True)
    print(f"Test run ID: {run_token}", flush=True)
    print(f"Concurrent files: {file_count} | Overall timeout: {timeout_seconds:g} seconds", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-resilience-") as directory:
        deadline = time.monotonic() + timeout_seconds
        sources = []
        for index, (scenario, filename, fixture) in enumerate(scenarios, start=1):
            source = Path(directory) / f"source-{index}"
            source.write_bytes(fixture)
            sources.append((scenario, f"{run_token}/{filename}", fixture, source))

        # Separate workers allow uploads to overlap instead of waiting for each other.
        with ThreadPoolExecutor(max_workers=file_count) as executor:
            futures = []
            for scenario, relative_key, _, source in sources:
                print(f"Uploading {scenario}: s3://{INCOMING_BUCKET}/{route.incoming_prefix}{relative_key}", flush=True)
                futures.append(executor.submit(upload_to_incoming, route, relative_key, source, deadline))
            try:
                for future in as_completed(futures, timeout=max(deadline - time.monotonic(), 0)):
                    # Surface upload errors from the worker instead of ignoring them.
                    future.result()
            except TimeoutError as error:
                raise TimeoutError("Timed out uploading concurrent fixtures.") from error

        # Each file is awaited separately so none can be satisfied by another's delivery.
        for index, (scenario, relative_key, fixture, _) in enumerate(sources, start=1):
            destination_key = wait_for_delivery(route, relative_key, deadline, poll_seconds)
            delivered = Path(directory) / f"delivered-{index}"
            download(route, destination_key, delivered, deadline)
            if delivered.read_bytes() != fixture:
                raise RuntimeError(f"Concurrent delivery changed contents for {scenario}.")
            print(f"PASS: {scenario} arrived unchanged.", flush=True)

    print(f"PASS: all {file_count} concurrent files arrived independently.", flush=True)


def wait_for_destination_contents(route, destination_key, expected, deadline, poll_seconds, delivered_path):
    """Poll until the exact destination key exists and holds the expected bytes.

    A listing that finds the key with older bytes is not a pass: an overwrite test
    needs to tell the replacement from the file it replaced.
    """
    while True:
        keys = list_keys(route, destination_key, deadline, "Timed out waiting for the expected destination contents.")
        if destination_key in keys:
            download(route, destination_key, delivered_path, deadline)
            if delivered_path.read_bytes() == expected:
                return
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))


def run_overwrite_test(route, timeout_seconds=300, poll_seconds=5):
    """Deliver a file, then deliver different bytes of the same size to the same path.

    Equal sizes ensure a size-only comparison cannot mistake the original for
    the replacement. The destination must end up with the replacement bytes
    and no leftover temporary objects.
    """
    _check_timing(timeout_seconds, poll_seconds)
    if route.recipient_id:
        raise ValueError(f"The {route.name} route gives every delivery its own directory.")
    run_token = uuid.uuid4().hex
    filename = "overwrite-same-size.bin"
    original = f"{run_token}:first".encode() + b"1" * 2048
    replacement = f"{run_token}:other".encode() + b"2" * 2048
    if len(original) != len(replacement) or original == replacement:
        raise RuntimeError("Overwrite fixtures must be different bytes of the same size.")
    relative_key = f"{run_token}/{filename}"
    destination_key = route.destination_prefix + relative_key
    print("File-transfer test: L3 same-size overwrite.", flush=True)
    print(f"Environment: test | Route: {route.label}", flush=True)
    print(f"Test run ID: {run_token}", flush=True)

    with tempfile.TemporaryDirectory(prefix="file-transfer-overwrite-") as directory:
        deadline = time.monotonic() + timeout_seconds
        source = Path(directory) / "source"
        delivered = Path(directory) / "delivered"
        for label, fixture in (("original", original), ("same-size replacement", replacement)):
            source.write_bytes(fixture)
            print(f"Uploading {label}: s3://{INCOMING_BUCKET}/{route.incoming_prefix}{relative_key}", flush=True)
            upload_to_incoming(route, relative_key, source, deadline)
            wait_for_destination_contents(
                route, destination_key, fixture, deadline, poll_seconds, delivered
            )
            print(f"PASS: {label} is at {destination_key}.", flush=True)

        keys = list_keys(
            route, route.run_prefix(run_token), deadline,
            "Timed out before checking for leftover files.",
        )
        extra = [key for key in keys if key != destination_key]
        if extra:
            raise RuntimeError(f"Delivery left {len(extra)} unexpected extra object(s) beside the destination file.")
    print("PASS: same-size replacement overwrote the file and left nothing behind.", flush=True)
