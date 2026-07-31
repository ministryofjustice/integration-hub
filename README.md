# Ministry of Justice Integration Hub

[![Ministry of Justice Repository Compliance Badge](https://github-community.service.justice.gov.uk/repository-standards/api/integration-hub/badge)](https://github-community.service.justice.gov.uk/repository-standards/integration-hub)

This repository holds files relevant to the Ministry of Justice Integration Hub.

It contains the [Integration Hub user guide](https://user-guide.integration-hub.service.justice.gov.uk), architectural decision records, and other design artefacts.

It will also serve as a landing page for the team's backlog.

## Publishing the user guide

Pull requests that change `config`, `source`, or the documentation workflow build the guide and check its links. Merges to `main` publish the guide to GitHub Pages.

The custom domain must also be configured in the repository's GitHub Pages settings. Its DNS record is managed separately in [`ministryofjustice/dns`](https://github.com/ministryofjustice/dns) and should be added to `hostedzones/service.justice.gov.uk.yaml` as:

```yaml
user-guide.integration-hub:
	ttl: 300
	type: CNAME
	value: ministryofjustice.github.io
```
