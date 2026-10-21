import sys
from harness import run_cli


def smoke_scenarios(run_token):
    """Create one small text file to check that the basic delivery route works.

    Each scenario contains a description, filename, and expected file contents.
    The run ID makes the contents specific to this test run.
    """
    return [
        (
            "small text",
            "payload.txt",
            f"Integration Hub file-transfer smoke test: {run_token}\n".encode(),
        ),
    ]


if __name__ == "__main__":
    sys.exit(run_cli("L1 smoke checks", smoke_scenarios))