# syntax=docker/dockerfile:1.7
# Backup image: PostgreSQL 18 client + age + rclone. No server, no shell history, no secrets.
# age:    https://github.com/FiloSottile/age/releases/tag/v1.3.2 built from the commit-pinned
#         source on Go 1.26.9 (published binaries embed Go 1.27.0 stdlib CVEs).
# rclone: https://github.com/rclone/rclone/releases/tag/v1.75.2 built from the commit-pinned
#         source with gRPC and x/net raised to fixed releases (image scan gate).

FROM golang:1.26.9-alpine@sha256:cdfd4fe2da6b225d8b40c6b7a105736e548e83ff56d5d8f9394446eeb5eb84e0 AS agebuild

ARG AGE_VERSION=1.3.2
ARG AGE_COMMIT=b74dce4cdbe35b5e5f66c06d9612b72f89028758

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${AGE_VERSION}" \
		https://github.com/FiloSottile/age.git .; \
	test "$(git rev-parse HEAD)" = "${AGE_COMMIT}"; \
	CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/age ./cmd/age; \
	CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/age-keygen ./cmd/age-keygen

FROM golang:1.26.9-alpine@sha256:cdfd4fe2da6b225d8b40c6b7a105736e548e83ff56d5d8f9394446eeb5eb84e0 AS rclonebuild

ARG RCLONE_VERSION=1.75.2
ARG RCLONE_COMMIT=be8d6044eacb843b74d335db0c5fbe09222031fe
ARG GRPC_VERSION=1.83.2
ARG NET_VERSION=0.60.0

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${RCLONE_VERSION}" \
		https://github.com/rclone/rclone.git .; \
	test "$(git rev-parse HEAD)" = "${RCLONE_COMMIT}"; \
	go get "google.golang.org/grpc@v${GRPC_VERSION}"; \
	go get "golang.org/x/net@v${NET_VERSION}"; \
	CGO_ENABLED=0 go build -trimpath -tags noselfupdate \
		-ldflags "-s -w -X github.com/rclone/rclone/fs.Version=v${RCLONE_VERSION}" \
		-o /out/rclone .

FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS runtime

RUN apk add --no-cache bash ca-certificates 'postgresql18-client~=18.6' \
	&& adduser -S -D -H -u 10001 -s /sbin/nologin backup

COPY --from=agebuild /out/age /out/age-keygen /usr/local/bin/
COPY --from=rclonebuild /out/rclone /usr/local/bin/rclone
COPY backup.sh restore-verify.sh /usr/local/bin/

ENV HOME=/tmp \
	RCLONE_CONFIG=/dev/null \
	RCLONE_CACHE_DIR=/tmp/rclone-cache

USER 10001
WORKDIR /tmp

ENTRYPOINT ["/usr/local/bin/backup.sh"]
