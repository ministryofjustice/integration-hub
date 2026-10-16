# Kong Evaluation

## 1. The Integration Hub

MoJ teams have built integrations in different ways. This leads to duplicated effort, inconsistent security and controls, and integrations that are hard to operate and change.

The Integration Hub (IH) provides shared, reusable integration capabilities that teams can use instead of building their own. Teams keep ownership of their systems, data and integration logic. The IH team owns and operates the shared platform.

Current capabilities:

- **Managed File Transfer:** SFTP/FTPS into S3. 
- **API integration:** publish and consume APIs using shared capabilities and consistent patterns.

Planned: event streaming and other integration patterns.

## 2. The API integration capability

Agencies build APIs independently, giving inconsistent security, duplicated effort, no central monitoring or governance, and no lifecycle management.

The goal is a shared platform where:

- **API providers** publish their APIs and follow common standards (auth, OpenAPI specs, CI/CD) instead of rebuilding them.
- **API consumers** discover APIs and request access in one place.
- Every call is authenticated, limited, logged and traceable in the same way.

## 3. What the platform needs

**API providers**

- P1. Put an API on the platform quickly and the same way every time.
- P2. Not build authentication, rate limiting and logging themselves.
- P3. Change or retire an API without breaking consumers.

**API consumers**

- C1. Find which APIs exist and request access in one place.
- C2. Authenticate the same way for every API.

**Integration Hub team**

- H1. Run the platform with a small team: deploy, upgrade and recover predictably.
- H2. See what is happening across all APIs.
- H3. Manage everything as code, through pipelines.

**MoJ security and governance**

- G1. Know who called what, and when.
- G2. Revoke access centrally and quickly.
- G3. Apply the same controls to every API.
- G4. Protect OFFICIAL and OFFICIAL-SENSITIVE data.

## 4. Where the API gateway fits

An API gateway is a shared front door. Every call passes through it, so authentication, limits and logging happen once, in one place, instead of in each team's code.

```
Consumer -> API gateway -> Provider's backend API
             checks identity, applies limits, logs the call, routes it
```

The gateway is the enforcement point for needs such as authentication, rate limiting, logging and routing. Publishing workflows, discovery and the portal sit around it.

## 5. Options for the gateway

| Option | Summary | Notes |
|---|---|---|
| Kong (open source) | Gateway we run ourselves on Kubernetes. Plugin ecosystem. | Candidate under review; tested locally |
| Kong Konnect | Kong-hosted control plane with Dev Portal, catalogue, analytics; data planes self-hosted or Kong-hosted. | Priced; not tested |
| Other self-run gateways (for example Envoy-based) | Similar trade-offs to Kong. | Not yet assessed |
| Build our own | Full control, full maintenance. | Unlikely |

This evaluation is about Kong only. It covers the open-source gateway and the hosted Konnect option.

## 6. Tests

This section records how Kong was tested. It is detail for anyone who wants to know how we arrived at the research conclusions. To skip it, go to [section 7](#7-findings-why-kong-or-why-not) for the findings, which summarise everything here.

- **Phase 1, local container:** one operator, two sample backends. Checks that Kong meets the basic needs from section 3.
- **Phase 2, Container Platform:** checks that Kong holds up when run as the Integration Hub would run it: several replicas, heavy load and failures. Kong's own features are not re-tested here.

Status: ⬜ not run, 🔄 in progress, ✅ pass, ❌ fail, 🔶 partial. Where relevant, record whether the capability works in OSS, needs a plugin, needs Enterprise or Konnect, or is not covered.

<details>
<summary><b>Phase 1: local container</b></summary>

| Need | Test | Pass criteria | Status | Observed |
|---|---|---|---|---|
| P1, H3 | Add a second backend by editing config only | Live with no gateway code and one config file | ✅ | Every API is onboarded the same way, through one service and route entry in the config file, with no gateway code. |
| P2, C2 | Call with no credential, then a valid key, then a JWT | 401 without, 200 with, same method on both APIs | ✅ | API key and JWT authentication can be applied to any API; OIDC, key rotation and token issuing were not tested. |
| G2 | Remove a consumer | Its calls stop immediately | ✅ | Access can be revoked centrally by a config change that takes effect immediately, without a restart. |
| G3, P2 | Apply one rate limit and one logging rule to both APIs | Both enforce it (429 over the limit) | ✅ | One rate-limit rule covers every API and is counted per consumer, but counts are per gateway instance, so running several replicas needs a shared store. |
| G1 | Inspect the log of a call | Shows consumer, API and time | ✅ | Calls are traceable to a consumer and API, including rejected ones. |
| H2 | Read the metrics endpoint | Request counts and errors per API | ✅ | Request counts by API, consumer and status code can be produced. Dashboards are ours to build. |
| C1 | Check the free edition for a portal or catalogue | Yes or no, and what the paid edition adds | ✅ | The open-source gateway has no portal or catalogue. These are Konnect features. |
| H1 | Run Kong under the chart's security settings, with memory and CPU limits, under load | Stays up; memory and CPU measured | ✅ | Kong can run within the platform's security restrictions and limits: ~112MiB idle, ~172MiB at ~3,600 req/s, with no errors. |

</details>

<details>
<summary><b>Phase 2: Container Platform (scalability)</b></summary>

| Need | Test | Pass criteria | Status | Observed |
|---|---|---|---|---|
| H1 | Throughput and latency under load through `/ping` | Requests per second and latency measured, with no errors | 🔶 | With 2 replicas, a short sweep at 1, 5, 20, 50, 100 and 200 concurrent clients returned only 200s, at about 30, 150, 558, 1,279, 2,148 and 3,754 requests/sec respectively. At 200 clients, p50 latency was 44 ms. A separate run with three load generators at 200 clients each reached about 5,300 requests/sec, all 200. These are preliminary public-route measurements from one client machine, not a capacity benchmark; pod CPU and memory were unavailable. |
| H1 | Compare 1, 2 and 4 replicas under the same load | Throughput grows with replicas | 🔶 | The deployment stayed at 2 replicas: ArgoCD reconciled direct scale changes before a valid 1- or 4-replica measurement could be taken. No replica comparison was established. |
| H1 | Gateway overhead: `/ping` against a proxied API | Added latency measured | ⬜ | Not measured: the available proxied API is rate-limited, so it would not give a controlled latency comparison with `/ping`. |
| H1 | Overload: push past capacity | Fails cleanly and recovers with no manual action | 🔶 | At four load generators with 300 concurrent clients each, many requests ended in client-side TLS EOF errors and p95 latency was about 700 ms. The endpoint and Kubernetes DNS then became unreachable from the test machine, so the errors could not be attributed to Kong or the platform. `/ping` later returned 200 again; the overload limit and recovery behaviour remain unknown. |
| H1 | Scale out and in during load | No failed requests | 🔶 | Not completed: ArgoCD reset direct replica changes to 2 before a scale event could be tested under load. |
| G3 | Rate limit with several replicas under load | One limit across all instances | 🔶 | Counts are kept per instance and per clock minute, so the limit is not shared: 13 calls passed against a limit of 5 in the original check. A 65-second burst at 20 concurrent clients produced 21 responses with 200 and 38,675 with 429. A shared store is needed for one limit across replicas. |
| H1 | Restart pods while under load | Service recovers without manual action | 🔶 | Deleting a pod caused no failed requests (200 of 200). After adding a 10-second pre-stop delay and 45-second termination grace, two 75-second rolling-restart runs at 10 concurrent clients completed about 23,200 requests each with no errors. This is encouraging but too few runs to establish that the change fixed the earlier intermittent failure (about 1 in 300, twice). |
| H2 | Metrics and logs across replicas | Usable as a whole, not per pod | 🔶 | Logs and per-consumer metrics are available per pod and counted separately on each pod. Central collection and pod resource use could not be checked. |
| H1 | Soak: steady load for an extended period | No memory growth or errors | 🔶 | A 5-minute run at 10 concurrent clients completed 84,563 requests (about 282 requests/sec), all 200; p95 latency was 49 ms. This short run cannot establish long-term memory stability. |
| P1 | Scale in the number of APIs and consumers | Startup time and latency stay acceptable | ⬜ | |

</details>

## 7. Findings

Kong Gateway OSS provides the core gateway capabilities IH needs: routing, API-key/JWT authentication, access control, rate limiting, logging and metrics. The trade-off is that IH would need to provide the surrounding management layer: a developer portal and catalogue, shared rate limits across replicas, central dashboards and a multi-team configuration workflow. Kong's hosted enterprise option, Konnect, on the other hand, provides a managed control plane with a developer portal, API catalogue and analytics.

### Findings by need

| Need | Kong Gateway OSS (prototype) | Kong Konnect (not yet tested) |
|---|---|---|
| P1, H3 Add an API from config | One service and route entry per API, held in git | Uses Gateway data planes; Konnect provides hosted management |
| P2, C2 Authentication | API key, JWT and ACL tested. JWT expiry checking must be enabled; OIDC and token issuance were not tested | OIDC/SAML plugins and Kong Identity are vendor-described; confirm applicable plan |
| P2, G3 Rate limiting | Per-consumer limits work, but are counted separately per replica; shared Redis is needed for a cluster-wide limit | Advanced rate limiting is vendor-described; not evaluated |
| G1 Who called what | File logging can record consumer and API; central log collection remains ours to configure | Analytics are provided by Konnect; not evaluated |
| H2 Visibility | Prometheus metrics are available per pod; aggregation and dashboards remain ours to build | Built-in analytics are vendor-described; not evaluated |
| G2 Revoke access | Config changes revoked access locally without restart; on the Container Platform, pods needed restarting to load changed config | Managed configuration; revocation behaviour not evaluated |
| C1 Portal and catalogue | Not included in OSS | Developer Portal and API Catalog are available |
| H1 Running it | Runs on the Container Platform; IH operates and patches the Gateway | Kong hosts the control plane; IH operates the data planes |
| G3 RBAC, SSO, audit log | Not included in OSS; configuration access is managed through Git and platform permissions | Enterprise-tier capabilities; exact plan not evaluated |

### What we would build or run with open-source Kong

1. A portal and an access-request flow. Kong open source has none.
2. A shared store (Redis) for rate limits, once there is more than one instance.
3. Logging and metrics pipelines and dashboards. Kong produces the records only.
4. Secrets handling for keys and JWT secrets.
5. A config workflow for multiple teams.

## 8. Cost comparison (OSS kong vs. Konnect)

TBD

## 9. Open questions

- Which use cases and capabilities are most important to evaluate?
- If we proceed, are we considering self-managed Kong Gateway Enterprise or Kong Konnect?
- What is the time box, and how long should the prototype remain available?

## 10. Recommendation

TBD, after the local trial and cluster findings.
