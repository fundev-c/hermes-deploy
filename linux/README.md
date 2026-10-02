# Linux (Ubuntu 24.04) deployment snapshot

Live copies from the Linux box as of 2026-09-30. `SETUP.md` is still written for Windows + WSL2.

- `team-config.yaml`: `~/.hermes/profiles/team/config.yaml`. The team profile owns email and cron,
  and its terminal is the Docker sandbox (see `../sandbox/README.md`). It has no secrets; those live
  in the profile's `.env`, which is not committed.
- `systemd/`: user units from `~/.config/systemd/user`, with their `low-memory.conf` drop-ins.
  Install them with `systemctl --user daemon-reload && systemctl --user enable --now <unit>`.
- `sync_allowlist.py`: pushes `EMAIL_ALLOWED_USERS` from the team `.env` into the n8n send guard.

Hermes core patches 0001–0007 are in `../hermes-agent-patches/`. The plugins are in
`../hermes-home/plugins/` (beardrive, learning, firecrawl_local). Sandboxed self-hosted Firecrawl is in
`../firecrawl/`. Champ skills sync is in `../champ-skills/`.
