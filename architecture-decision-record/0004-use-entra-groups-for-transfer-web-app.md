# 4. Use Entra Groups for Transfer Web App

Date: 2026-10-20

## Status

✅ Accepted

## Context

We need to manage access to the Transfer Web App in a way that is consistent with our overall identity and access management strategy. Using Entra Groups allows us to centralize and simplify the management of user permissions for this application.

## Decision

We will use Entra Groups to manage access to the Transfer Web App over GitHub-backed groups.

## Consequences

- Groups will be synchronised from Entra into IAM Identity Center on a 2-hourly basis

- Groups will need to be prefixed `azure-aws-sso` in order to be sync'd

- Group management will remain a manual endeavour, but using nested groups will make this easier

- Customers will be restricted to those with a membership in the appropriate Entra Group
