# Integration Hub PagerDuty business services — proposal

**Status: draft for design review. Do not apply or merge before approval.**

## Proposed scope

Create these three business services in the agreed PagerDuty account:

| Key | Proposed name |
| --- | --- |
| `mft` | Managed File Transfer |
| `api_platform` | API Platform |
| `events_platform` | Events platform |

Terraform creates only these three business services. It does not create
technical-service dependency links, alerts, event integrations, escalation
policies, schedules, or Slack connections. Team assignment is optional until
the Integration Hub PagerDuty team is confirmed.

Create and configure the external status page manually in PagerDuty. Add the
business services to the page manually after they have been created. The page
and its service components are not managed by this Terraform configuration.

## State backend

The S3 backend is declared without account-specific settings. Supply the
approved backend configuration outside the repository when initializing
Terraform, for example with `terraform init -backend-config=backend.hcl`.
Confirm the state account, bucket, region, unique key, KMS key, and locking
approach before creating that file or running `terraform init` with a remote
backend. Do not commit credentials or environment-specific backend values.

The intended backend should use a dedicated state key, encryption, restricted
access, and locking. The existing Integration Hub environment backend is not
assumed to be approved for this PagerDuty state.

## GitHub OIDC deployment design

Before enabling deployment, provision or approve a role specifically for
`ministryofjustice/integration-hub`. Restrict its trust to the repository and
approved GitHub environments, with `sts.amazonaws.com` as the audience. Scope
its permissions to the selected state bucket and KMS key, plus read access to
the PagerDuty secret. Prefer separate plan and apply roles, with apply gated by
a protected GitHub environment and reviewer approval.

The existing application deployment role for the API repository is not assumed
to trust this repository or have the required state and secret permissions.
This proposal does not create or assume any AWS role.

## PagerDuty authentication

Supply `pagerduty_token` at runtime from the approved secret store, for example
as `TF_VAR_pagerduty_token` in a future protected workflow. Never put the token
in Terraform variables files, source control, outputs, or logs. The variable is
sensitive and ephemeral, so Terraform does not persist its value in state or
saved plan files. This is not a substitute for secure runtime secret delivery.

## Decisions required before deployment

- Confirm the PagerDuty account in which the business services will be created.
- Confirm the AWS account and S3/KMS state backend.
- Confirm the PagerDuty secret location and dedicated API token.
- Confirm the GitHub OIDC role(s) and protected-environment approval process.
- Confirm the team ID, if the business services should be assigned to a team.
