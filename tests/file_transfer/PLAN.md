## Purpose

Show, before every promotion to production, that each supported file-transfer route delivers files intact, that invalid requests are rejected, and that failures are alerted and recoverable. Results must be usable as evidence on a pull request.

The L1 smoke check (see [flowcharts.md](flowcharts.md)) covers one route and
the happy path. The current L2 check adds integrity fixtures for that same
route, but does not yet cover the full route/scenario matrix. This plan
describes how to grow the checks into a production-grade system.

## Scope

Routes in scope (confirm at the first review):

- `push-to-s3`
- `push-to-s3-with-hosted-pickup`
- `pull-from-presigned-url`
- SFTP and FTPS through AWS Transfer Family with the custom identity provider
- Transfer web app

All tests run against the file-transfer **test** account with synthetic data only.

## Test layers

| Layer | Checks | When | Gating |
|---|---|---|---|
| L0 Harness unit tests | The harness's own logic with mocked AWS | Every PR | Yes |
| L1 Smoke | One happy path per route | After each test deployment | Yes |
| L2 Functional | Route and scenario matrix, including negative paths | After each test deployment | Yes, for P0 scenarios |
| L3 Resilience and security | DLQ and replay, retries, concurrency, tenant isolation | Nightly and before release | Release gate |
| L4 Non-functional | Latency and throughput baselines, large files | Weekly or on demand | Informational until baselines are agreed |
| L5 Production canary | One tiny synthetic file on a schedule | Production | Monitoring only |

## Scenario matrix

The matrix is the coverage record. Each row has an ID, route, case, expected result, priority (P0 gating, P1, P2) and automation status. It lives alongside the tests and is updated with every new route or scenario.

Cases per route:

- **Integrity:** small, zero-byte, multi-MB and multipart files; binary content; special characters in names; checksum match.
- **Delivery:** correct destination path, metadata and tags; encryption with the expected KMS key; delivery within a latency threshold.
- **Negative:** unauthorised user or wrong prefix; expired or reused presigned URL; oversize or disallowed file; unavailable destination. Each must be rejected or quarantined and alert as designed.
- **Behaviour:** duplicate uploads, concurrent uploads, retries.
- **Isolation:** one tenant cannot read another's data.
- **Observability:** logs, EventBridge events, SNS, email and PagerDuty paths fire when expected.
- **Failure handling:** a failed message reaches the DLQ and the documented replay procedure works.

## Harness design

- Rebuild as a pytest suite using boto3 in place of the AWS CLI subprocess calls.
- Proposed layout: `tests/file_transfer/{routes,scenarios,fixtures,lib}`. Configuration comes from environment variables, with no hard-coded resource names.
- Each scenario gets a unique run ID, its own timeout, and teardown. Lifecycle rules on test prefixes act as a safety net.
- Polling with bounded timeouts. Retries are allowed only for known eventual consistency and never to mask real failures.
- Reports: JUnit XML and JSON, a per-route GitHub step summary, uploaded artifacts, and a run record with the Modernisation Platform SHA, the integration-hub SHA and the run ID.
- Flake control: quarantine tag, flake-rate tracking, and a fix-or-remove deadline.
- The harness is itself unit tested (L0) so its pass and fail logic stays trustworthy.

## Infrastructure and security

- Keep the dedicated, minimally scoped role. Extend its policy per route, limited to harness prefixes and reviewed on each addition.
- Keep harness resources gated to the test workspace. Confirm from plans that dev and prod get none.
- Synthetic, generated data only. No secrets in logs. Keep action references pinned to SHAs.
- Keep the check that the AWS identity is the expected role in the expected account.

## Triggering and gating

- Trigger automatically after candidate changes are deployed to test, carrying the Modernisation Platform SHA. Mechanism to be agreed (for example `repository_dispatch` from the deployment workflow, or a reusable workflow called from it). `workflow_dispatch` remains as a manual rerun that takes the SHA as input.
- Use a concurrency group so only one run uses the test environment at a time.
- Report the result back to the change under test as a required status check.
- Schedule L3 nightly and L4 weekly.
- Document which PRs require passing tests, how candidates reach the test environment, and where the result is recorded before merge.

## Notifications

- Notify on pass, fail and not-completed. A run that does not complete counts as a failure.
- Content: result, route and scenario, Modernisation Platform change, who triggered it, and a link to the run.
- Destination, success versus failure paths and PagerDuty use are to be agreed together with [#231](https://github.com/ministryofjustice/integration-hub/issues/231) so both share one approach.

## Phasing

1. **Foundation:** agree the scenario matrix and P0 scope. Refactor to pytest and boto3 with configuration and reporting, keeping the current test as the first scenario.
2. **P0 routes:** integrity and delivery for every route plus core negative cases. Wire up the trigger, status check and notifications.
3. **Resilience and security:** DLQ and replay, concurrency, isolation, alerting.
4. **Non-functional and canary:** latency baselines, large files, production canary.
5. **Ongoing:** flake review, and a rule that a new route cannot ship without scenarios.

## Definition of done

- All P0 scenarios are automated and gating, and the suite finishes in about 15 minutes.
- The flake rate stays below an agreed threshold (for example 1%) over a month.
- Every failure gives a clear message and an alert.
- Documentation and the runbook are updated, and a new scenario can be added in under an hour.

## Open questions

- Is a production canary allowed, and under what data rules?
- Who owns the notification destination?
- What latency and throughput targets apply?
- Which routes are in scope at launch?
- Which trigger mechanism does the team prefer?
