import sys

from common.resilience import run_writer_resilience_cli
from common.routes import PUSH_TO_SFTP

# The writer's concurrency limit.
CONCURRENT_FILES = 5


def main(argv=None):
    return run_writer_resilience_cli(
        PUSH_TO_SFTP, argv, concurrent_files=CONCURRENT_FILES, overwrite=True
    )


if __name__ == "__main__":
    sys.exit(main())
