import sys

from common.fixtures import functional_scenarios, nested_directory_scenarios
from common.levels import run_delivery_cli
from common.routes import PUSH_TO_S3_WITH_HOSTED_PICKUP


def hosted_pickup_functional_scenarios(run_token):
    """The shared fixtures plus nested keys, which must keep their relative path under the destination prefix."""
    return functional_scenarios(run_token) + nested_directory_scenarios(run_token)


def main():
    # The copy preserves content type and user metadata and encrypts with the hosted bucket's key.
    return run_delivery_cli(
        PUSH_TO_S3_WITH_HOSTED_PICKUP, "L2 functional integrity checks",
        hosted_pickup_functional_scenarios, verify_metadata=True,
    )


if __name__ == "__main__":
    sys.exit(main())
