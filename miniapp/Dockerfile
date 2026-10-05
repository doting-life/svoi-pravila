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

# Strip ambient capabilities in a disposable stage so the final layer
# never retains apk tools or package caches (see task 0013-a).
# Official image: https://github.com/caddyserver/caddy/releases/tag/v2.11.4
FROM caddy:2.11.4@sha256:0c994536bddb66445885237f1a5dcc1916bccea922661c76b4e9fc24061f9b52 AS stripcaps
RUN apk add --no-cache libcap \
	&& setcap -r /usr/bin/caddy \
	&& apk del libcap

# Alpine final (not `FROM caddy`) so the stripped binary is not layered
# on top of the identical Hub binary (which doubled history size ~45 MB).
FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0

COPY --from=stripcaps /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=stripcaps /usr/bin/caddy /usr/bin/caddy

RUN adduser -D -u 10001 -H -s /sbin/nologin miniapp \
	&& mkdir -p /srv /config/caddy /data/caddy /etc/caddy \
	&& chown -R miniapp:miniapp /srv /config /data

COPY --from=build --chown=miniapp:miniapp /app/dist /srv
COPY --chown=miniapp:miniapp Caddyfile /etc/caddy/Caddyfile

ENV XDG_CONFIG_HOME=/config
ENV XDG_DATA_HOME=/data

USER miniapp
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
