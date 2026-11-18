# Integration Hub PagerDuty business services

The GitHub Actions workflow runs Terraform plans only. It does not apply changes.

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
12-digit production AWS account ID. The workflow uses OIDC to assume the
existing `role/github-actions/integration-hub-pagerduty` role in that account.

The API token is stored in `pagerduty/api-key` in AWS Secrets Manager.
When replacing it, an authorised person should store just the token on one
line, not JSON.

The workflow reads the token only when planning. Do not put it in GitHub,
code, variables files or logs. Terraform marks it sensitive and ephemeral,
so its value is not saved in state or plan files.

## Running a plan

Pull requests run formatting and validation without AWS access.

Open **Actions > PagerDuty Terraform > Run workflow** in GitHub and select
`main`. Review the plan in the job log. Production plans run only when
started manually.

The AWS role trusts jobs on `main`. Do not add a GitHub environment to the
plan job: that changes the OIDC subject and prevents it from assuming the role.
Keep `main` protected.

Check for existing business services before applying changes to avoid creating
duplicates. Applying changes needs separate approval.

## Terraform state

State uses `pagerduty/terraform.tfstate` in the
`integration-hub-production-pagerduty-tfstate` bucket in `eu-west-2`.
Use the default workspace. The backend uses KMS encryption and S3 locking.
The backend uses `alias/s3/integration-hub-production-pagerduty-tfstate`
directly as its KMS key identifier. The alias must point to the existing state
key in the production account in `eu-west-2`. This is not the key used for
the API token. No separate KMS lookup or `kms:DescribeKey` permission is needed.
