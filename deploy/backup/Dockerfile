# syntax=docker/dockerfile:1.7
# Backup image: PostgreSQL 18 client + age + rclone. No server, no shell history, no secrets.
# age:    https://github.com/FiloSottile/age/releases/tag/v1.3.2 (checksummed release archive)
# rclone: https://github.com/rclone/rclone/releases/tag/v1.75.1 built from the commit-pinned
#         source with gRPC raised to a fixed release: the published v1.75.1 binaries embed a
#         gRPC development snapshot affected by CVE-2026-84445 (image scan gate).

FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS fetch

ARG TARGETARCH
ARG AGE_VERSION=1.3.2
ARG AGE_SHA256_AMD64=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
ARG AGE_SHA256_ARM64=6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4

RUN apk add --no-cache curl

WORKDIR /dl
RUN set -eu; \
	case "${TARGETARCH}" in \
		amd64) age_sha="${AGE_SHA256_AMD64}" ;; \
		arm64) age_sha="${AGE_SHA256_ARM64}" ;; \
		*) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
	esac; \
	age_file="age-v${AGE_VERSION}-linux-${TARGETARCH}.tar.gz"; \
	curl -fsSL -o "${age_file}" \
		"https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/${age_file}"; \
	echo "${age_sha}  ${age_file}" | sha256sum -c -; \
	mkdir -p /out; \
	tar -xzf "${age_file}" -C /out --strip-components=1 age/age age/age-keygen; \
	chmod 0755 /out/age /out/age-keygen

FROM golang:1.26.8-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS rclonebuild

ARG RCLONE_VERSION=1.75.1
ARG RCLONE_COMMIT=687d264b689b8c49a67e2e52a8a5e0caa01c04ce
ARG GRPC_VERSION=1.83.2

RUN apk add --no-cache git
WORKDIR /src
RUN set -eu; \
	git clone --quiet --depth 1 --branch "v${RCLONE_VERSION}" \
		https://github.com/rclone/rclone.git .; \
	test "$(git rev-parse HEAD)" = "${RCLONE_COMMIT}"; \
	go get "google.golang.org/grpc@v${GRPC_VERSION}"; \
	CGO_ENABLED=0 go build -trimpath -tags noselfupdate \
		-ldflags "-s -w -X github.com/rclone/rclone/fs.Version=v${RCLONE_VERSION}" \
		-o /out/rclone .

FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS runtime

RUN apk add --no-cache bash ca-certificates 'postgresql18-client~=18.6' \
	&& adduser -S -D -H -u 10001 -s /sbin/nologin backup

COPY --from=fetch /out/age /out/age-keygen /usr/local/bin/
COPY --from=rclonebuild /out/rclone /usr/local/bin/rclone
COPY backup.sh restore-verify.sh /usr/local/bin/

ENV HOME=/tmp \
	RCLONE_CONFIG=/dev/null \
	RCLONE_CACHE_DIR=/tmp/rclone-cache

USER 10001
WORKDIR /tmp

ENTRYPOINT ["/usr/local/bin/backup.sh"]
