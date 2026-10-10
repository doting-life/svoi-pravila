# syntax=docker/dockerfile:1.7
# Prometheus rebuilt from v3.15.0 on Go 1.27.2 with golang.org/x/net@v0.60.0
# so image-scan clears CVE-2026-78667 / CVE-2026-78669 / CVE-2026-97031.
# Upstream prom/prometheus:v3.15.0 embeds Go 1.27.1 and x/net v0.58.0.

FROM golang:1.27.2-alpine@sha256:85dc1069ac644ea3c527b177303a406eb3358192816cd7f9e5848eb658851673 AS build

ARG PROM_VERSION=3.15.0
ARG PROM_COMMIT=5241a27fe3c6983549fccc32f6e65917408c63cd
ARG NET_VERSION=0.60.0

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${PROM_VERSION}" \
		https://github.com/prometheus/prometheus.git .; \
	test "$(git rev-parse HEAD)" = "${PROM_COMMIT}"; \
	go get "golang.org/x/net@v${NET_VERSION}"; \
	CGO_ENABLED=0 go build -trimpath -buildvcs=false -tags netgo,osusergo \
		-ldflags "-s -w" -o /out/prometheus ./cmd/prometheus; \
	CGO_ENABLED=0 go build -trimpath -buildvcs=false -tags netgo,osusergo \
		-ldflags "-s -w" -o /out/promtool ./cmd/promtool

FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0

COPY --from=build /out/prometheus /out/promtool /bin/
COPY prometheus.yml /etc/prometheus/prometheus.yml
# Named volume /prometheus inherits ownership from this directory on first mount.
RUN mkdir -p /prometheus && chown nobody:nobody /prometheus
USER nobody
EXPOSE 9090
VOLUME ["/prometheus"]
ENTRYPOINT ["/bin/prometheus"]
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus"]
