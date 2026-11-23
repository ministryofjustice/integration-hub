import sys

from common.delivery import run_concurrent_delivery_test
from common.writer import run_duplicate_action_test


def run_l3_cli():
    """Run concurrent delivery, then duplicate handling; return 1 if either fails."""
    try:
        run_concurrent_delivery_test(timeout_seconds=120, poll_seconds=2)
        run_duplicate_action_test(timeout_seconds=180)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0
