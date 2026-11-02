import sys

from common.fixtures import functional_scenarios
from common.levels import run_delivery_cli
from common.routes import PUSH_TO_S3


def main():
    return run_delivery_cli(
        PUSH_TO_S3, "L2 functional integrity checks", functional_scenarios,
        verify_metadata=True,
    )


if __name__ == "__main__":
    sys.exit(main())
