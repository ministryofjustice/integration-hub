import argparse
import sys
from pathlib import Path

from common.delivery import run_terminal_routing_test


FIXTURE = Path(__file__).with_name("test-document.pdf")


def run_investigation_test(environment, timeout_seconds=300, poll_seconds=5):
    """Upload the PDF unchanged and verify it reaches investigation."""
    run_terminal_routing_test(
        environment, FIXTURE, "investigation", "application/pdf",
        timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the PDF fixture lands in investigation.")
    parser.add_argument("--environment", choices=("development", "test"), required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        run_investigation_test(args.environment, timeout_seconds=args.timeout_seconds)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
