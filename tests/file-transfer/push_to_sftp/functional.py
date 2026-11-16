import sys

from common.fixtures import functional_scenarios, nested_directory_scenarios
from common.levels import run_delivery_cli
from common.routes import PUSH_TO_SFTP


def sftp_functional_scenarios(run_token):
    """The shared fixtures plus nested directories, which the SFTP server must create."""
    return functional_scenarios(run_token) + nested_directory_scenarios(run_token)


def main():
    # Object metadata and KMS headers do not exist on an SFTP server's files.
    return run_delivery_cli(PUSH_TO_SFTP, "L2 functional integrity checks", sftp_functional_scenarios)


if __name__ == "__main__":
    sys.exit(main())
