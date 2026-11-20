# File-transfer tests

These tests send files through the File Transfer Service and check what happens
to them. They cover delivery to S3, hosted pickup, presigned-pickup permissions,
and the files sent to quarantine or investigation.

Live tests use AWS resources. Unit tests check the test code without connecting
to AWS.

## Finding your way around

```text
tests/file-transfer/
  common/                         Code shared by the tests
  lifecycle/                      Quarantine and investigation checks
  push_to_s3/                     Delivery to a recipient's S3 bucket
  push_to_s3_with_hosted_pickup/   Delivery to a pickup bucket we provide
  pull_from_presigned_url/        Access to files collected through the portal
  unit/                           Tests for the test code
```

The two S3 delivery packages have the same four entry points:

- `smoke.py` sends one small file to check that delivery works.
- `functional.py` checks empty files, binary data, larger files and unusual
  filenames. It also checks metadata and encryption. Hosted pickup includes
  nested directories.
- `resilience.py` checks concurrent uploads, replacing a file and repeating a
  delivery request. It also checks that the original clean-file version is kept.
- `performance.py` measures delivery times for three 1 MiB files and one
  16 MiB file. It records a baseline; there is no pass/fail speed target.

In the workflow, these are labelled L1 smoke, L2 functional, L3 resilience and
L4 baseline.

The [lifecycle checks](lifecycle/) run once for the shared processing pipeline.
They check that the approved PDF reaches investigation and the EICAR antivirus
test file reaches quarantine. Finding either file in the wrong bucket fails
the check.

Presigned pickup has an [access check](pull_from_presigned_url/access.py).
It waits for a file to be available for pickup, checks who has READ grants, and
confirms that neither an anonymous visitor nor the test role can read the file
directly. Checking the Slack notification and downloading through the portal
as an authorised or unauthorised user are manual tests.

## Run the unit tests

You need Python 3.9 or newer. From the repository root, run:

```sh
python3 -B -m unittest discover -s tests/file-transfer -t tests/file-transfer -p 'test_*.py' -v
```

You do not need AWS credentials or any extra Python packages.

## Run live tests

You need AWS CLI v2, Python 3.9 or newer, and credentials for the test account.
The delivery tests use `eu-west-2`. Check that you are using the intended
account and role before uploading anything.

From the repository root:

```sh
export PYTHONPATH=tests/file-transfer

python3 -B -m push_to_s3.smoke
python3 -B -m push_to_s3.functional
python3 -B -m push_to_s3.resilience
python3 -B -m push_to_s3.performance --report /tmp/file-transfer-l4.json
```

Replace `push_to_s3` with `push_to_s3_with_hosted_pickup` to test hosted pickup.
Its resilience check also checks the bucket's one-day expiry rule. This checks
the rule, not whether S3 has already deleted a file.

For presigned pickup:

```sh
python3 -B -m pull_from_presigned_url.access
```

For quarantine and investigation:

```sh
python3 -B -m lifecycle.investigation --environment test
python3 -B -m lifecycle.quarantine --environment test
```

These two commands also accept `--environment development` and
`--timeout-seconds`. Use credentials for the environment you select.
The investigation check uses the PDF in [lifecycle/](lifecycle/). The quarantine
check uses `lifecycle/eicar.txt` if it is valid, otherwise it downloads and
verifies the EICAR fixture using `curl`. **EICAR is harmless, but running this
check can trigger malware alerts.**

Resilience tests can also send deliberately invalid requests with
`--include-rejection-check`. Leave this off unless you have checked the alarm
impact: rejected requests increase the writer's failure metric.

## Run through GitHub Actions

Open **Actions > File Transfer Tests > Run workflow**, choose an authorised
branch and select the optional checks you want.

Every live run checks direct-S3 delivery, investigation and quarantine.
Hosted pickup, presigned pickup and the L4 baseline are optional. L4 writes a
JSON report, which is saved as a workflow artifact, and adds timings to the
run summary.

Unit tests run first. Live tests use the dedicated test role and the
`smoke-test` GitHub environment, so its branch restrictions and approvals apply.

The [workflow](../../.github/workflows/file-transfer-tests.yml) also runs:

- **Unit tests on pull requests to `main`.**
- **Unit and default live tests on pushes to `smoke-test`.**
- **Unit and default live tests when called after an eligible MP test
  deployment.** Fork pull requests do not run live tests.

The push and pull-request triggers apply when the test files or workflow
change. These checks provide evidence; they do not automatically approve a
production release. See [#218](https://github.com/ministryofjustice/integration-hub/issues/218)
for the wider promotion process.

## Test settings

The defaults match the test resources managed in
[Modernisation Platform Environments](https://github.com/ministryofjustice/modernisation-platform-environments/tree/main/terraform/environments/integration-hub-file-transfer).
You do not need to add route variables in GitHub. The infrastructure and
permissions must already be deployed; the tests do not create them.

The bucket names, incoming prefixes and local overrides are defined in
[common/routes.py](common/routes.py). The expected pickup group is defined in
[pull_from_presigned_url/access.py](pull_from_presigned_url/access.py).
Presigned pickup looks up the AWS account ID to find its bucket.

Use the dedicated test role when collecting results. It can read delivered
files but cannot write them to the destination. For presigned pickup it can
list files, but must not be able to read them.

Files go through normal scanning. Do not copy a file into a destination or
bypass scanning to make a test pass. The duplicate-request checks call the
writer directly, so they do not prove that SQS retries or DLQ replay work.
