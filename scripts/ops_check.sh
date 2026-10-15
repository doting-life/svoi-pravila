#!/usr/bin/env bash
# Validate the Prometheus and Alertmanager configuration with the pinned images:
# promtool check config / check rules / test rules, amtool check-config and a render of the
# Telegram template. Alertmanager reads its secrets from files, so throwaway ones are created in a
# temporary directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/image-pins.env
source "$ROOT/scripts/image-pins.env"
: "${PROMETHEUS_IMAGE:?PROMETHEUS_IMAGE is unset}"
: "${ALERTMANAGER_IMAGE:?ALERTMANAGER_IMAGE is unset}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '%s' '000000:throwaway' >"$tmp/bot_token"
printf '%s' '-1000000000000' >"$tmp/chat_id"
chmod 0644 "$tmp/bot_token" "$tmp/chat_id"

promtool() {
  docker run --rm --entrypoint promtool \
    -v "$ROOT/ops/prometheus:/etc/prometheus:ro" \
    "$PROMETHEUS_IMAGE" "$@"
}

amtool() {
  docker run --rm --entrypoint amtool \
    -v "$ROOT/ops/alertmanager:/etc/alertmanager:ro" \
    -v "$tmp:/etc/svoi-pravila/alertmanager:ro" \
    "$ALERTMANAGER_IMAGE" "$@"
}

promtool check config /etc/prometheus/prometheus.yml
promtool check rules /etc/prometheus/rules/svoi.yml
promtool test rules /etc/prometheus/rules/svoi.test.yml
amtool check-config /etc/alertmanager/alertmanager.yml
amtool template render --template.glob=/etc/alertmanager/telegram.tmpl \
  --template.text='{{ template "sp.telegram" . }}' >/dev/null
echo "ops-check: ok"
