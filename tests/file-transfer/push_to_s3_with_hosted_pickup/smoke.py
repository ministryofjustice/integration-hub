import sys

from common.fixtures import smoke_scenarios
from common.levels import run_delivery_cli
from common.routes import PUSH_TO_S3_WITH_HOSTED_PICKUP


def main():
    return run_delivery_cli(
        PUSH_TO_S3_WITH_HOSTED_PICKUP, "L1 smoke checks", smoke_scenarios
    )


if __name__ == "__main__":
    sys.exit(main())
