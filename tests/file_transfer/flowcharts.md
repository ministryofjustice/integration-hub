## Planned Flow: Testing Infrastructure Changes

Automatically check that infrastructure changes haven’t broken the file-transfer service before those changes reach production.

```mermaid
flowchart TD
  change["Open an infrastructure PR in Modernisation Platform"]
  change --> deploy["Deploy candidate changes to the test environment"]
  deploy --> trigger["Automatically trigger the Integration Hub harness"]
  trigger --> scenarios["Test supported transfer routes and selected edge cases"]
  scenarios --> report["Report results and notify the team"]
  report --> review{"All required checks passed?"}
  review -->|Yes| approve["Review evidence before approving promotion to production"]
  review -->|No or incomplete| investigate["Investigate, fix and rerun"]
  investigate --> deploy
```

## Current Flow

Verify that a file uploaded to MFT reaches the expected destination unchanged.

```mermaid
flowchart TD
  prepare["Create a unique test file"] --> upload["Upload to MFT's incoming S3 bucket"]
  upload --> deliver["MFT processes and delivers the file"]
  deliver --> verify["Confirm the file arrived at the expected destination unchanged"]
```

See the [running instructions](../../README.md#file-transfer-smoke-test) for details.