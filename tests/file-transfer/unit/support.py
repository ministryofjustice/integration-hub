import json
import unittest
from pathlib import Path
from unittest.mock import patch
from common import delivery as harness


class HarnessTestCase(unittest.TestCase):
    def setUp(self):
        """Give each test a fresh simulated S3 service and clock."""
        self.elapsed = 0
        self.uploaded = b""
        self.upload_metadata = {}
        self.upload_content_type = ""
        self.incoming_key = ""
        self.destination_key = ""
        self.listings = 0
        self.calls = []
        self.corrupt_delivery = False
        self.never_deliver = False
        self.local_paths = []
        aws_patch = patch.object(harness, "aws_s3", side_effect=self.fake_aws)
        self.aws = aws_patch.start()
        self.addCleanup(aws_patch.stop)
        clock_patch = patch.object(harness.time, "monotonic", side_effect=lambda: self.elapsed)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        sleep_patch = patch.object(harness.time, "sleep", side_effect=self.advance_time)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def fake_concurrent_aws(self, operation, *arguments, timeout=30):
        """Simulate S3 for overlapping uploads, keeping each file's bytes separately.

        The barrier waits for all uploads to start. A serial implementation would
        time out here. The lock protects shared test data while workers update it.
        """
        self.assertGreater(timeout, 0)
        bucket = arguments[arguments.index("--bucket") + 1]
        if operation == "put-object":
            self.assertEqual(bucket, harness.INCOMING_BUCKET)
            self.upload_barrier.wait(timeout=5)
            incoming_key = arguments[arguments.index("--key") + 1]
            source = Path(arguments[arguments.index("--body") + 1])
            relative_key = incoming_key[len(harness.INCOMING_PREFIX):]
            with self.concurrent_lock:
                self.uploaded_by_key[relative_key] = source.read_bytes()
            return {}
        self.assertEqual(bucket, harness.DESTINATION_BUCKET)
        if operation == "list-objects-v2":
            prefix = arguments[arguments.index("--prefix") + 1]
            with self.concurrent_lock:
                return {
                    "Contents": [
                        {"Key": harness.DESTINATION_PREFIX + key}
                        for key in self.uploaded_by_key
                        if key.startswith(prefix[len(harness.DESTINATION_PREFIX):])
                    ]
                }
        if operation == "get-object":
            destination_key = arguments[arguments.index("--key") + 1]
            relative_key = destination_key[len(harness.DESTINATION_PREFIX):]
            destination = Path(arguments[-1])
            with self.concurrent_lock:
                destination.write_bytes(self.uploaded_by_key[relative_key])
                self.downloaded_keys.append(destination_key)
            return {}
        self.fail(f"Unexpected AWS operation: {operation}")

    def advance_time(self, seconds):
        """Move the simulated clock forward so timeout tests need no real waiting."""
        self.elapsed += seconds

    def fake_aws(self, operation, *arguments, timeout=30):
        """Simulate upload, exact-key listing, download, and HeadObject."""
        self.calls.append(operation)
        self.assertGreater(timeout, 0)
        bucket = arguments[arguments.index("--bucket") + 1]
        if operation == "put-object":
            self.assertEqual(bucket, harness.INCOMING_BUCKET)
            self.incoming_key = arguments[arguments.index("--key") + 1]
            self.assertTrue(self.incoming_key.startswith(harness.INCOMING_PREFIX))
            self.listings = 0
            self.destination_key = (
                harness.DESTINATION_PREFIX
                + self.incoming_key[len(harness.INCOMING_PREFIX):]
            )
            source = Path(arguments[arguments.index("--body") + 1])
            self.local_paths.append(source)
            self.uploaded = source.read_bytes()
            if "--metadata" in arguments:
                self.upload_metadata = json.loads(arguments[arguments.index("--metadata") + 1])
            if "--content-type" in arguments:
                self.upload_content_type = arguments[arguments.index("--content-type") + 1]
            return {}
        self.assertEqual(bucket, harness.DESTINATION_BUCKET)
        if operation == "list-objects-v2":
            self.assertEqual(arguments[arguments.index("--prefix") + 1], self.destination_key)
            self.listings += 1
            if self.never_deliver:
                return {}
            if self.listings == 1:
                return {"Contents": [{"Key": self.destination_key + ".old"}]}
            return {"Contents": [{"Key": self.destination_key}]}
        if operation == "get-object":
            self.assertEqual(arguments[arguments.index("--key") + 1], self.destination_key)
            destination = Path(arguments[-1])
            self.local_paths.append(destination)
            destination.write_bytes(b"incorrect" if self.corrupt_delivery else self.uploaded)
            return {}
        if operation == "head-object":
            self.assertEqual(arguments[arguments.index("--key") + 1], self.destination_key)
            return {
                "ContentLength": len(self.uploaded),
                "ContentType": self.upload_content_type,
                "Metadata": self.upload_metadata,
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "test-destination-key",
            }
        self.fail(f"Unexpected AWS operation: {operation}")
