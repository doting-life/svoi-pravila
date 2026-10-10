# syntax=docker/dockerfile:1.7
# Grafana OSS with Postgres and Prometheus datasources baked in (no runtime download).
# Pins: see ops/grafana/image-pins.env (enforced by toolchain-check).
# Plugins live under /usr/share/grafana/plugins so the data volume on
# /var/lib/grafana cannot hide them.
#
# ADR-0008-style: rebuild the Grafana server binary and plugin gobinaries on
# Go 1.27.2 with golang.org/x/net@v0.60.0 so image-scan clears
# CVE-2026-78667 / CVE-2026-78669 / CVE-2026-97031. Frontend assets stay from
# the digest-pinned upstream slim image; only Go binaries are replaced.

ARG GO_IMAGE=golang:1.27.2-alpine@sha256:85dc1069ac644ea3c527b177303a406eb3358192816cd7f9e5848eb658851673
ARG NET_VERSION=0.60.0

FROM ${GO_IMAGE} AS grafanabuild

ARG TARGETARCH
ARG GRAFANA_VERSION=13.2.3
ARG GRAFANA_COMMIT=6193dc03311b631b9727b560d24369e683dc396e
ARG NET_VERSION

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${GRAFANA_VERSION}" \
		https://github.com/grafana/grafana.git .; \
	test "$(git rev-parse HEAD)" = "${GRAFANA_COMMIT}"; \
	go get "golang.org/x/net@v${NET_VERSION}"; \
	CGO_ENABLED=0 go build -trimpath -buildvcs=false \
		-ldflags "-s -w -X main.version=${GRAFANA_VERSION} -X main.commit=${GRAFANA_COMMIT} -X main.buildBranch=v${GRAFANA_VERSION}" \
		-o /out/grafana ./pkg/cmd/grafana

FROM ${GO_IMAGE} AS pgpluginbuild

ARG TARGETARCH
ARG GRAFANA_PG_PLUGIN_VERSION=13.0.4
ARG GRAFANA_PG_PLUGIN_COMMIT=4d167c25053dc0444178451abf8a3f171e9e4471
ARG NET_VERSION

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${GRAFANA_PG_PLUGIN_VERSION}" \
		https://github.com/grafana/grafana-postgresql-datasource.git .; \
	test "$(git rev-parse HEAD)" = "${GRAFANA_PG_PLUGIN_COMMIT}"; \
	go get "golang.org/x/net@v${NET_VERSION}"; \
	CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags "-s -w" \
		-o "/out/gpx_grafana_postgresql_datasource_linux_${TARGETARCH}" ./pkg

FROM ${GO_IMAGE} AS prompluginbuild

ARG TARGETARCH
ARG GRAFANA_PROM_PLUGIN_VERSION=13.2.3
ARG GRAFANA_PROM_PLUGIN_COMMIT=6d21418acde6ce31c9f7a6418a88758842a881bb
ARG NET_VERSION

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${GRAFANA_PROM_PLUGIN_VERSION}" \
		https://github.com/grafana/grafana-prometheus-datasource.git .; \
	test "$(git rev-parse HEAD)" = "${GRAFANA_PROM_PLUGIN_COMMIT}"; \
	go get "golang.org/x/net@v${NET_VERSION}"; \
	CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags "-s -w" \
		-o "/out/gpx_grafana-prometheus-datasource_linux_${TARGETARCH}" ./pkg

FROM grafana/grafana:13.2.3-slim@sha256:7c2b05e94eb43e2b88aa814d7a1c9421affc7f3593a951148177f29feaff6017

USER root

ARG TARGETARCH
ARG GRAFANA_PG_PLUGIN_URL=https://grafana.com/api/plugins/grafana-postgresql-datasource/versions/13.0.4/download
ARG GRAFANA_PG_PLUGIN_SHA256=e0c9f7b560229fd78b701fa803552127db4aab467bb036d506d8d124c62c7feb
ARG GRAFANA_PROM_PLUGIN_URL=https://grafana.com/api/plugins/prometheus/versions/13.2.3/download
ARG GRAFANA_PROM_PLUGIN_SHA256=e0871397ef37dcfe76dd14ab534256acaf7f57caefe370fc19e55b8496a423b8

ENV GF_PATHS_PLUGINS=/usr/share/grafana/plugins
# Rebuilt plugin gobinaries invalidate Grafana's signed MANIFEST; allow these
# two baked-in datasources only (ADR-0008-style rebuild above).
ENV GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=grafana-postgresql-datasource,prometheus

RUN apk add --no-cache curl unzip \
	&& mkdir -p /usr/share/grafana/plugins \
	&& curl -fsSL -o /tmp/grafana-postgresql-datasource.zip "${GRAFANA_PG_PLUGIN_URL}" \
	&& echo "${GRAFANA_PG_PLUGIN_SHA256}  /tmp/grafana-postgresql-datasource.zip" | sha256sum -c \
	&& unzip -q /tmp/grafana-postgresql-datasource.zip -d /usr/share/grafana/plugins \
	&& rm -f /tmp/grafana-postgresql-datasource.zip \
	&& curl -fsSL -o /tmp/grafana-prometheus-datasource.zip "${GRAFANA_PROM_PLUGIN_URL}" \
	&& echo "${GRAFANA_PROM_PLUGIN_SHA256}  /tmp/grafana-prometheus-datasource.zip" | sha256sum -c \
	&& unzip -q /tmp/grafana-prometheus-datasource.zip -d /usr/share/grafana/plugins \
	&& rm -f /tmp/grafana-prometheus-datasource.zip \
	&& find /usr/share/grafana/plugins -type f \( \
		-name '*_darwin_*' -o -name '*_windows_*' -o -name '*.exe' \
		-o -name '*_linux_amd64' -o -name '*_linux_arm' -o -name '*_linux_arm64' \
		-o -name '*_linux_s390x' \
	\) -delete \
	&& apk del curl unzip

COPY --from=grafanabuild /out/grafana /usr/share/grafana/bin/grafana
COPY --from=pgpluginbuild /out/gpx_grafana_postgresql_datasource_linux_${TARGETARCH} \
	/usr/share/grafana/plugins/grafana-postgresql-datasource/gpx_grafana_postgresql_datasource_linux_${TARGETARCH}
COPY --from=prompluginbuild /out/gpx_grafana-prometheus-datasource_linux_${TARGETARCH} \
	/usr/share/grafana/plugins/prometheus/gpx_grafana-prometheus-datasource_linux_${TARGETARCH}

RUN chmod 0755 /usr/share/grafana/bin/grafana \
		/usr/share/grafana/plugins/grafana-postgresql-datasource/gpx_grafana_postgresql_datasource_linux_${TARGETARCH} \
		/usr/share/grafana/plugins/prometheus/gpx_grafana-prometheus-datasource_linux_${TARGETARCH} \
	&& rm -f \
		/usr/share/grafana/plugins/grafana-postgresql-datasource/MANIFEST.txt \
		/usr/share/grafana/plugins/prometheus/MANIFEST.txt \
	&& chown -R 472:472 /usr/share/grafana/plugins \
	&& chown 472:472 /usr/share/grafana/bin/grafana

USER 472
