# Ministry of Justice Integration Hub

[![Ministry of Justice Repository Compliance Badge](https://github-community.service.justice.gov.uk/repository-standards/api/integration-hub/badge)](https://github-community.service.justice.gov.uk/repository-standards/integration-hub)

This repository holds files relevant to the Ministry of Justice Integration Hub.

It contains the [Integration Hub user guide](https://user-guide.integration-hub.service.justice.gov.uk), architectural decision records, and other design artefacts.

It will also serve as a landing page for the team's backlog via GitHub Projects.

## Local Development

The site uses the GOV.UK [tech-docs-gem](https://github.com/alphagov/tech-docs-gem).
Docker provides the dependencies; no local Ruby or Node.js installation is required.

### Requirements

- **macOS:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) and Make
  (available through Xcode Command Line Tools: `xcode-select --install`).
- **Linux:** [Docker Engine](https://docs.docker.com/engine/install/) and Make, installed
  through your distribution's package manager. Ensure your user can run Docker commands.
- **Windows:** [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and
  [Docker Desktop with WSL integration](https://docs.docker.com/desktop/features/wsl/).
  Install Make in your WSL distribution and run the commands below in its Linux terminal,
  not PowerShell or Command Prompt.

Start Docker before continuing. On ARM machines, including Apple Silicon Macs, the preview
uses an AMD64 image and requires emulation support. Docker Desktop provides this by default;
ARM Linux users must [configure emulation](https://docs.docker.com/build/building/multi-platform/#qemu).

### Preview the Site

Clone this repository and, from its root directory, run:

```sh
make preview
```

Then open `http://127.0.0.1:4567`.

The first run downloads the Docker image and may take a few minutes. Edit the documentation
in `source/` and refresh your browser to see changes. Press **Ctrl+C** to stop the preview;
your local files are retained.

If port 4567 is already in use, run `make preview PORT=4568` and open `http://127.0.0.1:4568`.

The [Makefile](Makefile) contains the Docker command and uses the same pinned publisher image
as the [publishing workflow](.github/workflows/documentation.yml).

### Check Links

From the repository's root directory, run:

```sh
make link-check
```

This runs Lychee in Docker using the version and link-check options used by CI. No local
Lychee installation is required. The check reads Markdown, HTML, and ERB files and needs
internet access to check external links.

Review any failures in the terminal before opening a PR. HTTP responses 403 and 429 are
accepted, matching CI, so a passing check does not guarantee that every link is accessible.

## File-transfer Smoke Test

The [File Transfer Tests workflow](.github/workflows/file-transfer-tests.yml) tests
direct-S3 delivery in the file-transfer **test** account. It uploads a small,
synthetic text file, waits up to five minutes for delivery, downloads it and
compares its bytes. A fresh UUID in the key and contents prevents a previous
run's file from satisfying the test. AWS errors and content mismatches fail the run.

See the [current and planned harness flowcharts](tests/file_transfer/flowcharts.md).

### Local Checks Without AWS

With Python 3.9 or newer, run:

```sh
python3 -B -m unittest discover -s tests/file_transfer -p 'test_*.py' -v
```

These unit tests mock AWS calls and polling delays. No AWS credentials or extra
Python packages are required. They also run on PRs changing the test script or
workflow. The live AWS job does not run on PR events.

While the workflow is not yet on `main`, relevant pushes to the `smoke-test`
branch also run the local checks and then the live AWS test. The `smoke-test`
GitHub environment must explicitly allow that branch; required approvals still
apply. Pushing this branch can therefore upload a test fixture to AWS. Pushes to
other branches do not trigger this workflow. Manual dispatch becomes available
once the workflow exists on the default branch.

### Run Against the Test Account

Before running, confirm the dedicated harness role, its OIDC trust policy,
the destination bucket and KMS key, and the parent and `push-to-s3` delivery
configuration are deployed. The `smoke-test` GitHub environment must contain
`ACCOUNT_ID` for the test account and `AWS_REGION` set to `eu-west-2`, with
deployment branch restrictions and reviewer protections agreed by the team.

In GitHub, select **Actions > File Transfer Tests > Run workflow**, choose an
authorised branch (normally `main`) and run it. After local checks pass, the live
job assumes the dedicated role, verifies its identity and runs L1 smoke, L2
functional, and L3 concurrent-delivery checks. The run summary reports each
layer separately. Authentication success alone does not mean file delivery
passed.
AWS CLI v2 and Python 3 are supplied by the GitHub-hosted Ubuntu runner; the
script uses no additional Python packages.

The [L1 smoke test](tests/file_transfer/smoke_test.py) and
[L2 functional test](tests/file_transfer/functional_test.py) use this test-only
route, matching the MP Terraform configuration. Both use shared AWS, polling,
and verification code from [harness.py](tests/file_transfer/harness.py); its
mocked unit tests are in [test_harness.py](tests/file_transfer/test_harness.py).
The [L3 resilience test](tests/file_transfer/resilience_test.py) exercises
three independent uploads at the same time, then invokes the test writer twice
with the same action request and verifies the duplicate does not create another
destination version. This checks writer idempotency, not SQS redelivery.

### L4 Non-functional Baseline

The [L4 performance test](tests/file_transfer/performance_test.py) measures the
same `push-to-s3` route using the shared delivery runner. It uploads three 1 MiB
files and one 16 MiB file sequentially: 19 MiB of synthetic source data in total.
Every delivered file must match its original bytes. It uses the existing S3
permissions; no additional MP permissions are needed for L4.

To opt in, select **Run workflow** and enable **Run the informational L4 baseline**.
L4 runs after L1-L3 succeed, using the same protected test environment and role.
It does not run on pushes, PR events, or a schedule. The workflow keeps its JSON
report as an artifact for 30 days and adds a table to the run summary.

For an authorised local session using the dedicated test role:

```sh
python3 -B tests/file_transfer/performance_test.py --report /tmp/file-transfer-l4.json
```

The report contains per-file timings, run ID, timestamps, sizes, polling settings,
and minimum/median/maximum observed delivery times grouped by file size:

- **Observed delivery:** upload start to the first listing that finds the exact
  destination key. This includes upload time, CLI overhead and polling delay
  (two-second intervals), not just server-side processing time.
- **Verified time:** upload start through download and byte comparison.
- **Effective MiB/s:** verified payload MiB divided by the sum of verified times
  for that file size. This is a sequential harness rate, not peak service throughput.

Measurements are informational until targets are agreed. AWS errors, content
mismatches and the ten-minute safety timeout still fail execution. A failed run
retains already-verified samples, marked as a partial result rather than a complete
baseline. Compare repeated runs under similar conditions; three small samples and
one larger sample do not establish reliable tail latency or a performance guarantee.

This is the first bounded L4 baseline, not the complete non-functional suite.
The 16 MiB file is larger than L2's fixtures but does not exercise the writer's
multipart-copy path. Sustained/concurrent load, multi-GB files, capacity limits and
performance regression thresholds remain follow-up work. Live measurements have
not yet been collected. Use only synthetic data and do not commit local reports.

### Route and Verification Details

| Setting | Value |
| --- | --- |
| Incoming bucket | `integration-hub-file-transfer-test-incoming` |
| Incoming key | `test-harness/push-to-s3/<run-token>/<file-name>` |
| Destination bucket | `integration-hub-file-transfer-test-test-harness` |
| Destination key | `push-to-s3/<run-token>/<file-name>` |
| Region | `eu-west-2` |

L1 uploads one small synthetic text file. L2 uploads zero-byte, binary, 2 MiB,
and filename-with-spaces-and-punctuation fixtures. For each L2 file, the
harness polls `ListObjectsV2` for the exact destination key, downloads it with
`GetObject`, compares the bytes, and checks that content type and user metadata
were preserved and that the object uses KMS encryption. Each layer has one
overall timeout. The harness never writes to the destination or deletes AWS
objects. Local temporary files are removed on success or failure.
Destination lifecycle rules manage current and noncurrent file expiry
asynchronously; source and intermediate objects remain subject to the
service's existing retention rules.

For a timeout, use the run token in the logs to investigate the service's
CloudWatch logs and dead-letter queues with an authorised operator role. The
harness itself has no diagnostic-log access. Do not copy a fixture into the
destination to make a test pass.

L1 exercises the happy path for the `push-to-s3` route. L2 currently checks
content integrity, metadata preservation, and KMS encryption for the additional
fixtures on that route; it is not yet the full L2 route/scenario matrix. Tag
checks and live negative cases are not included. L3 currently checks concurrent
delivery of independent files only; retries, DLQ/replay, tenant isolation, and
alerting are not yet covered. API, SFTP/FTPS or browser uploads,
malicious-file rejection, and completion notifications are also not covered.
External notifications and automatic post-deployment triggering remain
follow-up work. Live delivery must be verified separately from these local
unit checks.