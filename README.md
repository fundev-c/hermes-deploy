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

## Setup on a new machine

The short version is below. [SETUP.md](SETUP.md) has the full steps, a check after
each one, and the gotchas that cost time on the reference machine. Read it the first
time through.

**Before you start:**
- Windows 10 or 11 with 8 GB+ RAM and virtualization on in the BIOS.
- Git for Windows and Node.js 24.
- The agent's Gmail address with an app password, an OpenRouter key, and a Nous Portal login.

```powershell
$H  = "$env:LOCALAPPDATA\hermes"
$py = "$H\hermes-agent\venv\Scripts\python.exe"
$R  = "<path to this repo>"
```

1. **Clone this repo.**
   ```powershell
   git clone https://github.com/fundev-c/hermes-deploy.git
   ```
2. **Install Hermes pinned to the tested commit, then apply our patch.**
   ```powershell
   & ([scriptblock]::Create((irm https://hermes-agent.nousresearch.com/install.ps1))) `
       -Commit 01382698fc32ec7740b6a204d9b7a6abeac74d33
   cd $H\hermes-agent
   git apply "$R\hermes-agent-patches\0001-cron-deliver-not-model-settable-and-beardrive-tests.patch"
   ```
3. **Copy the configuration in and fill in secrets.**
   ```powershell
   Copy-Item "$R\hermes-home\*" $H -Recurse -Force
   Copy-Item "$H\.env.example" "$H\.env"      # then edit .env
   ```
   - Set the API key, Gmail login, allowlist, n8n tokens and `BEARDRIVE_ROOT` in `.env`.
   - Replace `C:\Users\dlteam` in the launchers, `run-local-model.cmd` and `sync_allowlist.py`.
   - Install the plugin's Excel and PDF libraries through Hermes:
   ```powershell
   & $py -c "from pathlib import Path; from hermes_cli.plugin_python_deps import install_for_plugin_dir as i; print(i(Path(r'$H\plugins\beardrive')))"
   ```
4. **(Optional) Local model.** Install llama.cpp with `winget install ggml.llamacpp`.
   Download `Qwen3-1.7B-Q4_K_M.gguf`, then run `run-local-model.cmd`.
5. **n8n.**
   ```powershell
   npm install -g n8n@2.39.8
   Start-Process powershell -ArgumentList '-NoExit','-Command','n8n start'
   ```
   - At `http://localhost:5678`, create the owner account.
   - Create the `Hermes Gmail SMTP` and `Bearer Auth account` credentials. The Bearer token is `N8N_MCP_TOKEN`.
   - Import `n8n/workflows/hermes__send_email.*.json` first, then `My_workflow.*.json`.
   - Re-link the credentials and the sub-workflow, then **publish both**. Saving is not publishing.
   - Create an API key for `.env`, write the new send-email workflow id into
     `n8n-workflows\.send_email_id`, and run the sync:
   ```powershell
   & $py "$H\n8n-workflows\sync_allowlist.py"; & $py "$H\n8n-workflows\sync_allowlist.py" --check
   ```
6. **BearDrive in WSL2.**
   - Run `wsl --install -d Ubuntu-24.04`.
   - Copy `beardrive-wsl/wsl.conf` to `/etc/wsl.conf` and `.wslconfig` to your user folder, then run `wsl --shutdown`.
   - Inside WSL, install Go 1.26 and clone `runbear-io/beardrive` at `42739b0` into `~/src/beardrive`. Build it with `go build -o ~/bin/bdrive ./cmd/bdrive`.
   - Copy `hub.example.json` to `~/bdrive-hub/hub.json` and edit the user and admin email.
   - Sign up at `http://localhost:4173/auth/signup`.
   - Enable `bdrive-hub.service` and run `sudo loginctl enable-linger $USER`.
   - Mount the folder:
   ```sh
   bdrive init /mnt/c/Users/<you>/BearDrive --server http://localhost:4173 --name beardrive-shared --no-hooks --yes
   ```
   - Put the mount id in `config.yaml`'s `heartbeat_path`.
   - Run `beardrive-wsl/register-logon-task.ps1` from Windows.
7. **Start.** Run `n8n start`, then `hermes gateway restart`. Wait for `email connected` in `logs\gateway.log`.
8. **Verify.**
   ```powershell
   hermes mcp test n8n-tools            # 2 tools
   hermes fallback list                 # primary + 4
   curl.exe -s -o NUL -w "%{http_code}" http://localhost:4173/auth/login   # 200
   ```
   Then email the agent an `.xlsx` from an allow-listed address. Check that
   `logs\beardrive-access.jsonl` shows `drive_save_attachment` then `drive_read`.

## Refreshing this repo from the live install

Files are copied, not linked. After changing something in the live Hermes home,
copy it back over the matching file here, then commit. Do not `git init` inside
`%LOCALAPPDATA%\hermes`: a stray `git add .` there would pick up `.env`.
