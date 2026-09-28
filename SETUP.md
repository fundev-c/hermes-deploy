# Setting up Hermes + n8n + BearDrive on a new machine

This rebuilds the working stack from the reference machine, which was set up on
2026-09-28. Follow the steps in order. Each step ends with a check. Do not start
the next step until the check passes.

> **The Hermes home on Windows is `%LOCALAPPDATA%\hermes`, not `~/.hermes`.**
> The upstream docs are written for POSIX. Trust the code, not the docs.

## Pinned versions

| Component | Version |
|---|---|
| Hermes (`NousResearch/hermes-agent`) | 0.21.3 @ `01382698fc32ec7740b6a204d9b7a6abeac74d33` |
| n8n (npm, **no Docker**) | 2.39.8 |
| Node.js | 24.x (24.19.0 on the reference box) |
| WSL2 distro | Ubuntu 24.04 |
| Go (inside WSL) | 1.26.8 (anything >= 1.25.8) |
| BearDrive (`runbear-io/beardrive`) | `42739b0309e5523c88ada2715db398f8edcf3d51` |
| Local model (optional) | llama.cpp (`winget ggml.llamacpp`) + `Qwen3-1.7B` Q4_K_M GGUF |

## What you need before starting

- Windows 10 or 11 with at least 8 GB RAM.
- **Virtualization enabled in the BIOS.** On Lenovo: F1, then Security, then
  Virtualization, then Enabled. Once the hypervisor runs, the CPU flags read
  `False`. The check that matters is:
  ```powershell
  (Get-CimInstance Win32_ComputerSystem).HypervisorPresent   # True after WSL2 is installed
  ```
- Git for Windows and Node.js 24.
- **Secrets you will type into `.env`:**
  - the agent's Gmail address plus a Gmail **app password** (IMAP enabled);
  - an OpenRouter API key (a credited account gets 1000 free requests a day instead of 50);
  - a Nous Portal login for the primary model (done interactively by `hermes`).

Throughout, `$H` means the Hermes home and `$py` the agent's Python:

```powershell
$H  = "$env:LOCALAPPDATA\hermes"
$py = "$H\hermes-agent\venv\Scripts\python.exe"
$R  = "<path where you cloned this repo>"
```

---

## 1. Clone this repo

```powershell
git clone <this repo's URL> hermes-deploy
```

## 2. Install Hermes, pinned to the tested commit

```powershell
& ([scriptblock]::Create((irm https://hermes-agent.nousresearch.com/install.ps1))) `
    -Commit 01382698fc32ec7740b6a204d9b7a6abeac74d33
```

This creates `$H` and clones the agent into `$H\hermes-agent` with its venv.
Then apply our patch, which removes the model-settable cron `deliver` egress path
and adds two regression suites:

```powershell
cd $H\hermes-agent
git apply --check "$R\hermes-agent-patches\0001-cron-deliver-not-model-settable-and-beardrive-tests.patch"
git apply         "$R\hermes-agent-patches\0001-cron-deliver-not-model-settable-and-beardrive-tests.patch"
```

**Check:** `git status --short` shows `tools/cronjob_tools.py` modified and the two
new test files. A later `hermes update` can conflict with the patch. If it does,
reapply it.

The venv is built by **uv** and has **no pip**. Use `$H\bin\uv.exe pip install ...`
if you ever need a package by hand.

## 3. Copy the configuration in

```powershell
Copy-Item "$R\hermes-home\*" $H -Recurse -Force
Copy-Item "$H\.env.example" "$H\.env"
```

Fill in `$H\.env`. These are the values that matter:

| Key | Value |
|---|---|
| `OPENROUTER_API_KEY` | your key. It appears twice in the file, so set both |
| `EMAIL_ADDRESS` / `EMAIL_PASSWORD` | the agent's Gmail and its app password |
| `EMAIL_ALLOWED_USERS` | comma-separated senders who may use the agent. It is default-deny, because `From:` is spoofable |
| `EMAIL_HOME_ADDRESS` | the agent's own address |
| `N8N_MCP_TOKEN` | a long random string. You will paste the same one into n8n in step 5 |
| `N8N_API_KEY` | created in n8n in step 5 |
| `BEARDRIVE_ROOT` | `C:\Users\<you>\BearDrive` (created in step 6) |

Never edit `.env` or `config.yaml` with Git Bash `sed -i` or Python text mode. Both
files are CRLF and those tools silently rewrite every line.

**Machine-specific paths to fix** (the files copied in hard-code the reference
user `dlteam`):

- `config.yaml`, `plugins.entries.beardrive.settings.heartbeat_path`:
  `\\wsl$\Ubuntu-24.04\home\<linux user>\.bdrive\volumes\<mount id>\sync.json`.
  You get the mount id from `bdrive init` in step 6.
- `gateway-service\Hermes_Gateway.cmd` and `.vbs`: every `C:\Users\dlteam`.
- `run-local-model.cmd`: the model path.
- `n8n-workflows\sync_allowlist.py`: `HERMES_HOME` on line 20. It is copied in step 5.

**Plugin dependencies** (openpyxl and pypdf for Excel and PDF reading) are
declared in `plugin.yaml`. Install them through Hermes, not by hand, so that
`hermes update` keeps them:

```powershell
& $py -c "from pathlib import Path; from hermes_cli.plugin_python_deps import install_for_plugin_dir as i; print(i(Path(r'$H\plugins\beardrive')))"
```

**Check:** the output says `installed`. `hermes` then logs in to Nous Portal the
first time it runs interactively.

## 4. (Optional) Local model floor

This is the last entry in the fallback chain. Skip it on an 8 GB machine unless
you need it.

```powershell
winget install ggml.llamacpp
# download Qwen3-1.7B-Q4_K_M.gguf from huggingface.co/ggml-org/Qwen3-1.7B-GGUF
$H\run-local-model.cmd        # serves http://127.0.0.1:8080/v1
```

Keep `--jinja` in the launcher, or tool calls never appear. Never add `--chat-template`.

## 5. n8n, the execution layer

```powershell
npm install -g n8n@2.39.8
Start-Process powershell -ArgumentList '-NoExit','-Command','n8n start'   # http://localhost:5678
```

A plain terminal `n8n` exited twice on the reference machine. The detached
window above stayed up.

1. Open `http://localhost:5678` and create the owner account.
2. **Create two credentials first**, because the workflows reference them:
   - **SMTP** named `Hermes Gmail SMTP`: host `smtp.gmail.com`, port 465 (SSL) or
     587, with the agent's Gmail and app password.
   - **Bearer Auth** named `Bearer Auth account`, with the token set to `N8N_MCP_TOKEN` from `.env`.
3. **Import `n8n\workflows\hermes__send_email.*.json` first**, then
   `My_workflow.*.json`. n8n assigns new ids on import.
   - In the MCP workflow's `send_email` tool node, re-select `hermes__send_email`
     as the sub-workflow.
   - In both workflows, re-select the credentials created above on the nodes that
     show a warning.
4. **Publish both workflows.** Saving is not publishing: an unpublished change
   keeps serving the old version. n8n also refuses to publish while a credential
   is missing.
5. The MCP trigger path must stay `hermes`. The MCP URL
   `http://127.0.0.1:5678/mcp/hermes` in `config.yaml` depends on it.
6. Settings, n8n API: create an API key and put it in `.env` as `N8N_API_KEY`.
7. Push the allowlist into the guard:
   ```powershell
   New-Item -ItemType Directory -Force "$H\n8n-workflows" | Out-Null
   Copy-Item "$R\n8n\sync_allowlist.py" "$H\n8n-workflows\"
   # write the NEW id of hermes__send_email (from its URL) into the id file:
   Set-Content -NoNewline -Encoding ascii "$H\n8n-workflows\.send_email_id" "<new workflow id>"
   & $py "$H\n8n-workflows\sync_allowlist.py"          # rewrites the guard and publishes
   & $py "$H\n8n-workflows\sync_allowlist.py" --check  # must exit 0
   ```

Every time you change `EMAIL_ALLOWED_USERS`, rerun `sync_allowlist.py`. Otherwise
the agent accepts a new sender's email and then refuses to reply to them.

## 6. BearDrive, the shared folder (WSL2)

BearDrive does not build for Windows, so every `bdrive` process runs in WSL2. The
shared folder itself is a normal `C:\` path.

**6.1 Distro.** Run from an elevated PowerShell:

```powershell
wsl --install -d Ubuntu-24.04          # create a Linux user when prompted
```

Copy `beardrive-wsl\wsl.conf` to `/etc/wsl.conf`, changing `default=` to your
Linux user. It turns on systemd and the `metadata` automount option. Without
`metadata`, inbound sync silently fails. Then:

```powershell
wsl --shutdown
Copy-Item "$R\beardrive-wsl\.wslconfig" "$env:USERPROFILE\.wslconfig"   # keeps the distro running when idle
```

**6.2 Build `bdrive`.** Do this inside WSL, on ext4, never under `/mnt/c`. Install
Go 1.26.x from the go.dev tarball to `/usr/local/go`; do not use the apt package.
Add `/usr/local/go/bin:$HOME/bin` to `PATH` in `~/.profile`.

```sh
git clone https://github.com/runbear-io/beardrive ~/src/beardrive
cd ~/src/beardrive && git checkout 42739b0309e5523c88ada2715db398f8edcf3d51
go vet ./... && go test ./...
mkdir -p ~/bin && go build -o ~/bin/bdrive ./cmd/bdrive
```

If the clone ends up marked `+dirty`, it was copied from a Windows checkout with
`core.autocrlf=true`. Run `git reset --hard` in the WSL copy.

**If WSL has no outbound internet** (Symantec Endpoint Protection blocks TCP from
the WSL NAT on the reference machine), download the Go tarball and run
`go mod download` on Windows. Copy both in, then build with
`GOPROXY=off GOTOOLCHAIN=local`. Do not tunnel around the firewall.

**6.3 Hub.**

```sh
mkdir -p ~/bdrive-hub ~/bdrive-store && chmod 700 ~/bdrive-hub ~/bdrive-store
cp /mnt/c/<path to repo>/beardrive-wsl/hub.example.json ~/bdrive-hub/hub.json
# edit: YOUR_LINUX_USER in "remote", and your email in "admins"
bdrive serve -c ~/bdrive-hub/hub.json      # foreground, first run only
```

- Keep three slashes in `file:///`.
- `upload: true` is mandatory.
- The config decoder rejects unknown keys, so a typo stops it from booting.

Open `http://localhost:4173/auth/signup` and sign up with the admin email. That
first-account window closes as soon as the account exists. Then stop the
foreground hub (Ctrl+C) and run it as a service:

```sh
mkdir -p ~/.config/systemd/user
cp /mnt/c/<path to repo>/beardrive-wsl/bdrive-hub.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now bdrive-hub
sudo loginctl enable-linger $USER
```

**6.4 Mount the shared folder.**

```sh
mkdir -p /mnt/c/Users/<WindowsUser>/BearDrive
bdrive init /mnt/c/Users/<WindowsUser>/BearDrive \
    --server http://localhost:4173 --name beardrive-shared --no-hooks --yes
```

- Spell the path with exactly the same case everywhere. A case mismatch kills the daemon.
- `init` uses the device-code login when there is no TTY. If you run
  `bdrive login` yourself, pass `--device`: the browser flow cannot reach WSL loopback.
- `init` installs `beardrive.service` itself. The copy in `beardrive-wsl/` is for
  reference only, so do not copy it in by hand.
- Note the mount id (`m-xxxxxxxx`, in `bdrive status`). Put it in
  `config.yaml`'s `heartbeat_path` from step 3.
- Review the `.bdriveignore` that `init` seeds. Every version of every file is
  kept forever.

**6.5 Survive reboots.** Run from Windows PowerShell:

```powershell
& "$R\beardrive-wsl\register-logon-task.ps1"
```

**Check:**

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:4173/auth/login     # 200
wsl -d Ubuntu-24.04 -- bash -lc "systemctl --user is-active bdrive-hub beardrive; cd /mnt/c/Users/<WindowsUser>/BearDrive && bdrive status"
```

If Windows gets connection-refused while `curl` inside WSL works, run `wsl --shutdown` and retry.

**Never put `invoice_mar.md` or any prompt-injection fixture in the shared
folder.** It replicates to every member's machine.

## 7. Start everything

```powershell
Start-Process powershell -ArgumentList '-NoExit','-Command','n8n start'
hermes gateway restart
```

Wait for `email connected` in `$H\logs\gateway.log` before sending mail. On
connect, the adapter marks every existing inbox message as seen, so a mail that
arrives during a restart is never processed. Do not run the Electron desktop app
on 8 GB, because there is not enough memory.

## 8. Verify

```powershell
hermes mcp test n8n-tools                                 # 2 tools: send_email, hermes_ping
hermes fallback list                                      # primary + 4 fallbacks
hermes cron list
& $py "$H\n8n-workflows\sync_allowlist.py" --check        # exit 0

cd $H\plugins
& $py -m pytest -p no:cacheprovider beardrive\test_tools.py -q      # 40 passed
cd $H\hermes-agent
& $py -m pytest tests/cron/test_cron_deliver_not_model_settable.py -q                   # 7 passed
& $py -m pytest tests/security/test_beardrive_cross_tool_containment.py -q              # 4 passed
```

**Live test.** From an allow-listed address, email the agent an `.xlsx` and ask
for a total. The pass condition is the tool calls, not the answer:

```powershell
Get-Content "$H\logs\beardrive-access.jsonl" -Tail 5
# expect drive_save_attachment then drive_read, with a non-empty "actor"
Select-String -Path "$H\logs\agent.log" -Pattern 'read_file|search_files|terminal|execute_code' | Select-Object -Last 5
# expect nothing from an email session
```

## Adding a user later

1. Add their address to `EMAIL_ALLOWED_USERS` in `.env`.
2. Run `& $py "$H\n8n-workflows\sync_allowlist.py"`.
3. Run `hermes gateway restart`.

Learned skills are staged. Approve them only at this PC: run `hermes`, then
`/skills pending` and `/skills approve <id>`.
