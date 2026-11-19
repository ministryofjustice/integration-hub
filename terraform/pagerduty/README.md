# Integration Hub PagerDuty business services

The GitHub Actions workflow plans changes on pull requests and applies them when
they are merged to `main`.

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
12-digit production AWS account ID. The workflow uses OIDC to assume these
roles in that account:

- `role/github-actions/integration-hub-pagerduty-plan` for pull request plans
- `role/github-actions/integration-hub-pagerduty` for applies on `main`

The roles, secrets and state bucket are created in
`modernisation-platform-environments`. Deploy that change first.

The API tokens are stored in AWS Secrets Manager: `pagerduty/api-key` for
applies and a read-only key in `pagerduty/api-key-read-only` for pull request
plans. When replacing either one, an authorised person should store just the
token on one line, not JSON.

The workflow reads a token only when it runs. Do not put it in GitHub, code,
variables files or logs. Terraform marks it sensitive and ephemeral, so its
value is not saved in state or plan files.

## Plans and applies

Pull requests run formatting and validation without AWS access. For pull
requests from this repository, the plan job then waits for approval in the
`pagerduty-production-plan` environment. It assumes the plan role, reads the
read-only token and writes the plan to the job log. Pull requests from forks
do not get a plan.

Approved pull request code can read the production state and the read-only
token, so only approve changes you trust.

Merging to `main` runs the apply job. It assumes the apply role, plans again
and applies that plan. The apply job must not use a GitHub environment: that
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
