# File-transfer Tests

The file-transfer tests check that synthetic files are delivered through the
direct-to-S3 `push-to-s3` route in the test account and arrive unchanged.

## Test coverage

- **Local unit tests** ([test_harness.py](test_harness.py)) mock AWS and polling
  to check the shared test harness, scenarios, and failure handling. This
  includes retrying terminal-bucket lookups and timing out if only a similar
  object key appears. They need no AWS credentials.
- **L1 smoke** ([smoke_test.py](smoke_test.py)) uploads one small text file and
  verifies its delivery.
- **L2 functional** ([functional_test.py](functional_test.py)) checks delivery
  of zero-byte, binary, 2 MiB, and special-character filename fixtures. It
  verifies file contents, metadata, and KMS encryption.
- **L3 resilience** ([resilience_test.py](resilience_test.py)) checks delivery
  of concurrent files and confirms repeating the same action request does not
  create a duplicate destination version.
- **L2 investigation routing** ([investigation_test.py](investigation_test.py))
  uploads `test-document.pdf` and checks that it arrives in the investigation
  bucket, failing if it is found in clean or quarantine instead.
- **L2 quarantine routing** ([quarantine_test.py](quarantine_test.py)) uses
  the EICAR antivirus test file and checks that it reaches quarantine rather than
  clean or investigation.
- **L4 baseline** ([performance_test.py](performance_test.py)) measures
  sequential delivery for three 1 MiB files and one 16 MiB file. Results are
  informational; no performance threshold is enforced.

The workflow runs the L1-L3 direct-to-S3 checks and both routing checks on each
live test run. It does not test other upload routes, SQS retries, DLQ replay, or
negative cases.

## Run local unit tests

From the repository root, with Python 3.9 or newer:

```sh
python3 -B -m unittest discover -s tests/file-transfer -p 'test_*.py' -v
```

No additional Python packages or AWS credentials are required.

## Run live delivery tests

Live tests use the dedicated test account and require access to the protected
`smoke-test` GitHub environment and its harness role. Confirm the test
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
python3 -B tests/file-transfer/performance_test.py \
  --report /tmp/file-transfer-l4.json
```

The test uses `eu-west-2` and uploads 19 MiB of synthetic files. Results are
printed in the terminal and saved to `/tmp/file-transfer-l4.json`.
