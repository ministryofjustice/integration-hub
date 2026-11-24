import sys

from common.checks import verify_retention_lifecycle
from common.resilience import run_writer_resilience_cli
from common.routes import PUSH_TO_S3_WITH_HOSTED_PICKUP

# The mover's SQS event source mapping caps concurrency at five.
CONCURRENT_FILES = 5


def main(argv=None):
    return run_writer_resilience_cli(
        PUSH_TO_S3_WITH_HOSTED_PICKUP, argv,
        concurrent_files=CONCURRENT_FILES, overwrite=True,
        extra_checks=(verify_retention_lifecycle,),
    )


if __name__ == "__main__":
    sys.exit(main())
