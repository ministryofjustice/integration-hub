import sys

from common.fixtures import functional_scenarios
from common.levels import run_cli


def main():
    return run_cli("L2 functional integrity checks", functional_scenarios, verify_metadata=True)


if __name__ == "__main__":
    sys.exit(main())
