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

FROM caddy:2.10.2@sha256:c3d7ee5d2b11f9dc54f947f68a734c84e9c9666c92c88a7f30b9cba5da182adb

# Drop file capabilities so the binary can run under no-new-privileges as non-root
# (port 8080 does not need CAP_NET_BIND_SERVICE).
RUN apk add --no-cache libcap \
	&& setcap -r /usr/bin/caddy \
	&& apk del libcap \
	&& adduser -D -u 10001 -H -s /sbin/nologin miniapp \
	&& mkdir -p /srv /config/caddy /data/caddy \
	&& chown -R miniapp:miniapp /srv /config /data

COPY --from=build --chown=miniapp:miniapp /app/dist /srv
COPY --chown=miniapp:miniapp Caddyfile /etc/caddy/Caddyfile

USER miniapp
EXPOSE 8080
