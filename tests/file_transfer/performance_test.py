import argparse
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness import REGION, run_delivery_test


MIB = 1024 * 1024
SAMPLE_SIZES_MIB = (1, 1, 1, 16)
TIMEOUT_SECONDS = 600
POLL_SECONDS = 2


def performance_scenarios(run_token):
    """Create three 1 MiB samples and one 16 MiB file, totalling 19 MiB.

    Files are synthetic and include the run ID and sample number. Sizes are
    deliberately bounded; this is not a multipart or maximum-size load test.
    """
    scenarios = []
    for index, size_mib in enumerate(SAMPLE_SIZES_MIB, start=1):
        size_bytes = size_mib * MIB
        marker = f"L4 synthetic fixture: {run_token}:{index}\n".encode()
        block = marker + bytes(range(256))
        fixture = (block * (size_bytes // len(block) + 1))[:size_bytes]
        scenarios.append((
            f"{size_mib} MiB sample {index}",
            f"baseline-{index}-{size_mib}mib.bin",
            fixture,
        ))
    return scenarios


def summarise_measurements(measurements):
    """Group verified samples by size so small and larger files are not mixed.

    Effective rate is payload MiB divided by summed upload-to-verification time.
    It includes polling and downloads, so it is not peak service throughput.
    """
    baselines = []
    for size_bytes in sorted({sample["size_bytes"] for sample in measurements}):
        samples = [sample for sample in measurements if sample["size_bytes"] == size_bytes]
        observed = [sample["observed_delivery_seconds"] for sample in samples]
        verified_seconds = sum(sample["verified_seconds"] for sample in samples)
        baselines.append({
            "size_bytes": size_bytes,
            "sample_count": len(samples),
            "min_observed_delivery_seconds": min(observed),
            "median_observed_delivery_seconds": statistics.median(observed),
            "max_observed_delivery_seconds": max(observed),
            "effective_mib_per_second": (
                size_bytes * len(samples) / MIB / verified_seconds
                if verified_seconds > 0 else None
            ),
        })
    return baselines


def run_performance_test():
    """Measure the normal push-to-s3 route and keep partial results on failure.

    Slow results do not fail an agreed performance threshold because none exists
    yet. AWS errors, corrupted files and the ten-minute safety timeout still fail.
    """
    report = {
        "schema_version": 1,
        "status": "incomplete",
        "environment": "test",
        "route": "push-to-s3",
        "region": REGION,
        "run_token": uuid.uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expected_files": len(SAMPLE_SIZES_MIB),
        "expected_bytes": sum(SAMPLE_SIZES_MIB) * MIB,
        "timeout_seconds": TIMEOUT_SECONDS,
        "poll_seconds": POLL_SECONDS,
        "measurements": [],
    }
    try:
        run_delivery_test(
            "L4 non-functional baseline", performance_scenarios,
            timeout_seconds=TIMEOUT_SECONDS, poll_seconds=POLL_SECONDS,
            run_token=report["run_token"], measurements=report["measurements"],
        )
        report["status"] = "completed"
    except (RuntimeError, OSError, ValueError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
        print(f"FAIL: {error}", file=sys.stderr)
    report["baselines"] = summarise_measurements(report["measurements"])
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return report


def format_summary(report):
    """Render a readable baseline table, clearly marking partial or failed runs."""
    lines = [
        "### L4 non-functional baseline",
        "",
        f"Status: **{report['status']}**. Verified files: "
        f"{len(report['measurements'])}/{report['expected_files']}.",
        f"Run ID: `{report['run_token']}`",
        "",
        "| File size (MiB) | Samples | Min observed (s) | Median observed (s) | Max observed (s) | Effective MiB/s |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for baseline in report["baselines"]:
        rate = baseline["effective_mib_per_second"]
        rate_text = f"{rate:.3f}" if rate is not None else "n/a"
        lines.append(
            f"| {baseline['size_bytes'] / MIB:g} | {baseline['sample_count']} | "
            f"{baseline['min_observed_delivery_seconds']:.3f} | "
            f"{baseline['median_observed_delivery_seconds']:.3f} | "
            f"{baseline['max_observed_delivery_seconds']:.3f} | {rate_text} |"
        )
    lines.extend([
        "",
        "Observed delivery is measured from upload start to the first exact-key listing. "
        "It includes upload, AWS CLI overhead and polling delay; it is not server-side latency.",
        "Effective rate includes the verification download. Samples are sequential, "
        "not a peak-throughput or saturation test. The 16 MiB result is one sample only.",
        "Performance numbers are informational; no performance thresholds are agreed. "
        "Failed runs contain partial measurements only and are not complete baselines.",
        "",
    ])
    return "\n".join(lines)


def run_l4_cli(argv=None):
    """Run L4 on demand, write JSON evidence and optionally append a workflow summary.

    Return 1 if delivery or report writing fails; do not present failures as a
    successful baseline just because performance thresholds are informational.
    """
    parser = argparse.ArgumentParser(description="Run the bounded L4 push-to-s3 baseline.")
    parser.add_argument("--report", type=Path, default=Path("file-transfer-l4.json"))
    parser.add_argument("--summary", type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = run_performance_test()
        arguments.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        summary = format_summary(report)
        print(summary)
        if arguments.summary:
            with arguments.summary.open("a") as summary_file:
                summary_file.write(summary)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(run_l4_cli())