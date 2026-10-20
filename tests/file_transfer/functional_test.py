import sys

from harness import run_cli


LARGE_FIXTURE_BYTES = 2 * 1024 * 1024


def functional_scenarios(run_token):
    binary_fixture = run_token.encode() + bytes(range(256)) * 32
    large_fixture = (
        run_token.encode()
        + bytes(range(256)) * (LARGE_FIXTURE_BYTES // 256 + 1)
    )[:LARGE_FIXTURE_BYTES]
    return [
        ("zero-byte file", "empty.bin", b""),
        ("binary file", "binary-payload.bin", binary_fixture),
        ("2 MiB file", "multi-megabyte.bin", large_fixture),
        (
            "special-character filename",
            "special chars + (1).txt",
            f"Synthetic fixture: {run_token}\n".encode(),
        ),
    ]


if __name__ == "__main__":
    sys.exit(
        run_cli(
            "L2 functional integrity checks",
            functional_scenarios,
            verify_metadata=True,
        )
    )