.PHONY: preview link-check

.DEFAULT_GOAL := preview

TECH_DOCS_IMAGE ?= ghcr.io/ministryofjustice/tech-docs-github-pages-publisher@sha256:8b00235edfa4d1248e3cdd08c022d5f398c7f4abb3315b6078f1e876214a171e
LYCHEE_IMAGE ?= docker.io/lycheeverse/lychee:0.24.2
PORT ?= 4567

preview:
	docker run --rm -it \
		--platform linux/amd64 \
		--publish 127.0.0.1:$(PORT):4567 \
		--volume "$(CURDIR)/config:/tech-docs-github-pages-publisher/config" \
		--volume "$(CURDIR)/source:/tech-docs-github-pages-publisher/source" \
		$(TECH_DOCS_IMAGE) \
		/usr/local/bin/preview

link-check:
	docker run --rm \
		--volume "$(CURDIR):/work:ro" \
		--workdir /work \
		$(LYCHEE_IMAGE) \
		--verbose --no-progress './**/*.md' './**/*.html' './**/*.erb' \
		--accept 403,200,429 \
		--exclude '^https://github-community\.service\.justice\.gov\.uk/repository-standards/.*$$'