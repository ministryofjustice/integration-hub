# Integration Hub PagerDuty business services

Matching pull requests targeting `main` validate Terraform. Same-repository
PRs also run a production plan. After merge, `main` runs a fresh plan and apply.

## What this creates

This Terraform configuration defines three PagerDuty business services:

- Managed File Transfer
- API Platform
- Events platform

Create the status page, add these services to it and link technical services
in PagerDuty separately. This configuration does not manage alerts,
integrations, escalation policies, schedules or Slack connections.

## Before running

Confirm the PagerDuty account before running a plan. Team assignment is
optional; set `pagerduty_team_id` to assign the services to a team.

Set the `PRODUCTION_ACCOUNT_ID` repository secret in Integration Hub to the
12-digit production AWS account ID. Both plan and apply use OIDC to assume
`role/github-actions/integration-hub-pagerduty`. They need the existing state
bucket, KMS access and `pagerduty/api-key` secret in that account. Confirm the
key can manage the business services before merging. Store the token on one
line, not as JSON.

The workflow reads a token only when it runs. Do not put it in GitHub, code,
variables files or logs. Terraform marks it sensitive and ephemeral, so its
value is not saved in state or plan files.

## Plans and applies

The [PR workflow](../../.github/workflows/pagerduty-plan.yml) checks formatting
and validates without the backend, OIDC or a PagerDuty token. After validation,
the plan job skips forks and uses the existing role and `pagerduty/api-key`
to run a locked production plan. It does not save or apply the plan.

The production role must trust the repository's PR OIDC subject for this to
work. Before enabling that trust, review who can run PR code: these sessions
can read production state and the same PagerDuty token used for apply. If the
token can write, PR code has that capability even though the job runs only
`terraform plan`. Masking the token does not restrict its permissions.

The [apply workflow](../../.github/workflows/pagerduty-apply.yml) runs on
matching pushes to `main`, including the merge that introduces the workflow.
It validates, creates a fresh production plan and applies that saved plan in
the same job. Immediately before apply, it checks the current `main` commit.
If `main` has moved, the run skips apply and records why in its summary. If
the check fails, the job fails without applying. An unrelated push can also
make a run stale without triggering a replacement; check the run summary
before assuming a change was deployed.

The apply job must not use a GitHub environment: that
changes the OIDC subject and the role will not trust it. Keep `main` protected.

Check for existing business services before merging to avoid creating
duplicates.

## Terraform state

State uses `pagerduty/terraform.tfstate` in the
`integration-hub-production-pagerduty-tfstate` bucket in `eu-west-2`.
Use the default workspace. The backend uses KMS encryption and S3 locking.
The backend uses `alias/s3/integration-hub-production-pagerduty-tfstate`
directly as its KMS key identifier. The alias must point to the existing state
key in the production account in `eu-west-2`. This is not the key used for
the API token. No separate KMS lookup or `kms:DescribeKey` permission is needed.

Each plan and apply takes the S3 lock for the length of the command. Jobs that
overlap wait up to five minutes for it, then fail. If a run is killed and
leaves `pagerduty/terraform.tfstate.tflock` behind, check that nothing is
running, then an admin can remove it with `terraform force-unlock` using the ID
from the lock error.
