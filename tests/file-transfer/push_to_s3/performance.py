import sys

from common.levels import run_performance_cli
from common.routes import PUSH_TO_S3


def main(argv=None):
    return run_performance_cli(PUSH_TO_S3, argv)


if __name__ == "__main__":
    sys.exit(main())
