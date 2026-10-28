# Linux (Ubuntu 24.04) deployment snapshot

Live copies from the Linux box as of 2026-09-30. `SETUP.md` is still written for Windows + WSL2.

- `team-config.yaml`: `~/.hermes/profiles/team/config.yaml`. The team profile owns email and cron,
  and its terminal is the Docker sandbox (see `../sandbox/README.md`). It has no secrets; those live
  in the profile's `.env`, which is not committed.
- `systemd/`: user units from `~/.config/systemd/user`, with their `low-memory.conf` drop-ins.
  Install them with `systemctl --user daemon-reload && systemctl --user enable --now <unit>`.
- `sync_allowlist.py`: pushes `EMAIL_ALLOWED_USERS` + `EMAIL_CC_RECIPIENTS` (receive-only, patch 0008) from
  the team `.env` into the n8n send guard.
  `systemd/allowlist-sync.path` runs it automatically whenever the team `.env` changes.
- `../models/free_models.py` (`free-models.timer`, every 3 h): probes the free models on Nous, cross-checks
  OpenRouter's catalog for tool support, and rewrites `model.default` + `fallback_providers` in the team,
  default and bench configs to ones that pass a two-step tool-calling test. It's sticky, never touches a
  paid model, and writes nothing if nothing passes. Results go to `~/.hermes/free_models.json` (the
  learning verifier's judges come from it) and `~/.hermes/logs/free-models.jsonl`. Try it with `--dry-run`.
- `n8n-hermes.service.d/file-access.conf`: limits n8n file reads to the team profile's
  `cache/documents`, the only place `send_email` attachments may come from.
- Google Calendar: `../n8n/workflows/hermes__calendar.json` is a second MCP server at
  `/mcp/hermes-calendar` (MCP server `hermes-calendar` in the team config, same `N8N_MCP_TOKEN`).
  It has list, get, availability, create and update tools on harshilchamp676@gmail.com's calendar. There is
  no delete, and create/update never invite guests or send notifications, because Google invites
  would bypass the `send_email` allowlist. The n8n credential `Google Calendar OAuth2 API` uses an OAuth
  client in Google Cloud project `my-project-12461hermes-n8n`, with redirect
  `http://localhost:5688/rest/oauth2-credential/callback`. The app is in Testing, so Google expires the
  refresh token every 7 days: reconnect the credential in n8n weekly. The calendar ID must be an
  email address; `primary` fails n8n's validation.

Hermes core patches 0001–0013 are in `../hermes-agent-patches/`. The plugins are in
`../hermes-home/plugins/` (beardrive, learning, firecrawl_local). Sandboxed self-hosted Firecrawl is in
`../firecrawl/`. Champ skills sync is in `../champ-skills/`.
