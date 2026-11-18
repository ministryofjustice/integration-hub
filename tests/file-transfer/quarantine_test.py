import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

from harness import run_terminal_routing_test


EICAR_URL = "https://secure.eicar.org/eicar.com"
LOCAL_EICAR_FIXTURE = Path(__file__).with_name("eicar.txt")
EICAR_SIZE = 68
EICAR_SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"
DOWNLOAD_TIMEOUT_SECONDS = 30


def download_eicar(fixture):
    """Download the EICAR test file and reject anything other than the original."""
    try:
        subprocess.run(
            [
                "curl", "--disable", "--fail", "--silent", "--show-error", "--location",
                "--proto", "=https", "--proto-redir", "=https",
                "--max-time", str(DOWNLOAD_TIMEOUT_SECONDS),
                "--max-filesize", str(EICAR_SIZE),
                "--output", str(fixture), EICAR_URL,
            ],
            check=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS + 1,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Could not download the EICAR fixture with curl.") from error

    validate_eicar(fixture)


def validate_eicar(fixture):
    with fixture.open("rb") as source:
        contents = source.read(EICAR_SIZE + 1)
    if len(contents) != EICAR_SIZE or hashlib.sha256(contents).hexdigest() != EICAR_SHA256:
        raise ValueError(f"{fixture.name} does not match the EICAR fixture.")


def run_quarantine_test(environment, timeout_seconds=300):
    """Prefer the local fixture, then try curl if it cannot be used."""
    try:
        validate_eicar(LOCAL_EICAR_FIXTURE)
    except (OSError, ValueError) as error:
        print(f"Cannot use local EICAR fixture: {error}. Trying curl.", file=sys.stderr)
    else:
        print(f"Using local EICAR fixture: {LOCAL_EICAR_FIXTURE.name}", flush=True)
        run_terminal_routing_test(
            environment, LOCAL_EICAR_FIXTURE, "quarantine", "application/octet-stream",
            timeout_seconds=timeout_seconds,
        )
        return

    with tempfile.TemporaryDirectory(prefix="file-transfer-quarantine-") as directory:
        fixture = Path(directory) / "eicar.com"
        download_eicar(fixture)
        run_terminal_routing_test(
            environment, fixture, "quarantine", "application/octet-stream",
            timeout_seconds=timeout_seconds,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the EICAR test file lands in quarantine.")
    parser.add_argument("--environment", choices=("development", "test"), required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        run_quarantine_test(args.environment, timeout_seconds=args.timeout_seconds)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
