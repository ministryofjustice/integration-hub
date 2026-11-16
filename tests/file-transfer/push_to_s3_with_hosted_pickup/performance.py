import sys

from common.levels import run_performance_cli
from common.routes import PUSH_TO_S3_WITH_HOSTED_PICKUP


def main(argv=None):
    return run_performance_cli(PUSH_TO_S3_WITH_HOSTED_PICKUP, argv)


if __name__ == "__main__":
    sys.exit(main())
