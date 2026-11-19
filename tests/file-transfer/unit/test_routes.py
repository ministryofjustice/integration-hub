import unittest

from common import routes
from common.routes import select_route
from unit.support import (
    HOSTED_ENVIRONMENT, PRESIGNED_ENVIRONMENT,
    hosted, presigned,
)


class RouteConfigurationTests(unittest.TestCase):
    def test_every_route_name_can_be_selected(self):
        environments = {
            "push-to-s3": {},
            "push-to-s3-with-hosted-pickup": HOSTED_ENVIRONMENT,
            "pull-from-presigned-url": PRESIGNED_ENVIRONMENT,
        }
        self.assertEqual(set(environments), set(routes.ROUTE_NAMES))
        for name, environment in environments.items():
            self.assertEqual(select_route(name, environment).name, name)

    def test_unknown_route_fails(self):
        for name in ("push-to-nowhere", "push-to-sftp"):
            with self.subTest(route=name), self.assertRaisesRegex(ValueError, "Unknown route"):
                select_route(name)

    def test_unconfigured_routes_name_the_missing_variable_and_never_guess(self):
        cases = {
            "push-to-s3-with-hosted-pickup": routes.HOSTED_BUCKET_VARIABLE,
            "pull-from-presigned-url": routes.PRESIGNED_RECIPIENT_VARIABLE,
        }
        for name, variable in cases.items():
            for environment in ({}, {variable: "  "}):
                with self.subTest(route=name, environment=environment):
                    with self.assertRaisesRegex(ValueError, variable):
                        select_route(name, environment)

    def test_routes_are_isolated_from_each_other(self):
        all_routes = [select_route("push-to-s3"), hosted(), presigned()]
        self.assertEqual(len({r.incoming_prefix for r in all_routes}), 3)
        self.assertEqual(len({r.dispatch_secret_name for r in all_routes}), 3)
        self.assertEqual(len({r.destination_bucket for r in all_routes}), 3)


class FindDeliveryTests(unittest.TestCase):
    def test_direct_routes_match_only_the_exact_key(self):
        route = select_route("push-to-s3")
        key = "push-to-s3/run/file.bin"
        self.assertEqual(route.find_delivery({key + ".part", key + ".old"}, "run/file.bin"), None)
        self.assertEqual(route.find_delivery({key}, "run/file.bin"), key)

    def test_pickup_routes_match_recipient_pickup_id_and_name(self):
        route = presigned()
        good = f"test-recipient/{'a' * 64}/file.bin"
        near_misses = {
            f"test-recipient/{'a' * 63}/file.bin",
            f"test-recipient/{'a' * 64}/nested/file.bin",
            f"other-recipient/{'a' * 64}/file.bin",
            f"test-recipient/{'a' * 64}/file.bin.old",
        }
        self.assertIsNone(route.find_delivery(near_misses, "run/file.bin"))
        self.assertEqual(route.find_delivery(near_misses | {good}, "run/dir/file.bin"), good)

    def test_pickup_delivery_uses_the_given_name_when_it_differs(self):
        route = presigned()
        key = f"test-recipient/{'b' * 64}/real name.csv"
        self.assertIsNone(route.find_delivery({key}, "run/upload.bin"))
        self.assertEqual(route.find_delivery({key}, "run/upload.bin", "real name.csv"), key)

    def test_a_second_pickup_directory_is_a_duplicate_delivery(self):
        keys = {f"test-recipient/{c * 64}/file.bin" for c in "ab"}
        with self.assertRaisesRegex(RuntimeError, "2 pickup directories"):
            presigned().find_delivery(keys, "run/file.bin")


if __name__ == "__main__":
    unittest.main()
