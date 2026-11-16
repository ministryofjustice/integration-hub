import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from common.aws import INCOMING_BUCKET
from common.routes import hosted_pickup_route, presigned_route, s3_route, sftp_route

HOSTED_ENVIRONMENT = {"FILE_TRANSFER_HOSTED_PICKUP_BUCKET": "hosted-test-bucket"}
SFTP_ENVIRONMENT = {"FILE_TRANSFER_SFTP_DESTINATION_BUCKET": "sftp-test-bucket"}
PRESIGNED_ENVIRONMENT = {
    "FILE_TRANSFER_PRESIGNED_PICKUP_BUCKET": "pickup-test-bucket",
    "FILE_TRANSFER_PRESIGNED_RECIPIENT_ID": "test-recipient",
    "FILE_TRANSFER_PRESIGNED_INCOMING_PREFIX": "test-recipient/uploads/",
}


def hosted():
    return hosted_pickup_route(HOSTED_ENVIRONMENT)


def sftp():
    return sftp_route(SFTP_ENVIRONMENT)


def presigned(**extra):
    return presigned_route({**PRESIGNED_ENVIRONMENT, **extra})


def option(arguments, name):
    return arguments[arguments.index(name) + 1]


class FakeS3:
    """Simulate uploads to the incoming bucket being delivered to a route's destination.

    Direct routes write to destination_prefix + the uploaded key's relative path.
    Pickup routes write to <recipient>/<64 hex>/<filename>, like the notifier.
    Options model delivery faults the tests must catch.
    """

    def __init__(self, route, stale_replacement=False, leftover=False, never_deliver=False,
                 corrupt=False, similar_key_first=False, deliver_twice=False):
        self.route = route
        self.stale_replacement = stale_replacement
        self.leftover = leftover
        self.never_deliver = never_deliver
        self.corrupt = corrupt
        self.similar_key_first = similar_key_first
        self.deliver_twice = deliver_twice
        self.objects = {}
        self.heads = {}
        self.incoming = {}
        self.uploads = 0
        self.listings = 0
        self.calls = []

    def _destination_keys(self, relative, metadata):
        if not self.route.recipient_id:
            return [self.route.destination_prefix + relative]
        name = metadata.get("original-file-name", relative).replace("\\", "/").rsplit("/", 1)[-1]
        copies = 2 if self.deliver_twice else 1
        return [
            f"{self.route.destination_prefix}"
            f"{hashlib.sha256(f'{relative}{n}{self.uploads}'.encode()).hexdigest()}/{name}"
            for n in range(copies)
        ]

    def _deliver(self, relative, data, metadata, content_type):
        for key in self._destination_keys(relative, metadata):
            self.objects[key] = data
            filename = key.rsplit("/", 1)[-1]
            if self.route.recipient_id:
                head = {
                    "VersionId": "pickup-version", "ContentType": "application/octet-stream",
                    "ContentDisposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                    "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": "key", "Metadata": {},
                }
            else:
                head = {
                    "ContentType": content_type, "Metadata": metadata,
                    "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": "key",
                }
            self.heads[key] = head
            if self.leftover:
                self.objects[key + ".part"] = b"partial"

    def __call__(self, operation, *arguments, timeout=30):
        if timeout <= 0:
            raise AssertionError("Timeout must stay positive.")
        bucket = option(arguments, "--bucket")
        self.calls.append((operation, bucket))
        if operation == "put-object":
            assert bucket == INCOMING_BUCKET
            key = option(arguments, "--key")
            assert key.startswith(self.route.incoming_prefix), key
            relative = key[len(self.route.incoming_prefix):]
            data = Path(option(arguments, "--body")).read_bytes()
            metadata = {}
            if "--metadata" in arguments:
                metadata = json.loads(option(arguments, "--metadata"))
            content_type = option(arguments, "--content-type") if "--content-type" in arguments else None
            self.uploads += 1
            self.incoming[key] = (data, metadata, content_type)
            if not self.never_deliver and not (self.stale_replacement and self.uploads > 1):
                self._deliver(relative, data, metadata, content_type)
            return {}
        assert bucket == self.route.destination_bucket, bucket
        if operation == "list-objects-v2":
            prefix = option(arguments, "--prefix")
            self.listings += 1
            if self.similar_key_first and self.listings == 1:
                return {"Contents": [{"Key": prefix + ".old"}]}
            return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]}
        if operation == "get-object":
            key = option(arguments, "--key")
            Path(arguments[-1]).write_bytes(b"corrupt" if self.corrupt else self.objects[key])
            return {}
        if operation == "head-object":
            return self.heads[option(arguments, "--key")]
        raise AssertionError(f"Unexpected AWS operation: {operation}")


class FakeS3TestCase(unittest.TestCase):
    """Run a test against FakeS3 with a simulated clock, so timeouts need no real waiting."""

    route_factory = staticmethod(s3_route)
    fake_options = {}

    def setUp(self):
        self.route = self.route_factory()
        self.elapsed = 0
        self.s3 = FakeS3(self.route, **self.fake_options)
        for replacement in (
            patch("common.delivery.aws_s3", side_effect=lambda *a, **k: self.s3(*a, **k)),
            patch("time.monotonic", side_effect=lambda: self.elapsed),
            patch("time.sleep", side_effect=self.advance),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def advance(self, seconds):
        self.elapsed += seconds
