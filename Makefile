.PHONY: preview

.DEFAULT_GOAL := preview

TECH_DOCS_IMAGE ?= ghcr.io/ministryofjustice/tech-docs-github-pages-publisher@sha256:8b00235edfa4d1248e3cdd08c022d5f398c7f4abb3315b6078f1e876214a171e
PORT ?= 4567

preview:
	docker run --rm -it \
		--platform linux/amd64 \
		--publish 127.0.0.1:$(PORT):4567 \
		--volume "$(CURDIR)/config:/tech-docs-github-pages-publisher/config" \
		--volume "$(CURDIR)/source:/tech-docs-github-pages-publisher/source" \
		$(TECH_DOCS_IMAGE) \
		/usr/local/bin/preview