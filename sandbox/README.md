# Sandboxed terminal for the team (email/cron) profile

Since 2026-09-30, email and cron are served from the Hermes profile `team`
(`~/.hermes/profiles/team`), and their `terminal` tool runs inside Docker, not on the host.
Your own `hermes` CLI (the default profile) is unchanged.

## Pieces

| Piece | What it does |
|---|---|
| `hermes-sandbox:1` (`Dockerfile`) | Python 3.12 + pandas/openpyxl/numpy/xlrd, node, git. Runs as uid 1000, root fs read-only. `/workspace` and `/root` are per-session tmpfs, so nothing survives a session. |
| `hermes-sbx` network | `--internal`: no route out, no DNS for outside names. |
| `hermes-egress` (squid, `squid.conf`) | The only way out. It allows HTTPS CONNECT to PyPI, npm and GitHub. It denies every other site and any private or loopback address. |
| `drive_to_sandbox` / `sandbox_to_drive` | The only way files move between BearDrive and the sandbox (beardrive plugin). |
| Patch 0002 | Stops a cron job's own `enabled_toolsets` from widening past `platform_toolsets.cron`. |

The sandbox mounts nothing from the host except read-only copies of the profile's skills and
the attachment caches (Hermes does this itself). It gets no host env and no secrets.

Apt is not usable inside the sandbox (not root, read-only root fs), so apt mirrors are not allowlisted.

## Bring up / rebuild

```bash
~/Hermes_agent/hermes-deploy/sandbox/up.sh   # builds the image, (re)creates networks + proxy
systemctl --user restart hermes-gateway
```

The proxy runs with `--restart unless-stopped`, so it comes back with Docker.

## Checks

```bash
cd ~/.hermes/hermes-agent
venv/bin/python -m pytest -q -p no:cacheprovider tests/security/test_beardrive_cross_tool_containment.py \
  tests/cron/test_cron_job_toolsets_clamped.py
```

The containment suite fails if the team terminal stops being the locked sandbox. That covers
a local backend, host volumes, forwarded env, no `--read-only`, the wrong network, or a
sandbox shared across sessions.

Host services must listen on 127.0.0.1 only. The sandbox reaches the host through the
network gateway (172.18.0.1), so anything bound to `0.0.0.0` is reachable from it. The
BearDrive hub was moved to `127.0.0.1:4173` for this reason.

## Learning loop (plugin `learning`, team profile only)

- Every email reply ends with `Task T-xxxxxx`. The sender replies "good" or "bad: why", and the
  agent records it with `task_feedback`. No reply in 3 days counts as good.
- A bad grade may add a one-line **lesson**. Lessons apply immediately to later email and cron
  tasks. Guardrails:
  - at most 300 chars each, at most 20 active
  - only the task's own sender (or the owner) can add one
  - no URLs, commands, or permission/sender/secret/instruction wording
  - scanned by the skills guard
- Skills are still staged: approve them with `hermes -p team`, then `/skills pending`.
- Review or revoke lessons (the plugin copies live under `~/.hermes/profiles/team/plugins`):
  `python learning_admin.py list | remove L-xxxxxx | tasks`
- `learning-digest.timer` (20:00 daily) mails new lessons and bad grades to `EMAIL_HOME_ADDRESS`.
