import sys

from common.resilience import run_l3_cli


def main():
    return run_l3_cli()


if __name__ == "__main__":
    sys.exit(main())
