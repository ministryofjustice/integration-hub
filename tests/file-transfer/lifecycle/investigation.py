import sys
from pathlib import Path

from common.routing import run_routing_cli, run_terminal_routing_test


FIXTURE = Path(__file__).with_name("test-document.pdf")


def run_investigation_test(environment, timeout_seconds=300, poll_seconds=5):
    """Verify the investigation outcome of the file lifecycle using the unchanged PDF."""
    run_terminal_routing_test(
        environment, FIXTURE, "investigation", "application/pdf",
        timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
    )


def main(argv=None):
    return run_routing_cli(
        "Check that the PDF fixture lands in investigation.", run_investigation_test, argv,
    )


if __name__ == "__main__":
    sys.exit(main())
