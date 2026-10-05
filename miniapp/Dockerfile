# syntax=docker/dockerfile:1.7

FROM node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS build

ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable && corepack prepare pnpm@12.9.1 --activate

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# ADR-0008: build Caddy v2.11.4 from source on a current Go toolchain.
# https://github.com/caddyserver/caddy/releases/tag/v2.11.4
# xcaddy: https://github.com/caddyserver/xcaddy/releases/tag/v0.4.7
FROM golang:1.26.8-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS caddybuild

RUN apk add --no-cache git ca-certificates \
	&& go install github.com/caddyserver/xcaddy/cmd/xcaddy@v0.4.7

ENV CGO_ENABLED=0
ENV XCADDY_GO_BUILD_FLAGS="-trimpath -ldflags '-w -s'"

WORKDIR /build
# Raise modules that still ship vulnerable in Hub caddy:2.11.4 (0013-a Trivy):
#   golang.org/x/crypto@v0.55.0  — CVE-2026-56854
#   golang.org/x/net@v0.58.0     — CVE-2026-46600 (also required by crypto/grpc)
#   golang.org/x/text@v0.39.0    — CVE-2026-56852
#   google.golang.org/grpc@v1.83.2 — CVE-2026-84304, CVE-2026-84445, GHSA-hrxh-6v49-42gf
RUN mkdir -p /out \
	&& xcaddy build v2.11.4 --output /out/caddy \
		--replace golang.org/x/crypto=golang.org/x/crypto@v0.55.0 \
		--replace golang.org/x/net=golang.org/x/net@v0.58.0 \
		--replace golang.org/x/text=golang.org/x/text@v0.39.0 \
		--replace google.golang.org/grpc=google.golang.org/grpc@v1.83.2

# Writable runtime dirs for Caddy (uid 65532). Static content is prepared separately as root.
FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS rootfs
RUN mkdir -p /config/caddy /data/caddy \
	&& chown -R 65532:65532 /config /data \
	&& chmod 0755 /config /data /config/caddy /data/caddy

# Caddyfile and /srv are root-owned and only readable by the runtime user (0014 Part 0).
FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS static
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
RUN chown -R 0:0 /srv /etc/caddy \
	&& find /srv -type d -exec chmod 0555 {} + \
	&& find /srv -type f -exec chmod 0444 {} + \
	&& chmod 0555 /etc/caddy \
	&& chmod 0444 /etc/caddy/Caddyfile

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

COPY --from=caddybuild /out/caddy /usr/bin/caddy
COPY --from=rootfs --chown=65532:65532 /config /config
COPY --from=rootfs --chown=65532:65532 /data /data
COPY --from=static /srv /srv
COPY --from=static /etc/caddy /etc/caddy

ENV XDG_CONFIG_HOME=/config
ENV XDG_DATA_HOME=/data

USER nonroot
EXPOSE 8080
CMD ["/usr/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
