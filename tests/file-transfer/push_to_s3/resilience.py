import sys

from common.resilience import run_writer_resilience_cli
from common.routes import PUSH_TO_S3


def main(argv=None):
    return run_writer_resilience_cli(PUSH_TO_S3, argv, concurrent_files=3, overwrite=True)


if __name__ == "__main__":
    sys.exit(main())
