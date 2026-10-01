#!/usr/bin/env bash
# Start (or restart) the sandboxed self-hosted Firecrawl. Idempotent. Secrets are generated once
# into ~/.config/firecrawl-local.env (0600) and never committed.
set -euo pipefail
cd "$(dirname "$0")"
ENVF="${XDG_CONFIG_HOME:-$HOME/.config}/firecrawl-local.env"
if [ ! -f "$ENVF" ]; then
  umask 077
  printf 'FC_POSTGRES_PASSWORD=%s\nFC_BULL_AUTH_KEY=%s\n' "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" > "$ENVF"
fi
docker compose --env-file "$ENVF" up -d --remove-orphans "$@"
for _ in $(seq 1 90); do
  curl -fsS -m 3 http://127.0.0.1:3002/ >/dev/null 2>&1 && { echo "firecrawl-local up on 127.0.0.1:3002"; exit 0; }
  sleep 2
done
echo "firecrawl-local did not answer on 127.0.0.1:3002; see: docker compose logs api" >&2
exit 1
