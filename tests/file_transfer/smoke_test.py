import sys
from harness import run_cli


def smoke_scenarios(run_token):
    return [
        (
            "small text",
            "payload.txt",
            f"Integration Hub file-transfer smoke test: {run_token}\n".encode(),
        ),
    ]


if __name__ == "__main__":
    sys.exit(run_cli("L1 smoke checks", smoke_scenarios))