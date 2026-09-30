#!/usr/bin/env bash
# Build the sandbox image and (re)create the egress proxy + networks. Idempotent.
set -euo pipefail
cd "$(dirname "$0")"
docker build -q -t hermes-sandbox:1 .
docker network inspect hermes-sbx >/dev/null 2>&1 || docker network create --internal hermes-sbx
docker network inspect hermes-out >/dev/null 2>&1 || docker network create hermes-out
docker rm -f hermes-egress >/dev/null 2>&1 || true
docker run -d --name hermes-egress --restart unless-stopped \
  --network hermes-out --memory 96m --pids-limit 64 --cap-drop ALL --cap-add SETUID --cap-add SETGID \
  --security-opt no-new-privileges --read-only --tmpfs /var/spool/squid --tmpfs /var/run --tmpfs /var/log/squid:uid=13,gid=13 \
  -v "$PWD/squid.conf:/etc/squid/squid.conf:ro" \
  ubuntu/squid@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029 >/dev/null
docker network connect hermes-sbx hermes-egress
echo "hermes-sandbox:1 built; hermes-egress running on hermes-sbx"
