import argparse
import math
import time
import uuid

from common.aws import aws_s3
from common.levels import run_checks


def run_terminal_routing_test(environment, fixture, expected_area, content_type,
                              timeout_seconds=300, poll_seconds=5):
    """Upload a fixture and check which terminal bucket receives its exact key."""
    if environment not in ("development", "test"):
        raise ValueError("Routing tests only support development and test.")
    if expected_area not in ("quarantine", "investigation"):
        raise ValueError("Expected area must be quarantine or investigation.")
    if any(not math.isfinite(value) or value <= 0 for value in (timeout_seconds, poll_seconds)):
        raise ValueError("Timeout and polling interval must be finite and positive.")
    if not fixture.is_file():
        raise FileNotFoundError(f"{expected_area.capitalize()} fixture not found: {fixture}")

    bucket_prefix = f"integration-hub-file-transfer-{environment}-"
    object_key = f"test-harness/{expected_area}/{uuid.uuid4().hex}/{fixture.name}"
    destination = f"s3://{bucket_prefix}{expected_area}/{object_key}"
    deadline = time.monotonic() + timeout_seconds

    def request_timeout(message):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(message)
        return min(30, remaining)

    print(f"File-transfer test: L2 {expected_area} routing.", flush=True)
    print(f"Uploading s3://{bucket_prefix}incoming/{object_key}", flush=True)
    aws_s3(
        "put-object", "--bucket", bucket_prefix + "incoming", "--key", object_key,
        "--body", str(fixture), "--content-type", content_type,
        timeout=request_timeout("Timed out before uploading the routing fixture."),
    )

    areas = [area for area in ("clean", "quarantine", "investigation") if area != expected_area]
    areas.append(expected_area)
    timeout_message = f"Timed out waiting for {destination}."
    while True:
        for area in areas:
            listing = aws_s3(
                "list-objects-v2", "--bucket", bucket_prefix + area,
                "--prefix", object_key, timeout=request_timeout(timeout_message),
            )
            if not any(item["Key"] == object_key for item in listing.get("Contents", [])):
                continue
            if area != expected_area:
                raise RuntimeError(f"Fixture arrived in {area}, not {expected_area}: {object_key}")
            print(f"PASS: fixture arrived at {destination}", flush=True)
            return
        time.sleep(min(poll_seconds, request_timeout(timeout_message)))


def run_routing_cli(description, check, argv=None):
    """Run a terminal-bucket routing check with shared arguments and error handling."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--environment", choices=("development", "test"), required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    return run_checks(lambda: check(args.environment, timeout_seconds=args.timeout_seconds))
