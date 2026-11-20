import argparse

from common.delivery import run_concurrent_delivery_test, run_overwrite_test
from common.levels import run_checks
from common.routes import select_route
from common.writer import run_duplicate_action_test, run_rejected_request_test


def run_writer_resilience_cli(route_name, argv=None, concurrent_files=3, overwrite=False,
                              extra_checks=()):
    """Run L3 for a route that has its own writer; return 1 if any check fails.

    Checks run in order: concurrent delivery, optional same-size overwrite, the
    duplicate-action check, route-specific extra checks (each called with the
    route), then the opt-in rejection check. The rejection check is opt-in because
    it raises the writer's failed-record metric.
    """
    parser = argparse.ArgumentParser(description="Run the L3 resilience tests.")
    parser.add_argument(
        "--include-rejection-check", action="store_true",
        help="Also send requests the writer must reject; this increments its failure metric.",
    )
    arguments = parser.parse_args(argv)

    def run(check, *args, **keywords):
        return lambda: check(select_route(route_name), *args, **keywords)

    checks = [run(run_concurrent_delivery_test, file_count=concurrent_files,
                  timeout_seconds=300, poll_seconds=2)]
    if overwrite:
        checks.append(run(run_overwrite_test, timeout_seconds=300, poll_seconds=3))
    checks.append(run(run_duplicate_action_test, timeout_seconds=180))
    checks.extend(run(extra) for extra in extra_checks)
    if arguments.include_rejection_check:
        checks.append(run(run_rejected_request_test))
    return run_checks(*checks)
