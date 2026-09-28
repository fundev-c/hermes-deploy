# hermes-deploy

Hand-authored configuration and code for the Hermes email agent, its n8n
execution layer, and the BearDrive shared folder. Everything here is a copy of
files from a working install; nothing in this repo is a secret.

- **Rebuild on a new machine:** [SETUP.md](SETUP.md)
- **Design, status, and gotchas:** [docs/PRD.md](docs/PRD.md) (Hermes + n8n) and
  [docs/BearDrive-PRD.md](docs/BearDrive-PRD.md) (shared folder). The BearDrive
  PRD supersedes §8 of the parent.

| Folder | What it is | Goes to |
|---|---|---|
| `hermes-home/` | `config.yaml`, `.env.example`, `SOUL.md`, launchers, the `beardrive` plugin, the learned `sales-drive-workflow` skill | `%LOCALAPPDATA%\hermes\` |
| `hermes-agent-patches/` | Our change to upstream `NousResearch/hermes-agent` @ `01382698`: cron `deliver` removed from the model schema, plus two regression suites | `git apply` inside `%LOCALAPPDATA%\hermes\hermes-agent` |
| `n8n/` | The two published workflows (MCP server + `send_email` guard) and `sync_allowlist.py` | n8n import; script to `%LOCALAPPDATA%\hermes\n8n-workflows\` |
| `beardrive-wsl/` | `wsl.conf`, `.wslconfig`, hub config template, systemd units, logon task | WSL2 Ubuntu 24.04 and `%USERPROFILE%` |

**Deliberately not here:** `.env`, `auth.json`, `shared\nous_auth.json`, every
database, logs, sessions and caches; the n8n database (it holds the SMTP and
Bearer credentials); the retired `plugins\bear\` and `drafts\`; and the
prompt-injection fixture `invoice_mar.md`, which is a live payload.

## Refreshing this repo from the live install

Files are copied, not linked. After changing something in the live Hermes home,
copy it back over the matching file here, then commit. Do not `git init` inside
`%LOCALAPPDATA%\hermes`: a stray `git add .` there would pick up `.env`.
