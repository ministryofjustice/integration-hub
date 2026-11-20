# File-transfer Tests

These tests cover three hub delivery patterns in the test account: delivery
checks for direct S3 and hosted pickup, and access-control checks for presigned pickup.

| Route | Who owns the destination | Package |
| --- | --- | --- |
| `push-to-s3` | You own the bucket | [push_to_s3](push_to_s3) |
| `push-to-s3-with-hosted-pickup` | We own the bucket; the recipient collects | [push_to_s3_with_hosted_pickup](push_to_s3_with_hosted_pickup) |
| `pull-from-presigned-url` | The recipient retrieves it through a web interface | [pull_from_presigned_url](pull_from_presigned_url) |

## Layout

```text
tests/file-transfer/
  common/                    shared, route-neutral building blocks
    routes.py                Route definition and environment-based selection
    delivery.py              upload, wait for delivery, compare, concurrent, overwrite
    writer.py                action requests, direct writer invocation, duplicate and rejection checks
    checks.py                bucket lifecycle checks
    fixtures.py              synthetic files shared by every route
    levels.py                CLI runners for L1, L2 and L4
    resilience.py            CLI runner for L3 on routes with a writer
  push_to_s3/                smoke.py, functional.py, resilience.py, performance.py
  push_to_s3_with_hosted_pickup/    the same four scripts
  pull_from_presigned_url/          access.py (access-control checks only)
  investigation_test.py      investigation routing check
  quarantine_test.py         quarantine routing check
  unit/                      mocked tests; no AWS credentials needed
```

Each route package holds only what is particular to that route: its scripts and,
for presigned pickup, `access.py`. Anything useful to more than one route belongs
in `common/`. To add a route, add a factory to `common/routes.py` and a package
with the four scripts.

Run scripts from the repository root with `PYTHONPATH=tests/file-transfer`, for
example `PYTHONPATH=tests/file-transfer python3 -B -m push_to_s3.smoke`.

## Test levels

- **L1 smoke:** one small file is delivered unchanged.
- **L2 functional:** zero-byte, binary, 2 MiB and special-character filename
  fixtures, compared byte for byte. Hosted pickup adds nested
  directories. Direct S3 and hosted pickup also verify metadata and KMS
  encryption, because the mover copies them.
- **L2 investigation routing** ([investigation_test.py](investigation_test.py)):
  uploads `test-document.pdf` and checks that it arrives in investigation,
  failing if it is found in clean or quarantine instead.
- **L2 quarantine routing** ([quarantine_test.py](quarantine_test.py)):
  uses the EICAR antivirus test file and checks that it reaches quarantine
  rather than clean or investigation.
- **L3 resilience:** concurrent files, and a repeated action request that must
  not write a second copy. Routes with a fixed destination key (direct S3, hosted
  pickup) also check that a same-size replacement overwrites the original.
- **L4 baseline:** sequential delivery of three 1 MiB files and one 16 MiB file.
  Informational; no threshold. The workflow runs it for direct S3 only.
- **Opt-in rejection check** (`--include-rejection-check`): requests naming an
  unauthorised source must be rejected and deliver nothing. Each rejection counts
  as a failed record, so check that test-account alarms will not page anyone.

Route-specific checks:

| Route | Extra checks |
| --- | --- |
| `push-to-s3-with-hosted-pickup` | Bucket lifecycle configuration expires the pickup prefix after the expected retention (one day by default). |
| `pull-from-presigned-url` | Access control only (`access.py`): the recipient prefix has only READ grants to the expected groups, an anonymous request for the retained file is refused, and the test role cannot read it. |

## Route configuration

All routes default to the agreed test-account configuration in Modernisation
Platform Environments. No additional GitHub route variables are needed. Presigned
pickup resolves the account ID using STS to construct its shared bucket name;
lookup failures stop the test. The tests do not create dispatch entries or storage.

| Route | Defaults |
| --- | --- |
| `push-to-s3-with-hosted-pickup` | Bucket `integration-hub-test-test-harness-pickup`; incoming prefix `test-harness/hosted-pickup/`; destination prefix `pickup/`; retention one day |
| `pull-from-presigned-url` | Bucket `integration-hub-file-transfer-pickup-test-<account-id>`; recipient `test-harness-presigned`; incoming prefix `test-harness/pull-from-presigned-url/`; authorised group `azure-aws-sso-integration-hub` (`1622e2b4-9011-7000-b2fc-87228b7308cd`) |

Local runs may override these using `FILE_TRANSFER_HOSTED_PICKUP_BUCKET`,
`FILE_TRANSFER_HOSTED_PICKUP_INCOMING_PREFIX`,
`FILE_TRANSFER_HOSTED_PICKUP_DESTINATION_PREFIX`,
`FILE_TRANSFER_HOSTED_PICKUP_RETENTION_DAYS`, `FILE_TRANSFER_PRESIGNED_PICKUP_BUCKET`,
`FILE_TRANSFER_PRESIGNED_RECIPIENT_ID`, `FILE_TRANSFER_PRESIGNED_INCOMING_PREFIX`,
and `FILE_TRANSFER_PRESIGNED_AUTHORISED_GROUP_IDS` (comma-separated group IDs).
Explicit empty bucket, recipient or group settings are errors; an empty retention
override visibly skips the lifecycle check. The workflow uses the defaults.

Prefixes must end with `/`.

**Assumptions to confirm before relying on results**

- The hosted pickup mover is named `integration-hub-file-transfer-push-to-s3-with-hosted-pickup`
  in the harness. Check the deployed name.
- The harness role can read the destination buckets, read lifecycle
  configuration, invoke and read the configuration of the writers, and receive
  and delete messages on the notification queue.
- Whether the file mover keeps `original-file-name` metadata through to the clean
  bucket is unconfirmed; the original-filename test will show.

**Presigned pickup**

- The checks are `python3 -B -m pull_from_presigned_url.access`: the recipient
  prefix has only READ grants to the expected groups, an anonymous request for
  the retained file is refused, and the test role cannot read it. The role has
  list but no read permission on the pickup bucket.
- Slack delivery, an authorised member's download and a signed-in non-member's
  refusal are manual checks.
- Pickup directory names are not known in advance, so tests find a delivery by
  its unique filename. Do not reuse filenames across runs.

## Run locally

```sh
export PYTHONPATH=tests/file-transfer
python3 -B -m push_to_s3.smoke
python3 -B -m push_to_s3.functional
python3 -B -m push_to_s3.resilience
python3 -B -m push_to_s3.performance --report /tmp/file-transfer-l4.json
```

For hosted pickup, replace `push_to_s3` with
`push_to_s3_with_hosted_pickup`. Presigned pickup uses the separate `access.py`
checks described above.

## Run local unit tests

From the repository root, with Python 3.9 or newer:

```sh
python3 -B -m unittest discover -s tests/file-transfer -t tests/file-transfer -p 'test_*.py' -v
```

No additional Python packages or AWS credentials are required.

## Run live delivery tests

Live tests use the dedicated test account and harness role. Each calling
repository uses its own `smoke-test` GitHub environment. Confirm the test
environment and delivery infrastructure are ready before running; tests upload
synthetic files to AWS.

To run manually, open **Actions > File Transfer Tests > Run workflow** in
GitHub and select an authorised branch. The workflow runs L1, L2, L3, and both
routing checks after its local unit tests pass. It also runs automatically when
called by the Modernisation Platform Environments workflow after a successful
test deployment.

The routing checks run only against the test account. The selected branch must
contain the test scripts and approved `test-document.pdf` fixture. The quarantine
check uses `eicar.txt` if present, or downloads the fixture with `curl`. The
EICAR fixture can trigger malware alerts.

The harness role needs permission to upload under `test-harness/investigation/`
and `test-harness/quarantine/` in the incoming bucket and list the clean,
investigation and quarantine buckets for those keys.

### Run the investigation routing check locally

Put the approved `test-document.pdf` fixture beside the script. From the
repository root, use AWS CLI v2 credentials for the selected environment with
permission to upload under `test-harness/investigation/` in the incoming bucket
and list the clean, quarantine and investigation buckets. This check does not
use the `push-to-s3` prefix.

```sh
python3 -B tests/file-transfer/investigation_test.py --environment development
```

Use `--environment test` with test-account credentials to target test instead.

### Run the quarantine routing check locally

Use AWS CLI v2 and credentials for the selected environment with
permission to upload under `test-harness/quarantine/` in the incoming bucket and
list the clean, quarantine and investigation buckets. The script verifies and
uses `eicar.txt` beside the script first. If that file is missing, unreadable or
invalid, it uses `curl` to download and verify `eicar.com`. If neither source
provides a valid fixture, the test fails before uploading anything. Run only
in an authorised development or test environment: the fixture is harmless but
can trigger malware alerts.

```sh
python3 -B tests/file-transfer/quarantine_test.py --environment development
```

### Automatic tests for MP pull requests

For relevant pull requests from branches in the Modernisation Platform
Environments repository, L1-L3 run automatically after the deployment workflow
succeeds and includes a test-environment apply. No additional manual approval
is required to run these tests, and they are not restricted to `main`.
Fork pull requests are excluded from live testing.

A PR that does not deploy changes to test does not trigger these live tests.
The dedicated test role and test-only resources remain in use.

This policy applies to MP's `smoke-test` environment. Integration Hub's
separate environment retains its approvals and authorised-branch restrictions
for live runs. Relevant PRs in Integration Hub run local harness unit tests,
not live AWS tests.

These results provide test evidence; they do not automatically gate production
promotion. The wider pre-merge and promotion process is tracked in
[issue #218](https://github.com/ministryofjustice/integration-hub/issues/218).

### Run the L4 baseline on GitHub

1. Open the repository's **Actions** tab and select **File Transfer Tests**.
2. Select **Run workflow**.
3. Choose an authorised branch.
4. Tick **Run the informational L4 baseline** in the workflow inputs, then
   select **Run workflow** to start the run.

The workflow runs L1-L3 first. L4 runs only if those tests succeed, using the
same protected test environment. Its JSON report is saved as a workflow
artifact and a summary table is added to the run.

### Run the L4 baseline locally

You need AWS CLI v2 and Python 3.9 or newer installed.

1. Open [the MoJ AWS access portal](https://moj.awsapps.com/start) and sign in.
2. Expand **integration-hub-file-transfer-test** in the list of AWS accounts.
   If it is missing, request access from the team.
3. Next to your assigned role, click on the `Access keys` link. This will give you options to get your temporary access keys.
4. Using the access keys, you can run the baseline tests:

```sh
PYTHONPATH=tests/file-transfer python3 -B -m push_to_s3.performance \
  --report /tmp/file-transfer-l4.json
```

The test uses `eu-west-2` and uploads 19 MiB of synthetic files. Results are
printed in the terminal and saved to `/tmp/file-transfer-l4.json`.
