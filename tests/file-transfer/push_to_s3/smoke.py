import sys

from common.fixtures import smoke_scenarios
from common.levels import run_cli


def main():
    return run_cli("L1 smoke checks", smoke_scenarios)


if __name__ == "__main__":
    sys.exit(main())
