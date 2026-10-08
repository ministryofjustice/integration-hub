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
job assumes the dedicated role, verifies its identity and runs the smoke test.
The run summary reports the result. Authentication success alone does not mean
file delivery passed. AWS CLI v2 and Python 3 are supplied by the GitHub-hosted
Ubuntu runner; the script uses no additional Python packages.

The [smoke-test script](tests/file_transfer/smoke_test.py) uses this test-only
route, matching the MP Terraform configuration:

| Setting | Value |
| --- | --- |
| Incoming bucket | `integration-hub-file-transfer-test-incoming` |
| Incoming key | `test-harness/push-to-s3/<run-token>/payload.txt` |
| Destination bucket | `integration-hub-file-transfer-test-test-harness` |
| Destination key | `push-to-s3/<run-token>/payload.txt` |
| Region | `eu-west-2` |

The script performs one small `PutObject`, polls `ListObjectsV2` for the exact
destination key, then calls `GetObject`. It relies on bucket-default KMS
encryption and never writes to the destination or deletes AWS objects. Local
temporary files are removed on success or failure. Destination lifecycle rules
manage current and noncurrent file expiry asynchronously; source and intermediate
objects remain subject to the service's existing retention rules.

For a timeout, use the run token in the logs to investigate the service's
CloudWatch logs and dead-letter queues with an authorised operator role. The
harness itself has no diagnostic-log access. Do not copy a fixture into the
destination to make a test pass.

This first test exercises staging, scanning, routing and `push-to-s3` delivery.
It does not cover API, SFTP/FTPS or browser uploads, malicious-file rejection,
retry/idempotency guarantees or completion notifications. External notifications
and automatic post-deployment triggering remain follow-up work. Live delivery
must be verified separately from these local unit checks.