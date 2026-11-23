import io
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from common import delivery
from common.fixtures import (
    functional_scenarios, nested_directory_scenarios, smoke_scenarios,
)
from common.routes import s3_route
from unit.support import FakeS3, FakeS3TestCase, hosted, presigned


def quiet(function, *args, **kwargs):
    with redirect_stdout(io.StringIO()) as output:
        result = function(*args, **kwargs)
    return result, output.getvalue()


class DeliveryTests(FakeS3TestCase):
    fake_options = {"similar_key_first": True}

    def test_l1_waits_for_the_exact_key_not_a_similar_one(self):
        _, output = quiet(
            delivery.run_delivery_test, self.route, "L1 smoke checks", smoke_scenarios,
            timeout_seconds=10, poll_seconds=1,
        )
        self.assertEqual(
            [operation for operation, _ in self.s3.calls],
            ["put-object", "list-objects-v2", "list-objects-v2", "get-object"],
        )
        self.assertEqual(self.elapsed, 1)
        self.assertIn("PASS: small text delivered unchanged", output)
        self.assertIn("PASS: all 1 file-transfer scenarios completed.", output)

    def test_l2_checks_bytes_metadata_and_encryption(self):
        _, output = quiet(
            delivery.run_delivery_test, self.route, "L2", functional_scenarios,
            verify_metadata=True, timeout_seconds=30, poll_seconds=1,
        )
        for scenario in ("zero-byte file", "binary file", "2 MiB file", "special-character filename"):
            self.assertIn(f"PASS: {scenario} delivered unchanged", output)
        self.assertIn("metadata and KMS encryption verified", output)
        (data, metadata, content_type), = [
            value for key, value in self.s3.incoming.items() if key.endswith("empty.bin")
        ]
        self.assertEqual(content_type, "application/octet-stream")
        self.assertEqual(list(metadata), ["file-transfer-test-run-id"])

    def test_an_aws_error_stops_the_test_immediately(self):
        with patch("common.delivery.aws_s3", side_effect=RuntimeError("AWS access denied")) as aws:
            with self.assertRaisesRegex(RuntimeError, "access denied"):
                delivery.run_delivery_test(self.route, "L1", smoke_scenarios)
        self.assertEqual(aws.call_count, 1)

    def test_the_on_delivered_hook_receives_the_destination_key(self):
        seen = []
        quiet(
            delivery.run_delivery_test, self.route, "L1", smoke_scenarios,
            timeout_seconds=10, poll_seconds=1,
            on_delivered=lambda *args: seen.append(args),
        )
        destination_key, scenario, filename, fixture, run_token, _ = seen[0]
        self.assertEqual(destination_key, f"push-to-s3/{run_token}/payload.txt")
        self.assertEqual((scenario, filename), ("small text", "payload.txt"))
        self.assertIn(run_token.encode(), fixture)


class FailureTests(FakeS3TestCase):
    def test_missing_delivery_times_out_without_download(self):
        self.s3.never_deliver = True
        with self.assertRaisesRegex(TimeoutError, "Timed out"):
            quiet(delivery.run_delivery_test, self.route, "L1", smoke_scenarios,
                  timeout_seconds=2, poll_seconds=1)
        self.assertEqual(self.elapsed, 2)
        self.assertNotIn("get-object", [operation for operation, _ in self.s3.calls])

    def test_changed_contents_fail(self):
        self.s3.corrupt = True
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            quiet(delivery.run_delivery_test, self.route, "L1", smoke_scenarios,
                  timeout_seconds=10, poll_seconds=1)

    def test_failed_delivery_does_not_record_a_verified_measurement(self):
        for failure in ("never_deliver", "corrupt"):
            with self.subTest(failure=failure):
                self.s3.never_deliver = failure == "never_deliver"
                self.s3.corrupt = failure == "corrupt"
                measurements = []
                with self.assertRaises((TimeoutError, RuntimeError)):
                    quiet(delivery.run_delivery_test, self.route, "L4", smoke_scenarios,
                          timeout_seconds=2, poll_seconds=1, measurements=measurements)
                self.assertEqual(measurements, [])

    def test_measurements_separate_observation_from_verification(self):
        durations = {"put-object": 2, "list-objects-v2": 1, "get-object": 3}

        def timed(operation, *arguments, timeout=30):
            self.elapsed += durations[operation]
            return self.s3(operation, *arguments, timeout=timeout)

        measurements = []
        with patch("common.delivery.aws_s3", side_effect=timed):
            run_token, _ = quiet(
                delivery.run_delivery_test, self.route, "L4", smoke_scenarios,
                timeout_seconds=20, poll_seconds=1, measurements=measurements,
            )
        self.assertEqual(measurements, [{
            "run_token": run_token, "filename": "payload.txt",
            "size_bytes": len(self.s3.incoming[next(iter(self.s3.incoming))][0]),
            "observed_delivery_seconds": 3, "verified_seconds": 6,
        }])


class RouteSpecificDeliveryTests(unittest.TestCase):
    def run_delivery(self, route, scenarios, **options):
        s3 = FakeS3(route)
        with patch("common.delivery.aws_s3", side_effect=s3), patch("time.sleep"):
            token, output = quiet(
                delivery.run_delivery_test, route, "test", scenarios,
                timeout_seconds=30, poll_seconds=1, **options,
            )
        return s3, token, output

    def test_metadata_checks_are_refused_where_they_cannot_apply(self):
        route = presigned()
        s3 = FakeS3(route)
        with patch("common.delivery.aws_s3", side_effect=s3):
            with self.assertRaisesRegex(ValueError, "cannot verify object metadata"):
                delivery.run_delivery_test(route, "L2", functional_scenarios, verify_metadata=True)
        self.assertEqual(s3.calls, [])

    def test_hosted_pickup_verifies_metadata_in_its_own_bucket(self):
        route = hosted()
        s3, token, _ = self.run_delivery(route, smoke_scenarios, verify_metadata=True)
        self.assertIn(f"pickup/{token}/payload.txt", s3.objects)
        readers = {bucket for operation, bucket in s3.calls if operation != "put-object"}
        self.assertEqual(readers, {"hosted-test-bucket"})

    def test_invalid_destination_headers_fail_without_recording_verified_delivery(self):
        cases = (
            ({"ContentType": "text/plain"}, "unexpected content type"),
            ({"Metadata": {}}, "metadata was not preserved"),
            ({"Metadata": {"file-transfer-test-run-id": "another-run"}}, "metadata was not preserved"),
            ({"ServerSideEncryption": "AES256"}, "not encrypted with KMS"),
            ({"SSEKMSKeyId": None}, "not encrypted with KMS"),
        )
        for route in (s3_route(), hosted()):
            for headers, error in cases:
                with self.subTest(route=route.name, headers=headers):
                    s3 = FakeS3(route)
                    measurements = []

                    def wrong_headers(operation, *arguments, timeout=30):
                        response = s3(operation, *arguments, timeout=timeout)
                        return {**response, **headers} if operation == "head-object" else response

                    with patch("common.delivery.aws_s3", side_effect=wrong_headers), \
                            self.assertRaisesRegex(RuntimeError, error):
                        quiet(
                            delivery.run_delivery_test, route, "L2", smoke_scenarios,
                            verify_metadata=True, timeout_seconds=30, poll_seconds=1,
                            measurements=measurements,
                        )
                    self.assertEqual(measurements, [])

    def test_nested_directories_keep_their_relative_path(self):
        s3, token, output = self.run_delivery(
            hosted(), lambda t: functional_scenarios(t) + nested_directory_scenarios(t)
        )
        self.assertIn("PASS: all 6 file-transfer scenarios completed.", output)
        self.assertIn(f"pickup/{token}/nested/deeper/payload.txt", s3.objects)
        self.assertIn(f"pickup/{token}/folder with spaces/(1) + copy/special-payload.txt", s3.objects)


class ConcurrentDeliveryTests(unittest.TestCase):
    class ConcurrentS3(FakeS3):
        """Wait for every upload worker before answering, so serial uploads would time out."""

        def __init__(self, route, count):
            super().__init__(route)
            self.barrier = threading.Barrier(count)
            self.lock = threading.Lock()

        def __call__(self, operation, *arguments, timeout=30):
            if operation == "put-object":
                self.barrier.wait(timeout=5)
            with self.lock:
                return super().__call__(operation, *arguments, timeout=timeout)

    def run_concurrent(self, route, count):
        s3 = self.ConcurrentS3(route, count)
        with patch("common.delivery.aws_s3", side_effect=s3):
            _, output = quiet(
                delivery.run_concurrent_delivery_test, route, file_count=count,
                timeout_seconds=10, poll_seconds=1,
            )
        return s3, output

    def test_uploads_overlap_and_every_file_arrives_independently(self):
        for route in (s3_route(), hosted(), presigned()):
            with self.subTest(route=route.name):
                s3, output = self.run_concurrent(route, 3)
                self.assertEqual(len(s3.objects), 3)
                self.assertIn("PASS: all 3 concurrent files arrived independently.", output)

    def test_a_file_never_delivered_is_not_hidden_by_the_others(self):
        route = s3_route()
        s3 = self.ConcurrentS3(route, 3)
        original = s3._deliver

        def drop_one(relative, *args):
            if "concurrent-2" not in relative:
                original(relative, *args)

        s3._deliver = drop_one
        with patch("common.delivery.aws_s3", side_effect=s3), patch("time.sleep"):
            with patch("time.monotonic", side_effect=iter(range(0, 1000))):
                with self.assertRaises(TimeoutError):
                    quiet(delivery.run_concurrent_delivery_test, route, file_count=3,
                          timeout_seconds=40, poll_seconds=1)


class OverwriteTests(FakeS3TestCase):
    route_factory = staticmethod(hosted)

    def test_same_size_replacement_overwrites_and_leaves_nothing_behind(self):
        _, output = quiet(delivery.run_overwrite_test, self.route, timeout_seconds=30, poll_seconds=1)
        self.assertEqual(self.s3.uploads, 2)
        self.assertEqual(len(self.s3.objects), 1)
        self.assertIn("overwrote the file and left nothing behind", output)

    def test_replacement_that_never_arrives_is_not_a_pass(self):
        """The first file is still there, so only the replacement's bytes can pass."""
        self.s3.stale_replacement = True
        with self.assertRaisesRegex(TimeoutError, "expected destination contents"):
            quiet(delivery.run_overwrite_test, self.route, timeout_seconds=5, poll_seconds=1)

    def test_leftover_temporary_objects_fail_the_test(self):
        self.s3.leftover = True
        with self.assertRaisesRegex(RuntimeError, "extra object"):
            quiet(delivery.run_overwrite_test, self.route, timeout_seconds=30, poll_seconds=1)



if __name__ == "__main__":
    unittest.main()
