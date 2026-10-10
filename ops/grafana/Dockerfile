# syntax=docker/dockerfile:1.7
# Grafana OSS with Postgres and Prometheus datasources baked in (no runtime download).
# Pins: see ops/grafana/image-pins.env (enforced by toolchain-check).
# Plugins live under /usr/share/grafana/plugins so the data volume on
# /var/lib/grafana cannot hide them.
FROM grafana/grafana:13.2.3-slim@sha256:7c2b05e94eb43e2b88aa814d7a1c9421affc7f3593a951148177f29feaff6017

USER root

ARG GRAFANA_PG_PLUGIN_URL=https://grafana.com/api/plugins/grafana-postgresql-datasource/versions/13.0.4/download
ARG GRAFANA_PG_PLUGIN_SHA256=e0c9f7b560229fd78b701fa803552127db4aab467bb036d506d8d124c62c7feb
ARG GRAFANA_PROM_PLUGIN_URL=https://grafana.com/api/plugins/prometheus/versions/13.2.3/download
ARG GRAFANA_PROM_PLUGIN_SHA256=e0871397ef37dcfe76dd14ab534256acaf7f57caefe370fc19e55b8496a423b8

ENV GF_PATHS_PLUGINS=/usr/share/grafana/plugins

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
	&& chown -R 472:472 /usr/share/grafana/plugins \
	&& apk del curl unzip

USER 472
