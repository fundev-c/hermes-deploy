# Bear Drive — Product Requirements & Status

**Last updated:** 2026-09-22 (session 2) · **Owner:** harshil
**Status:** §4.0 gate PASSED · §4.2 egress fix APPLIED · §4.1 **BLOCKED in firmware (BIOS)**
**Upstream:** `github.com/runbear-io/beardrive` @ `42739b0` (`v0.15.0-80-g42739b0`), cloned 2026-09-21 23:59
**Supersedes:** `..\PRD.md` §8 ("Bear as a shared team folder") and `..\drafts\bear-shared-namespace\`

---

## 1. Goal

Give the Hermes email agent **one shared folder** that every allow-listed sender
can drop files into, that the agent can read, and that keeps working when more
than one person uses it.

The 2026-09-21 session established that the existing Bear feature is the
deliberate opposite of this. `plugins\bear\sandbox.py:108` is
`root = bear_root() / owner_slug(who)` — every sender gets a private sha256
subtree, so that *"one user cannot name, reach, or enumerate another user's
files."* Option B (a `shared/` namespace routing to `BEAR_ROOT/teams/<team>/`)
was drafted but never wired in, and its own merge checklist listed four
unsolved problems: cross-member prompt injection, silent write collisions,
no access audit, and the risk of conflating team membership with
`EMAIL_ALLOWED_USERS`.

**BearDrive replaces that draft.** It is "Google Drive for AI agents": any folder
becomes a synced project backed by a hub, with per-change provenance (who, when,
which device), content-addressed history retained forever, restore,
server-enforced folder permissions, and a web UI. Three of the four unsolved
problems become BearDrive's, and it has already solved them.

**Architecture principle, extending the existing one.** *"Hermes reasons, n8n
executes"* gains a third clause: **BearDrive stores.** BearDrive is never the
brain and never an execution path; it is a synced filesystem Hermes reads
through a jail.

```
  members (email)                       teammates (laptops, later)
        |                                          |
        v                                          v
  Hermes (allowlist + DMARC)              bdrive client daemon
        |                                          |
        |  drive_* tools (jailed)                  |  HTTPS
        v                                          v
  C:\Users\dlteam\BearDrive  <--- 3s scan ---  bdrive hub (WSL2)
        ^                                          |
        |                                    file:// object store
        +--- bdrive daemon materializes teammates' changes
```

**Constraints inherited from the parent PRD:** no Docker; free-tier models only;
Windows host. **New constraint:** BearDrive does not build for Windows, so every
`bdrive` process runs in WSL2.

---

## 2. Where everything lives

> Hermes home on Windows is `%LOCALAPPDATA%\hermes`, **not** `~/.hermes`.
> This bites BearDrive too — see §5 gotcha 3.

| Thing | Path |
|---|---|
| Upstream fork (pristine clone) | `...\hermes\Bear_Drive\beardrive\` |
| This document | `...\hermes\Bear_Drive\PRD.md` |
| Build location (**ext4, not `/mnt/c`**) | `~/src/beardrive` in WSL |
| `bdrive` binary | `~/bin/bdrive` in WSL |
| **The shared folder** | `C:\Users\dlteam\BearDrive` = `/mnt/c/Users/dlteam/BearDrive` |
| Hub config | `~/bdrive-hub/hub.json` (WSL, ext4) |
| Hub state (`BDRIVE_HOME`) | `~/bdrive-hub/` — `auth.json`, `projects.json`, `orgs.json`, `devices.json` |
| Hub object store | `~/bdrive-store/` (ext4) |
| Client state (`BDRIVE_HOME`) | `~/.bdrive/` — `settings.json` (device token, 0600), `mounts.json`, `volumes/` |
| Hub URL | `http://localhost:4173` (from Windows *and* from inside WSL) |
| New Hermes plugin | `...\hermes\plugins\beardrive\` |
| Hermes access audit | `...\hermes\logs\beardrive-access.jsonl` (**outside** the synced root) |
| Injection fixture | `...\hermes\fixtures\injection\invoice_mar.md` (**outside** any synced tree) |
| Retiring | `...\hermes\plugins\bear\`, `...\hermes\drafts\bear-shared-namespace\`, `C:\Users\dlteam\Bear\` |

**Ground truth verified 2026-09-22:** WSL 2.7.14.0 / kernel 6.18.33.2 is
installed, default version 2, **with zero distributions** — this is a
from-scratch bring-up. Windows is 10.0.19045.6466, which matters: no mirrored
networking (§5 gotcha 8). `C:\Users\dlteam\BearDrive` does not exist yet.

> **CORRECTION, session 2.** That check established WSL was *installed*. It never
> established WSL2 can **start** — and it cannot: **virtualization is disabled in
> this machine's firmware.** See §4.1a. Everything from §4.1 down is blocked on a
> BIOS change, not on any code. `wsl --status` succeeding is not the same as
> `Win32_Processor.VirtualizationFirmwareEnabled` being true; only the second is
> load-bearing, and only the second was false.

---

## 3. Decisions taken

| Question | Decision | Why |
|---|---|---|
| How Hermes reaches the folder | **A jailed plugin** | Not generic `file` tools — see §3.1. Not MCP — see §3.2 |
| bdrive on Windows | **Run it in WSL2** | `internal/store/store.go` uses `syscall.Flock`; `internal/daemon/daemon.go` uses `Flock`, `O_NOFOLLOW`, `SysProcAttr{Setsid}`, `Kill`. `.goreleaser.yaml` ships darwin+linux only |
| Folder location | **`/mnt/c/...`**, a native `C:\` path for Hermes | Avoids `\\wsl.localhost` — the jail rejects UNC by design and weakening that is the last thing to do |
| Hub | **Self-hosted locally, `file://`** | Nothing leaves the machine; no TLS/DNS/billing work |
| Project layout | **One shared project, everyone a member** | The literal requirement |
| Upstream relationship | **Hard fork, edit in-tree** | With clean per-category commits so general fixes stay cherry-pickable |
| Turn hooks | **Not in v1** | Polling only; hooks are attribution + latency, not correctness |

### 3.1 Why not generic file tools

`..\PRD.md` §3.9 withheld the `file` toolset from `email` and `cron` so
`read_file`/`search_files` could not reach arbitrary paths — that took the
catalog from 43 tools to 5. The rule recorded there is *"to restore one, add the
toolset name to both lists — never the first seven"*, and `file` is first on
that list. The folder can be plain files; the **tools** still need a jail.

### 3.2 Why not MCP (yet)

BearDrive ships a complete MCP server — `internal/webapp/mcp.go` (86 KB),
official `modelcontextprotocol/go-sdk v1.8.0`, ten tools
(`list read glob grep write edit delete move history restore`), OAuth 2.1 +
PKCE + dynamic client registration, and grants that can never exceed the
granting human's permission. It needs zero Hermes code and is worth revisiting.

Two reasons it is not v1:

1. **MCP servers bypass `platform_toolsets` entirely.**
   `hermes_cli/tools_config.py:653` `_merge_mcp_servers` adds every globally
   enabled server regardless of the allowlist. The §3.9 perimeter would stop
   being two lines of YAML and become the OAuth grant.
2. **Both doors on one project is a documented anti-pattern.** BearDrive's own
   `mcpInstructions`: an agent alternating between MCP and a synced folder
   *"races the daemon into conflict copies and reads its own stale writes."*

Enabling it later is `{"mcp": {"enabled": true}}` in the hub config — it is off
by default, and a hub with no MCP block has no `/mcp` at all rather than an
unauthenticated one.

### 3.3 BearDrive already ships a Hermes adapter

`internal/agenthooks/agenthooks.go:77` —
`var Agents = []string{"claude", "codex", "gemini", "hermes"}`, with
`installHermes` (`:573`) merging one `pre_llm_call` and two `post_tool_call`
hooks. The Hermes side accepts exactly that shape: `agent/shell_hooks.py` parses
`cfg.get("hooks")` with `_TOOL_EVENTS = {"pre_tool_call", "post_tool_call"}`.
`docs/self-hosting.md` even lists Hermes in the hub's one-paste agent setup.

Two things are wrong for this machine, both deferred out of v1 and scoped in §7.

---

## 4. What is LEFT

Ordered. Each phase has a gate; do not cross a gate on trust.

### 4.0 — ~~Verify the Bear-in-cron fix~~ — **CLOSED 2026-09-22, PASSED**

Done against the pre-migration `plugins\bear\` and `C:\Users\dlteam\Bear\`, exactly
as this section required. Email sent 16:04:17 from `harshilkhandelwal71@gmail.com`
(the address that owns the seeded data — slug `ecfb026e4ce25fd6`); job
`c8e296de2edf` created 16:04:27, fired 16:08:05.

The job carried what the 2026-09-21 control run could not:

| | control `4492149edad0` | this run `c8e296de2edf` |
|---|---|---|
| `origin` | `None` (CLI-created) | `{platform: email, chat_id: harshilkhandelwal71@gmail.com}` |
| `deliver` | `local` | `origin` |
| answer pre-baked in prompt? | n/a | **no** — "Read … Calculate …" |

**Result — both gates cleared in one run:**

- **Bear-in-cron: PASS.** `bear_list` (244 chars) then `bear_read sales_q3.csv`
  (237 chars) both succeeded inside `cron_c8e296de2edf_20260922_160805`. No
  `no session identity`. The chain is confirmed end to end: `scheduler.py:2077`
  builds `cron:<job_id>:<exec>` → `sandbox.py:57` takes `parts[1]` → `get_job` →
  `origin.chat_id` → the correct tenant slug.
- **§3.9 lockdown: PASS, under genuine pressure.** 15 tool calls, **zero**
  `search_files`/`read_file`/`write_file`/`terminal`/`execute_code`/
  `delegate_task`/`computer_use`. The agent was refused three times and still had
  no escape hatch — it tried `bear_read` on an absolute path instead, and the jail
  refused that too.
- **Prompt injection: PASS again, under harder conditions.** See parent PRD §3.8.
- Answer was AMER $931,000, correct — a side note only; the pass condition was
  always the tool call.

**Three findings this run produced that the plan did not anticipate**, all folded
in below: the `deliver` bypass demonstrated live (§6, now decided), the fact that a
cron agent structurally *cannot* address `send_email` (also §6), and gotchas 16–18.

### 4.1a — ~~Enable virtualization in firmware~~ — **CLOSED 2026-09-24**

**Closed 2026-09-24.** VT-x enabled in BIOS; `wsl --install -d Ubuntu-24.04`
succeeded. **The verification this section prescribed is wrong** — once the
Windows hypervisor is running it masks the CPU's virtualization flags, so
`VirtualizationFirmwareEnabled`, `VMMonitorModeExtensions` and
`SecondLevelAddressTranslationExtensions` *all* read **False** with VT-x on
(observed live). The load-bearing check is
`(Get-CimInstance Win32_ComputerSystem).HypervisorPresent` → **True**, or
`systeminfo` → "A hypervisor has been detected". Record of the original blocker:

`wsl --install -d Ubuntu-24.04` was run on 2026-09-22. It downloaded and installed
the distro, then failed at VM creation:

```
WSL2 is unable to start since virtualization is not enabled on this machine.
Error code: Wsl/InstallDistro/Service/RegisterDistro/CreateVm/HCS/HCS_E_HYPERV_NOT_INSTALLED
```

Confirmed two independent ways:

```
Win32_Processor.VirtualizationFirmwareEnabled : False   <- the blocker
Win32_Processor.VMMonitorModeExtensions       : True    <- the CPU does support VT-x
systeminfo -> Virtualization Enabled In Firmware: No
```

The i5-4590S supports VT-x; it is switched **off in BIOS**. Machine is a Lenovo
`10BBS0DY00`, BIOS `FGKT33AUS`. Fix: reboot → F1 → **Security → Virtualization →
Intel(R) Virtualization Technology → Enabled** → save and exit, then re-run
`wsl --install -d Ubuntu-24.04` and continue at §4.1.

`wsl --list --verbose` still reports no distributions after the failure, so §4.1
starts clean rather than half-done.

**Do not route around this with WSL1.** §4.1 needs `systemd=true` for
`autostart_linux.go`'s `booted()`, and `bdrive` relies on `Flock` / `O_NOFOLLOW` /
`Setsid`. WSL1 is not a degraded option here, it is a different one.

### 4.1 — Stand up BearDrive in WSL2 — **DONE 2026-09-24, GATE closed (2 of 3 pass; 1 accepted limitation)**

Touches no Hermes code.

**As built, 2026-09-24 — deviations from the steps below (verified live):**

- **Linux user is `dlteam_linux`, not `dlteam`.** Every `/home/dlteam/...` below
  is `/home/dlteam_linux/...`; `hub.json` `remote` is
  `file:///home/dlteam_linux/bdrive-store`; linger is for `dlteam_linux`. The
  shared folder is unchanged: `/mnt/c/Users/dlteam/BearDrive`.
- **WSL has no outbound TCP** — §5 gotcha 23. Go and modules were fetched on the
  Windows side and copied in; builds run `GOPROXY=off GOTOOLCHAIN=local`.
- **Go 1.26.8** (≥ 1.25.8), official tarball at `/usr/local/go`, SHA-256
  verified against go.dev. `~/.profile` adds `/usr/local/go/bin:$HOME/bin`.
- **Step 3:** fork copied to `~/src/beardrive` at `42739b0` — reset to HEAD after
  the copy, because the Windows clone's `core.autocrlf=true` made 708 files read
  as modified (build stamped `+dirty`). `go vet ./...` clean; `go test ./...`
  exit 0, 12 packages ok, no FAIL. The Windows clone is still pristine.
- **Step 5:** `admins: ["harshil.k@championsmail.com"]`. First account created
  2026-09-24 11:41 UTC; the bootstrap window is closed. Backup
  `~/bdrive-hub/hub.json.bak-20260924-113651`. `~/bdrive-hub` and
  `~/bdrive-store` are 0700.
- **Step 7:** hub unit is `~/.config/systemd/user/bdrive-hub.service`
  (`Environment=BDRIVE_HOME=%h/bdrive-hub`, `Restart=on-failure`), enabled.
  Linger enabled for `dlteam_linux`.
- **Step 8:** `bdrive login` was **not** run separately. Without a TTY, `init`
  falls back to the device-code flow by itself (`INSTALL_FOR_AGENTS.md` §2), which
  satisfies gotcha 7. Command as run:
  `bdrive init /mnt/c/Users/dlteam/BearDrive --server http://localhost:4173 --name beardrive-shared --no-hooks --yes`.
  Project `beardrive-shared` = `8552cbb4-e00b-4df1-be61-1b8f2b741e34`, mount
  `m-0b9866e5`, device `ACCOUNTS-6R (c43a5bfb8ae8)`. Autostart went to
  `~/.config/systemd/user/beardrive.service` (enabled). The seeded
  `.bdriveignore` has `*.zip`, `*.mp4`, `.env*`, `*.log` as expected.
- **Step 9 is not enough as written** — §5 gotcha 24. WSL stops an idle distro
  about 60s after the last `wsl.exe` client exits, taking the hub with it
  (observed: 000 from Windows after 80s idle). Fixed with
  `C:\Users\dlteam\.wslconfig`: `[general] instanceIdleTimeout=-1`,
  `[wsl2] vmIdleTimeout=-1` (verified: 200 after 100s idle). The logon task
  still has to *start* the distro after a Windows reboot. Claude Code's
  auto-mode classifier refused to register it (persistence), so it is
  **user-run and not yet confirmed**:
  `Register-ScheduledTask -TaskName "BearDrive - start WSL" -Action (New-ScheduledTaskAction -Execute "C:\Windows\System32\wsl.exe" -Argument "-d Ubuntu-24.04 --exec /bin/true") -Trigger (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")`

**GATE result, 2026-09-24:**

| Criterion | Result |
|---|---|
| `CHMOD_OK` | **PASS.** Mode actually changes (644 → 600). `/mnt/c` mount line shows `metadata;umask=22;fmask=11`. Probes used `*.log` names (ignored) instead of `.p`, so nothing probe-shaped entered permanent history. |
| Three distinct ns mtimes from three rapid writes | **FAIL — accepted by the user as a known limitation.** 2 of 3 distinct; 19 of 40 on a rerun (ext4 gave 40 of 40). NTFS timestamps advance about every 16 ms. Risk and mechanism: §5 gotcha 22. |
| Hub → Windows edit on disk | **PASS.** `hub-test.md` created from WSL, edited in the browser editor, landed on disk as `edited in the browser` (mode 644, 34 B), readable from Windows. No errors or skips in `daemon.log`. `bdrive log` shows three versions. There is no browser upload or create (`Browser.tsx:383`); the browser can only edit existing files. |

1. `wsl --install -d Ubuntu-24.04`, then write **both** settings to
   `/etc/wsl.conf` and `wsl --shutdown`:
   ```ini
   [boot]
   systemd=true
   [automount]
   options = "metadata,umask=22,fmask=11"
   ```
   `systemd` because `internal/autostart/autostart_linux.go` `booted()` stats
   `/run/systemd/system` and treats its absence (it names WSL1) as "a unit file
   is inert decoration". `metadata` because of the `os.Chmod` hazard, §5 gotcha 1.
2. Go ≥ 1.25.8 (`go.mod:3`). Not the distro package.
3. Copy the fork to ext4 (`~/src/beardrive`) and
   `go build -o ~/bin/bdrive ./cmd/bdrive`. Confirm `go vet ./...` and
   `go test ./...` green **before** any fork edit, so a later failure is ours.
   Optional pre-flight: `BDRIVE_E2E_SERVE=1 go test -run TestE2EServe ./internal/webapp`
   (seeded hub on :8993) proves binary + embedded frontend + auth before you
   commit to a real `BDRIVE_HOME` layout.
4. Two separate `BDRIVE_HOME`s — hub `~/bdrive-hub`, client default `~/.bdrive`
   — plus the store at `~/bdrive-store`. **All three on ext4.** Every POSIX-only
   primitive lives under `BDRIVE_HOME`; `grep syscall. internal/syncer/` returns
   nothing, which is the whole reason `/mnt/c` works.
5. `~/bdrive-hub/hub.json`:
   ```json
   {
     "remote": "file:///home/dlteam/bdrive-store",
     "addr": ":4173",
     "upload": true,
     "volume": "BearDrive",
     "auth": {
       "allow_signup": false,
       "require_verification": false,
       "require_approval": false,
       "admins": ["<your email>"],
       "brand": "BearDrive (local)"
     },
     "reads": { "enabled": true }
   }
   ```
   `upload: true` is **mandatory** — without it `bdrive init` cannot create the
   project (`internal/webapp/server.go:1309` → 403 "this server is read-only").
   Three slashes on `file://` or the first path segment parses as a host. No
   `database` block: `file` is the default and right for one machine. No
   `trust_proxy`: there is no proxy, and it would let a caller spoof
   `X-Forwarded-For` past the login limiter. No `smtp` — its absence is what
   lets `require_verification: false` boot. No `allowed_domains` — the bootstrap
   signup uses plain `signup`, not `signupInvited`, so a typo locks you out of
   your own first account. The decoder uses `DisallowUnknownFields`, so a typo
   anywhere is a loud boot failure.
6. Run it foreground once (`bdrive serve -c ~/bdrive-hub/hub.json`; `web` is an
   alias). First account at `http://localhost:4173/auth/signup` — while the hub
   has **zero** accounts, an email on `admins` may sign up directly and
   activates immediately. That window closes forever once the account exists.
   Invite the second member from the UI.
7. Promote the hub to its own systemd user unit. **`bdrive autostart`/`resume`
   manage client daemons only** (`cmd/bdrive/resume.go` iterates `LoadMounts()`)
   — they will never start the hub.
8. Mount:
   ```sh
   mkdir -p /mnt/c/Users/dlteam/BearDrive
   bdrive login --device http://localhost:4173
   bdrive init /mnt/c/Users/dlteam/BearDrive \
       --server http://localhost:4173 --name beardrive-shared --no-hooks --yes
   ```
   **`--device` is required** — §5 gotcha 7. Do **not** pass `--no-autostart`.
   Review the `.bdriveignore` that `init` seeds; it excludes `*.zip`, `*.mp4`,
   `.env*`, `*.log`, and it syncs.
9. Reboot survival needs **three** independent links: `systemd=true`,
   `loginctl enable-linger dlteam`, and a Windows logon scheduled task running
   `wsl.exe -d Ubuntu-24.04 --exec /bin/true` — nothing starts the distro on its
   own after a Windows reboot.

**GATE:** `CHMOD_OK` from the probe in §5 gotcha 1; three distinct nanosecond
mtimes from three rapid writes; and a **hub → Windows** edit landing on disk.
That last one is the only thing that exercises `os.Chmod`.

### 4.2 — ~~Apply the `deliver` egress fix~~ — **CLOSED 2026-09-22, APPLIED**

**Applied 2026-09-22.** `deliver` and `failure_deliver` are gone from
`CRONJOB_SCHEMA` and from `_HANDLER_FORWARDED_ARGS`. Reasoning in §6; what shipped
and how it was verified in §6.1. Backup: `tools\cronjob_tools.py.bak-20260922-162833`.

### 4.3 — Replace the Bear plugin — **DONE 2026-09-24 (config applied; gateway not yet restarted)**

New `plugins\beardrive\`, toolset key `beardrive`, tools `drive_list`,
`drive_read`, `drive_search`, `drive_save_attachment`, `drive_status`.

**As built, 2026-09-24 — deviations and findings (verified):**

- **Preserved byte-for-byte, checked by diff** (CRLF-normalised: the old
  `sandbox.py`/`tools.py` were CRLF, the new files are LF like the rest of the
  tree): the 35-line resolver block from `unstripped = ...` to
  `return candidate, root`, `_reject_hostile_component`,
  `_owner_from_cron_task`, and `_cache_roots`. The resolver's error strings still
  say "Bear folder" / "Bear paths". That was deliberate: byte-identical was the
  requirement.
- **`drive_status` could not use `.bdrive/` mtime, because the daemon never
  touches it.** Observed: `.bdrive/` and `.bdrive/config.json` stayed at
  11:45:38 for more than an hour while the daemon ran and synced. The
  "hours old means the daemon is dead" signal specified below would have
  reported *dead* permanently. Replaced by the daemon's own `sync.json` in the
  volume store, which is rewritten every remote cycle (observed every ~12s at
  idle), read from Windows as
  `\\wsl$\Ubuntu-24.04\home\dlteam_linux\.bdrive\volumes\m-0b9866e5\sync.json`
  with a 5s-bounded stat. The hub is probed at `/auth/login`. Stale over 120s →
  `sync: "stale"` with a note telling the agent to say so. Hub down →
  `"offline"`. Both paths are in `config.yaml` at
  `plugins.entries.beardrive.settings.{heartbeat_path, hub_url}`, not `.env`
  (Hermes rule: `.env` is secrets only). This is a fixed plugin-config read,
  never a model-supplied path, so the jail's UNC refusal is untouched.
  `.bdrive/config.json` is still never parsed and no id is surfaced.
- **`drive_root()` also requires `<root>/.bdrive/`**, proof the folder is a
  mount and not merely an existing directory. Smoke-tested: a mistyped root and a
  non-mount folder both make `drive_available()` False and create nothing.
- **Untrusted delimiter block** carries a fresh random nonce per read
  (`<<<UNTRUSTED FILE <nonce>: path ...>>> ... <<<END <nonce>>>`), so a
  planted file cannot pre-write the closing marker. `untrusted_content: true` is
  kept alongside it.
- **Audit line** (`logs\beardrive-access.jsonl`, via `get_hermes_home()`):
  `ts, tool, actor, actor_source (session|cron-origin|""), ok, path|query,
  bytes|count, error`. One line per call, refusals included. Never raises.
- **Directory walks prune** `.bdrive/`/`.git/`/`.bdrive-tmp-*` instead of
  filtering them, skip links, and skip any directory that resolves outside the
  root. On Windows a junction is not a symlink to `is_symlink()`.
- **Gotcha 4: the PRD's platform list was incomplete.** A probe over every entry
  in `PLATFORMS` (22) found `beardrive` still on by default for 11 platforms it
  didn't list: `api_server bluebubbles dingtalk feishu matrix mattermost webhook
  wecom wecom_callback weixin whatsapp_cloud`. All were added to
  `known_plugin_toolsets`. After the change, `beardrive` resolves **only** on
  `email` and `cron`.
- **Catalog probe (the §9 probe, `HERMES_GATEWAY_SESSION=1`,
  `skip_tool_search_assembly=True`)**, for `email` and `cron`: exactly
  `cronjob_manage drive_list drive_read drive_save_attachment drive_search drive_status`.
  None of `read_file search_files write_file patch terminal execute_code
  delegate_task computer_use` is present.
- **Smoke test, 21/21**, handlers called as `tool_executor` does
  (`fn({...}, task_id="", tool_call_id="abc")`).
  - Reads, against the real folder with no identity: list hides `.bdrive/` and
    flags `.bdriveignore` `read_only`; traversal, absolute, UNC, hidden and
    `.BDRIVE.` paths are refused.
  - Writes, against a throwaway mount: they refuse without identity;
    `on_exists` defaults to `fail`, `rename` gives `report (2).csv`, and
    `overwrite` is refused; sources outside the cache and reserved names
    (`.bdriveignore`, `*.bdrive-conflict-*`) are refused; conflict copies read
    as their real type.
- **Files:** `config.yaml` → backup `config.yaml.bak-20260924-181800` (CRLF kept,
  279 lines). `.env` → backup `.env.bak-20260924-181800`; exactly one line changed
  (`BEAR_ROOT` → `BEARDRIVE_ROOT=C:\Users\dlteam\BearDrive`), 521 CRs kept. A first
  attempt with Git Bash `sed -i` stripped every CR and mangled the new line (GNU
  sed reads `\U`/`\d`/`\B` in a replacement as case escapes). It was restored
  from the backup and redone in binary mode. **This is gotcha 21's cousin: never
  `sed -i` these files from Git Bash.**
- `plugins\bear\` is **still on disk, disabled** (no longer in `plugins.enabled`),
  pending §4.4 as §4.5 requires.
- **Gateway not restarted.** It and n8n were both down after the reboot, and
  starting the gateway puts the email agent live, so that is left to the user.
  First run after starting: re-run the catalog probe against the live gateway,
  then §4.4.

**Preserved byte-for-byte from `plugins\bear\`:** the whole rejection order in
`resolve_in_bear` from `unstripped = ...` to `return candidate, root` — UNC,
absolute/drive-letter, per-component `..`, `_reject_hostile_component` (NTFS ADS
`:`, trailing `[ .]`, `_WIN_RESERVED`), the containment check, the per-component
symlink probe, and the comments explaining why components are validated *before*
stripping. Also the handler signature `def f(arguments, task_id="", **_)` and the
`_cache_roots()` restriction on the attachment source.

**Changed:**

- `owner_slug()` and `owner_root()` **deleted**. `drive_root()` reads
  `BEARDRIVE_ROOT`, and **never `mkdir`s** — the old `owner_root` did.
- The `owner=` parameter is dropped from the resolver. It was safe when no tool
  passed it; on a function whose root no longer depends on it, it is a trap.
- `bear_available()`'s `or ~/Bear exists` fallback is **not** carried over. The
  new gate requires the env var *and* an existing directory. A typo'd var that
  silently creates an unsynced folder is the worst failure available here:
  everything looks like it works and nothing syncs.
- Reserved-path guard for `.bdrive/`, `.git/` (case-folded, trailing dot/space
  trimmed, at any depth), `.bdriveignore` (readable, never writable — it is the
  shared scope control), `*.bdrive-conflict-*` (listed and readable, never
  writable), `.bdrive-tmp-*`, and `.claude/` (readable, never writable — it is
  code the agent runs, which is why BearDrive refuses to sync it).
- **Strip the conflict suffix before the `_TEXT_SUFFIXES` check**, or every
  conflict copy is unreadable: `report.csv.bdrive-conflict-dev-…` has suffix
  `.bdrive-conflict-dev-…`, not `.csv`. The one class of file BearDrive
  preserves for a human to read would be the one class the agent cannot open.
- **New `drive_status`** — file/byte counts, conflict count, newest file mtime,
  and the mtime of `.bdrive/` (which the daemon touches; hours old means the
  daemon is dead and every read is stale). Health, not identity: it does not
  parse `.bdrive/config.json` and never surfaces project or mount ids.

**Identity splits (§5 gotcha 5).** `current_owner` becomes `current_actor()`
(returns `""` when unknown) plus `require_actor()` (raises, same message).
**Reads do not require identity; writes do.** `_owner_from_cron_task` is copied
byte-identical — its reasoning (the job's `origin` is unforgeable because
`origin` is absent from `CRONJOB_SCHEMA`) is unchanged and still correct.

**Write policy.** Overwrite is not a capability that exists: `on_exists` is
`"fail"` (default) or `"rename"`, so no argument a prompt-injected email can set
destroys a teammate's file. The collision check **is** the write —
`os.open(dest, O_CREAT|O_EXCL|O_WRONLY|O_BINARY)`, not `if dest.exists()`, which
has a real TOCTOU window since email and cron turns can overlap. Unlink the
partial on failure. Do not stage through a temp file inside the root — the
daemon will scan and upload it mid-write. `"rename"` yields `report (2).csv`,
deliberately **not** BearDrive's `.bdrive-conflict-` shape: a Hermes collision
is not a sync conflict, and borrowing the name would make the glob agents are
documented to look for lie.

**No free-text `drive_write` in v1.** The current surface can only introduce
bytes that arrived as an attachment from an allow-listed sender — a real
property, held for free. A `drive_write(path, content)` lets an injected run
plant a payload for a teammate's next run without sending any email, turning
read-only injection into persistent cross-member injection.

**`config.yaml`** — back up first (`config.yaml.bak-<yyyymmdd-HHMMSS>`):
```yaml
plugins:
  enabled: [beardrive]
  entries:
    beardrive: { allow_tool_override: false }
platform_toolsets:
  email: [beardrive, cronjob]
  cron:  [beardrive, cronjob]
known_plugin_toolsets:          # the §5 gotcha 4 fix — see below
  cli:           [a2a, beardrive, spotify]
  telegram:      [beardrive]
  discord:       [beardrive]
  whatsapp:      [beardrive]
  slack:         [beardrive]
  signal:        [beardrive]
  homeassistant: [beardrive]
  qqbot:         [beardrive]
  yuanbao:       [beardrive]
  teams:         [beardrive]
  google_chat:   [beardrive]
```
Nothing here adds `file`, `terminal`, `code_execution`, `computer_use`,
`browser`, `delegation` or `connections` to `email` or `cron`.

**`.env`** — delete `BEAR_ROOT`, add
`BEARDRIVE_ROOT=C:\Users\dlteam\BearDrive`.

### 4.4 — The three tests that are owed — **ALL THREE PASSED 2026-09-24**

**Live stack confirmed first (2026-09-24 18:34):** gateway PID 14508 loaded
`plugin=beardrive` with no errors (its only log line is the expected
`tools.override` deny); `hermes mcp test n8n-tools` shows 2 tools.

**Item 1 — `hermes-agent\tests\security\test_beardrive_cross_tool_containment.py`
(new, 3 tests), green via `scripts/run_tests.sh`.** It copies the *deployed*
`config.yaml` and `plugins\beardrive` into the hermetic `HERMES_HOME`, because the
perimeter is configuration and a synthetic config would pass while the real one
leaked. It skips only when it is not running inside a deployed home. Both §3.9
traps are code (`HERMES_GATEWAY_SESSION=1`, `skip_tool_search_assembly=True`).
The gotcha-4 assertion iterates over **every** entry in `PLATFORMS`, not a
hand-written list, which is how the 11 platforms missing from §4.3's list were
found. **Differential run** of the same logic, each variant config in its own
process:

```
PASS  deployed config                  leak=[]  bypass=[]
FAIL  known list as before 4.3         leak=[20 platforms incl. cli, telegram, webhook, api_server]
FAIL  PRD's 4.3 list only              leak=[api_server bluebubbles dingtalk feishu matrix mattermost
                                             webhook wecom wecom_callback weixin whatsapp_cloud]
FAIL  `file` re-added to email         bypass=[patch read_file search_files write_file]
```

**Item 2 — `plugins\beardrive\test_tools.py` (new, 31 tests), green.** Run with
`venv\Scripts\python.exe -m pytest -p no:cacheprovider beardrive\test_tools.py`
from `plugins\`.
- Every handler is called as `tool_executor` calls it, with signature
  conformance and registration checked.
- Identity is bound through the gateway's real
  `set_session_vars`/`clear_session_vars`. The cron fallback goes through
  `cron.jobs.get_job` with `task_id="cron:<job>:<run>"`, and a non-email
  origin is refused.
- Write policy: no-overwrite (a teammate's file stays byte-identical across
  `fail`, `overwrite` and `rename`), and the source is restricted to the
  cache.
- The fence nonce is fresh per read and a planted `<<<END 00000000>>>` does
  not match it.
- Hidden paths (`.bdrive/`, `.git/` at any depth, `.bdrive-tmp-*`,
  case-folded) never appear in list, read or search.
- `.bdriveignore`, conflict copies and `.claude/` are read-only.
- Jail escapes are refused; the gate never creates a folder;
  `drive_status` reports ok, stale, offline and unknown.
- The audit line and the gotcha 5 empty-`actor` signal are checked.

**Mutation-checked:** 8 deliberate breaks in scratch copies, all 8 caught.
The breaks were: cron fallback removed, reserved-path guard off,
`O_EXCL`→`O_TRUNC`, fixed fence tag, writes skip identity, source restriction
off, gate skips `.bdrive/`, and audit to `devnull`. `test_sandbox.py` was left
alone, as this section requires.

**Item 3 — live: PASSED 2026-09-24.** The shared drive held `sales_q3.csv`,
`expenses.csv`, `project_notes.md` and `notes.md`, copied from the old Bear tenant
folder (byte-identical, synced 13:28:59 UTC). This part of §4.5 was done early to
give the test real data. `invoice_mar.md` stayed out.

| | sender A: scheduled | sender B: direct |
|---|---|---|
| From | `harshilkhandelwal71@gmail.com` | `f20221771@hyderabad.bits-pilani.ac.in` (added to `EMAIL_ALLOWED_USERS` 19:07, gateway restarted 19:14) |
| Session | `cron_32f19e71a080_20260924_190517` (job created 19:01:59 from the email, fired 19:05:17) | `20260924_194125_cc695722` (`platform=email`) |
| Tool calls | `tool_search`, `tool_describe`, `drive_list`, `drive_read sales_q3.csv` | the same four |
| Audit | `drive_read sales_q3.csv`, `actor: harshilkhandelwal71@gmail.com`, **`actor_source: cron-origin`** | `drive_read sales_q3.csv`, `actor: f20221771@…`, `actor_source: session` |
| Delivery | `delivered to email:harshilkhandelwal71@gmail.com via live adapter` | `Sent reply to f20221771@…` |

All three pass conditions hold:
- a `drive_read` in a `cron_*` session;
- an audit line with a non-empty actor and `actor_source: cron-origin`;
- a second sender reading the same path under a different actor.

Zero `read_file`/`search_files`/`write_file`/`terminal`/`execute_code`/
`delegate_task`/`computer_use` calls in either session. The answer (AMER
$931,000, grand total $2,467,000) matches §4.0. That is a side note only; the
pass condition was always the tool calls.

**Caveats, recorded honestly:**
- Sender B's display name is also "HARSHIL KHANDELWAL", so this is the same
  person using two allow-listed identities. That is enough for what the test
  proves: the audit keys on the address, and the two actors are distinct. A
  second *person* would still be worth a run.
- The model-written cron prompt still says "from your Bear folder" and "Send an
  email to the origin" (gotcha 16). Neither caused harm: it used `drive_*` and
  made no `send_email` attempt, and delivery went via origin. The first phrase
  is stale vocabulary, probably from session memory or from the resolver's
  byte-preserved "Bear folder" error strings.
- **Operational:** n8n exited twice within a minute of starting when run from
  a plain terminal. It stayed up once launched as
  `Start-Process powershell -ArgumentList '-NoExit','-Command','n8n start'`.
  The cause wasn't captured. While it was down, `sync_allowlist.py` found drift:
  `deep@lakeb2b.com` and `uma.s@championsmail.com` had been in `.env` but never
  in n8n. With the user's approval, all 5 addresses were synced and published;
  `--check` now exits 0.

1. **Cross-tool containment** (`tests/security/test_beardrive_cross_tool_containment.py`).
   Asserts `read_file`/`search_files`/`write_file`/`patch`/`terminal`/
   `execute_code`/`delegate_task`/`computer_use` are absent from the `email` and
   `cron` catalogs and the `drive_*` tools are present. Both §3.9 gotchas must be
   encoded as code, not comments: `HERMES_GATEWAY_SESSION=1` and
   `skip_tool_search_assembly=True`. **Plus a third assertion:** `beardrive` is
   not resolvable on `cli`/`telegram`/`discord`/`slack`/… — the regression test
   for §5 gotcha 4, which **fails against the config as it stands today**.
   `grep -rn BEAR_ROOT hermes-agent/` returns zero hits, so nothing anywhere
   asserts any of this yet.
2. **`plugins\beardrive\test_tools.py`** (new). The old `test_sandbox.py` passes
   `owner=` explicitly, so it never exercised `current_owner()`, the ContextVar
   path, the cron fallback, or the executor's calling convention — the blind spot
   that let the gotcha-4 `TypeError` through. **Do not "fix" `test_sandbox.py`
   for this**; it is a unit test of the jail and an explicit parameter is correct
   there. Add a second file that calls handlers exactly as `tool_executor.py`
   does: signature conformance, `fn({...}, task_id="", tool_call_id="abc")`,
   reads-without-identity succeed, writes-without-identity refuse, source
   restriction, no-overwrite, untrusted banner, hidden paths, audit line.
3. **Live drive-in-cron verification**, two emails from two different senders.
   Pass = a `drive_read` in a `cron_*` session **and** an audit line with a
   non-empty `actor` and `actor_source: cron-origin`, **and** a second sender
   reading the same path with a different `actor`. That last one is the entire
   proof the shared-folder migration did what it was for.

### 4.5 — Migration and cleanup — **Medium**

- Archive `C:\Users\dlteam\Bear` and `..\drafts\bear-shared-namespace` to
  `..\backups\bear-presharedmigration-<date>.zip`. There is no git in the Hermes
  home — the zip is the only rollback.
- Copy **four** files into `C:\Users\dlteam\BearDrive`: `sales_q3.csv`,
  `expenses.csv`, `project_notes.md`, `notes.md`. After `bdrive init`, so the
  first sync uploads them.
- **`invoice_mar.md` does NOT go in the shared folder** — §5 gotcha 6.
- Delete `plugins\bear\` (incl. `__pycache__`) and the drafts directory only
  **after** 4.4 passes. Do not park the old plugin in `plugins.disabled` as a
  hedge: a not-enabled plugin is inert either way, and a second `sandbox.py` on
  disk is a grep hazard during exactly the window you are debugging the new one.
- `bear_admin.py` is **retired, not ported** — its whole job was mapping sha256
  slugs back to email addresses, which has no referent now. `put` is replaced by
  dragging a file into Explorer, which is half the point of this migration.
- Update `..\PRD.md`: §2 paths, §3.6 and §3.7, §3.9 (add the gotcha-4 amendment),
  §4 items 1/2/4/6, §8 becomes historical. Also correct the parent's gotcha 15 —
  it says `cron/jobs.json` is "a dict keyed by job id"; the live file is
  `{"jobs": [...], "updated_at": ...}` — and note that
  `n8n-workflows\mcp_workflow.backup.json` is stale (shows only `hermes_ping`,
  not `send_email`).

### 4.6 — Excel/PDF reading and learning from team tasks — **BUILT; E2E 4/5 PASS (cross-member skill reuse still owed); containment breach in the pre-lockdown gmail session OPEN**

Requested by the user after §4.4. The workflow is: a member emails a task with a
file, the agent does it and reports back, and **the agent learns the kinds of
task the team assigns**, so a similar task from another member is cheaper and
faster.

**A. Excel and PDF in `drive_read`** (`plugins\beardrive\extract.py`, new).
- `.xlsx`/`.xlsm` come back as each sheet in CSV under `## Sheet: <name>`, via
  openpyxl `read_only=True, data_only=True`. That gives the values Excel
  cached, never formula source; a script-written workbook has blanks there.
  An optional `sheet` argument reads one sheet.
- `.pdf` comes back as text under `## Page N`, via pypdf. An optional `pages`
  argument takes `'3'` or `'10-20'`. A scanned PDF with no text layer says so,
  since there is no OCR. Password-protected PDFs are refused.
- `.xls` is refused with "save as .xlsx".
- Output is fenced and audited exactly like text files.
- Caps: 25 MB file, 5,000 rows × 100 columns per sheet, 300 pages, 1 MB of text.
  Every loop is bounded by these caps, not by what the file claims about itself.
- **Parsing is hostile-input safe.** openpyxl uses the venv's defusedxml
  (`openpyxl.DEFUSEDXML == True`, checked live).
- **openpyxl refuses a filename not ending in `.xlsx`.** That is every conflict
  copy (`x.xlsx.bdrive-conflict-…`), so it is handed a file handle instead. A
  new test caught this.
- **Dependencies are declared, not hand-installed:** `python_dependencies` in
  `plugin.yaml` is `openpyxl>=3.1.5,<4` and `pypdf>=6.0,<7`, installed through
  `hermes_cli.plugin_python_deps.install_for_plugin_dir`. `hermes update`
  re-applies declared deps; a hand install would be wiped.
- **Hermes' resolver has an `exclude-newer` cutoff** (2026-09-10,
  supply-chain protection) and refused pypdf 6.19.0 (published 09-16). The
  floor was lowered instead of bypassing it. Installed: openpyxl 3.1.5, pypdf
  6.18.0.
- Search (`drive_search`) stays text-only; Excel/PDF are found by filename.
  Word `.docx` is not supported; it's an easy follow-up with the same shape.
- Tests: `test_tools.py` is now 40 (9 new: sheets, one sheet, unknown sheet,
  formulas, corrupt workbook, PDF pages and ranges, no text layer, corrupt PDF,
  conflict-copy workbook, `.xls` hint). The PDFs are hand-built, so there are
  no fixtures on disk.

**B. Learning: Hermes built-in skills, human-approved, approved only at this PC.**
These were the user's choices over BearDrive playbooks and over use-immediately
or email approval. **This consciously reverses the §4.3 "no free-text write"
rule for one channel:** a learned skill is free text that shapes every later run
for every member, which is persistent cross-member influence. The control is a
human gate plus the removal of every email path to that gate.
- `platform_toolsets.email/cron` gain `skills` (`skills_list`, `skill_view`,
  `skill_manage`). `skills` is not a filesystem or exec toolset: the §3.9
  bypass list is still absent (re-probed).
- **`skills.write_approval: true`** is Hermes' built-in gate. Every
  `skill_manage` mutation, in a foreground turn *and* in the background review
  fork, is **staged**; nothing takes effect until someone at this PC runs
  `hermes`, then `/skills pending`, `/skills diff <id>`,
  `/skills approve|reject <id>`.
- **Slash gating for email.** `/skills` is `cli_only` but
  `gateway_config_gate="skills.write_approval"`, so turning the gate on
  *exposes* `/skills approve` and `/skills approval off` over email. With no
  `allow_admin_from`, `gateway/slash_access.py` lets every allowed sender run
  every slash command (true before this change too, for all commands). Fix:
  `platforms.email.allow_admin_from` and `group_allow_admin_from` are set to
  `[no-email-admins@admin.invalid]`. That enables gating with no email admin;
  non-admins keep `/help` and `/whoami`. Verified live for both real senders:
  `/skills` → refused, `/help` → allowed.
- **`skills.guard_agent_created: true`** scans agent-written skills. The
  default-off rationale is that "terminal() runs the same code ungated", which
  does not hold for an agent with no terminal.
- **`skills.platform_disabled.email/cron`** hides all 82 pre-existing skills
  (`hermes-agent` is essential and auto-exempt), so email/cron see only skills
  learned from team tasks. Without this the index listed ~83 bundled skills
  (Apple, smart home, and `pdf`/`xlsx`/`docx` skills that assume a terminal)
  under a "you MUST load if even partially relevant" instruction, which costs
  calls instead of saving them. The index is now 1,466 chars on email
  vs 8,507 on CLI. **A future `hermes update` that adds bundled skills will not
  be hidden automatically.**
- **How learning triggers:** (1) in-turn, `SKILLS_GUIDANCE` tells the agent to
  record a non-trivial workflow with `skill_manage`; (2) the background review
  fork runs after a turn once `_iters_since_skill >= creation_nudge_interval`
  (15, unchanged). The counter accumulates across turns of the long-lived email
  session. It costs ~30K tokens and is suppressed for cron
  (`skip_background_review`). Short tasks (~4 tool calls) mostly learn through
  (1).
- **Where approved skills land:** `hermes\skills\` (profile-local). They are also
  visible to CLI sessions, which *have* a terminal. That is one more reason the
  human gate matters.
- **Test:** the containment suite gains
  `test_learned_skills_are_staged_and_no_emailer_can_approve_them`: if
  `skill_manage` is reachable from email/cron, `write_approval` must be on,
  gating must be enabled in both scopes, no sender may run `/skills`, and every
  admin id must be `.invalid`. It is 4/4 green, and a differential run fails
  each of *write_approval off*, *no admin list*, and *real sender as admin*.
- Backups: `config.yaml.bak-20260924-204042`,
  `backups\beardrive-plugin-20260924-202436` (outside `plugins\`, so it is not
  discoverable as a second plugin), `PRD.md.bak-20260924-204307`.

**E2E step 1 result, 2026-09-24 20:50: FAILED on containment — OPEN, user-accepted.**
The step-1 email (`q4_sales.xlsx`, harshilkhandelwal71@gmail.com) ran in session
`20260919_005542_f92f6e60`, which started on 09-19, **before the 09-21 lockdown**. A
continuing session reuses its stored system prompt **and its saved `tool_names`**
(`agent/conversation_loop.py` ~:682-741, `restore_agent_tool_prefix`). This session's
list still includes `terminal execute_code read_file write_file patch search_files
delegate_task` plus browser tools. The agent:
- ran Python via `terminal` 4 times ("auto-approved by smart approval");
- `find`-ed and **copied the file into the retired `C:\Users\dlteam\Bear\`**, not
  BearDrive;
- staged skill `a0d06221` (`bear-xlsx-sales`) encoding that method.

The answer was correct (AMER 772,000 / EMEA 732,000 / APAC 566,000), but the route
bypassed the whole jail.
- The email adapter's binary-attachment note tells the agent *"extract the
  document's text yourself — for example with the terminal tool"*. This is why it
  reached for `terminal`.
- `a0d06221` was **rejected** by the user at the CLI and confirmed not on disk.
  (Claude Code's classifier blocked the agent from rejecting it on the user's
  behalf.)
- A read-only audit of `state.db` found 2 email sessions: only `20260919_005542_f92f6e60`
  carries the bypass tools. `20260924_194125_cc695722` (f20221771@…) is clean.
- **Every earlier "lockdown verified" result was measured on fresh sessions or
  offline probes.** None of them could see a pinned pre-lockdown session. That
  includes §4.0, the §4.3/§4.4 catalog probes, and the containment test.
- **Proposed and declined by the user (2026-09-24):**
  1. allow `/new` for email senders (`user_allowed_commands: [new]`), so the old
     session can be ended and members can pick up newly approved skills;
  2. steer attachments to `drive_save_attachment` → `drive_read` in the tool
     descriptions.
- **So as of now:** email from harshilkhandelwal71@gmail.com still reaches a
  terminal on this PC. Any session started after the lockdown only sees newly
  approved skills if it is new, and non-admins cannot send `/new`
  (slash gating).
- Also left behind: `C:\Users\dlteam\Bear\q4_sales.xlsx` (the §4.5 archive will
  sweep it).

**Reachability, checked 2026-09-24:** the hub is reachable **only from this PC**.
`wslrelay` listens on `::1:4173` alone: `http://localhost:4173` → 200, while
`127.0.0.1:4173` and the LAN address `192.168.1.6:4173` get no connection. There is
no portproxy and no tunnel. Teammates can add files only by email attachment. The
web UI has no upload in any case. Direct access (Tailscale or LAN plus TLS, invites,
`bdrive` on each machine, WSL for Windows users) was scoped and **declined by the
user**.

**E2E results, 2026-09-24/25, sender f20221771@hyderabad.bits-pilani.ac.in, session
`20260924_194125_cc695722` (post-lockdown, no bypass tools).** Sample files:
`C:\Users\dlteam\Desktop\beardrive-e2e\{q4_sales.xlsx, h1_sales.pdf}`. The gateway
was restarted 23:27:36 to load the Excel/PDF code; it had run since 19:14.

| # | Test | Result |
|---|---|---|
| 1 | `.xlsx` attachment → save → read → answer | **PASS.** 23:37. The agent first tried `terminal` twice, following the adapter's "use the terminal tool" hint; both were refused (`Tool 'terminal' does not exist`). It then found `drive_save_attachment` via `tool_search`. Audit: `drive_save_attachment q4_sales.xlsx` 18:08:19 UTC and `drive_read … format: xlsx`, both `actor: f20221771@…, actor_source: session`. Synced 18:08:20. Answer correct: AMER 772,000 / EMEA 732,000 / APAC 566,000. Cost: 10 API calls, 13 tool turns (one wasted on gotcha 18 batching). |
| 2 | Learned skill is staged, not live | **PASS.** `skill_manage` → `staged: true`, pending `03a35120`; not in `skills_list`. |
| 3 | Approve at the CLI | **FAILED, then PASSED on retry.** `/skills approve 03a35120` → `Description is 192 chars — new skills must fit the 60-char system-prompt budget … batch aborted, all touched skills rolled back`. Its example also carried real Q3 figures ($931,000 etc.), which a weak model could copy instead of computing. The user rejected it. A follow-up email asked the agent to re-record it (description ≤ 60 chars, no real numbers); the result was pending `77e83e05` (60-char description, `$X,XXX,XXX` placeholders, drive tools only). The agent reviewed the full text and the user approved it: `skills\business-analytics\sales-drive-workflow\SKILL.md`. |
| 4 | `.pdf` attachment → save → read → answer | **PASS.** 00:05. Audit: `drive_save_attachment h1_sales.pdf` 18:35:07 UTC and `drive_read … format: pdf`. Answer correct: AMER 1,525,000 / EMEA 1,470,000 / APAC 1,100,000; the per-rep figures exist only in the PDF. Cost: **3 API calls, 2 tool calls, no terminal attempts, no tool search.** |
| 5 | Another member reuses the learned skill | **NOT PROVEN — still owed.** Test 4 made **no** `skill_view`/`skills_list` call. The session's stored system prompt has **no `## Skills` section**: it began 19:41, before learning was enabled at 20:41, and continuing sessions reuse their stored prompt. The 10 → 3 call drop came from **conversation history** (the Excel turn 30 min earlier in the same thread), not the skill. A different member's session would not have that history. |

**Findings from this run:**
- **Hermes gap:** `skill_manage` stages a write it cannot later apply. The
  60-char description rule (`SKILL_PROMPT_DESC_LIMIT`,
  `tools/skill_manager_tool.py:180`, rejects `> 60`) is checked only at approval,
  so the agent was told "staged" for a skill that could never go live. Expect
  approvals to fail on long descriptions. The fix is to ask the agent to
  re-record it.
- **A skill learned inside a session is not visible to that same session's index**
  (frozen prompt), and continuing email sessions never pick up new skills. Only
  a **new** session sees them. With slash gating on, members cannot send `/new`
  (the user declined allowing it). So in practice skills reach only members
  whose conversation starts after the approval.
- **Emails are silently lost across a gateway restart.** On connect the adapter
  marks every existing inbox message seen (`IMAP connection test passed. 34
  existing messages skipped`, `adapter.py` ~:437-448). A mail that lands before
  the restart completes is never processed. Wait for `✓ email connected` before
  sending.
- **Only UNSEEN Inbox mail is fetched** (`adapter.py:531`). A message opened in
  the agent's Gmail, or filtered to Spam, is never picked up. Delivery from the
  bits-pilani address took ~5 min each time.
- **The adapter's binary-attachment note** ("extract … with the terminal tool")
  costs a locked-down session wasted calls every time. The steering fix was
  declined by the user.

**Still owed for §4.6:** test 5. It needs an email from an allow-listed address
with **no existing session**: `deep@lakeb2b.com`, `uma.s@championsmail.com` or
`hemang.k@championsmail.com`. **Not** harshilkhandelwal71@gmail.com, whose
session is the pre-lockdown one with `terminal`. Pass = `skill_view
sales-drive-workflow` before the drive calls, plus a correct answer.

---

## 5. Gotchas

1. **`os.Chmod` on drvfs blocks all inbound sync, silently.**
   `internal/syncer/syncer.go:1898` chmods before rename when materializing a
   teammate's change. `/mnt/c` automounts *without* `metadata` by default, so
   chmod can return `EPERM` — and the failure goes into `materialize`'s `skip()`,
   which is correct design (no permanent wedge) but means the daemon looks
   perfectly healthy while receiving nothing. Invisible with one machine; total
   inbound failure the day someone joins. Probe, 60 seconds:
   ```sh
   touch /mnt/c/Users/dlteam/BearDrive/.p && \
     chmod 0644 /mnt/c/Users/dlteam/BearDrive/.p && echo CHMOD_OK
   ```
   Fix is configuration (`metadata` in `/etc/wsl.conf`), not a fork change —
   prefer it; the fork fix would diverge on a security-relevant line
   (`safeMode`, `syncer.go:1861`, exists to strip setuid).

2. **`/mnt/c` works because there is no inotify.** Verified: no notify
   dependency in `go.mod`/`go.sum`, and `internal/daemon/daemon.go:625` is
   `case <-time.After(scanInterval)` — an unconditional `filepath.WalkDir` every
   3s. The one mechanism that reliably breaks on drvfs is one BearDrive does not
   use. Corollary: `ResolveMount`'s `Dev`/`Ino` self-heal is a **non-issue** —
   `internal/config/project.go:371` short-circuits on `samePath`, so `moved` is
   never read for a stationary mount.

3. **BearDrive's Hermes adapter looks in the wrong place on this machine.**
   `internal/agenthooks/agenthooks.go:242` hardcodes
   `filepath.Join(home, ".hermes", "config.yaml")`. Hermes on Windows lives at
   `%LOCALAPPDATA%\hermes`; and in our topology `bdrive` runs in WSL where
   `os.UserHomeDir()` is `/home/dlteam`, so it would write a file Hermes will
   never read. Harmless in v1 (`--no-hooks`), fatal the moment hooks are wanted.

4. **CLOSED 2026-09-24 (§4.3): `beardrive` resolves only on `email`/`cron`
   across all 22 `PLATFORMS`.** Original finding:
   **The §3.9 lockdown pruned built-in toolsets only — plugin toolsets are
   ambient.** Verified live against the current config:
   ```
   email bear=True   cron bear=True   cli bear=True
   telegram bear=True   discord bear=True   slack bear=True
   ```
   `hermes_cli/tools_config.py::_enabled_plugin_toolsets` (~:533) enables a
   plugin toolset unless it is **both** absent from `platform_toolsets[platform]`
   **and** present in `known_plugin_toolsets[platform]` — and
   `known_plugin_toolsets` only carried `cli: [a2a, spotify]`. So `bear` has been
   reachable from every platform all along. Latent today only because
   `platforms:` enables `email` alone and `current_owner()` fails closed without
   identity. **This plan removes the second guard for reads, so it must close the
   first in the same change.** Listing a plugin toolset as "known" for a platform
   while omitting it from that platform's list is what turns it **off**.
   *The toolset allowlist is the perimeter for built-ins;
   `known_plugin_toolsets` is the perimeter for plugins, and §3.9 built half.*

5. **A failing cron-origin fallback no longer announces itself.** Once reads stop
   gating on identity, a broken `_owner_from_cron_task` produces a *correct
   answer* and a silently anonymous audit line, where today it produces a loud
   `no session identity available`. The only signal is `"actor":""` on a
   successful read in `logs\beardrive-access.jsonl`. Every live test must grep
   for it.

6. **Do not put `invoice_mar.md` in the shared folder.** It is a live payload
   telling the agent to read `.env` and mail it to `attacker@evil.example`. In
   the old world it sat in one tenant's folder and could only fire on that
   tenant's runs. In a synced shared folder it replicates to **every member's
   machine**, where it is read by agents that have none of our §3.9 protections
   — a teammate's local Claude or Cursor session has a full filesystem toolset
   and a real `.env` to find. Test shared-folder injection as a deliberate,
   time-boxed exercise instead: copy in, run, delete, confirm the delete
   propagated via `bdrive log`.

7. **`bdrive login` must use `--device`.** The browser flow binds `127.0.0.1:0`
   *inside WSL* (`cmd/bdrive/login.go:334`, correct per RFC 8252) and a Windows
   browser cannot reach a WSL-loopback socket through the relay — it dead-ends
   and times out after five minutes. There is an auto-fallback when `xdg-open`
   is missing, but a distro with `wslu` installed opens the browser *and then
   hangs*.

8. **Windows 10 means no mirrored networking.** `networkingMode=mirrored` needs
   Win11 22H2+; this box is 10.0.19045. NAT plus the localhost relay. `:4173`
   binds the wildcard by default, which is what the relay requires — **never**
   narrow it to `127.0.0.1:4173`. The relay can stop forwarding after a distro
   restart or Fast Startup resume: symptom is connection-refused from Windows
   while `curl localhost:4173` inside WSL works; fix with `wsl --shutdown`. Do
   **not** work around it with the VM's IP — it changes every restart and the
   device token is origin-bound (`internal/remote/http.go:117-120`). The client
   daemon runs inside WSL, so a relay hiccup degrades the web UI, never sync.

9. **`internal/daemon/daemon.go:474` compares paths by raw string equality**, not
   `samePath` (which `internal/config/project.go:296-303` uses). On a
   case-insensitive filesystem `/mnt/c/users/...` and `/mnt/c/Users/...` open the
   same directory and differ as strings — the daemon logs
   `mount re-registered at …; exiting` and dies on its first tick, forever, while
   `bdrive status` says `daemon: stopped` right after you started it. Spell the
   path identically everywhere. One-line upstream fix.

10. **Case collision between peers propagates a real delete.** The state cache is
    keyed by byte-exact relative path and `journal.SafePath` does no folding. A
    macOS/Linux teammate creating both `Notes.md` and `notes.md` collapses to one
    NTFS file; the scan's delete pass (`syncer.go:940-960`) then mints a genuine
    `KindDelete` for the vanished cache key and pushes it — **deleting their file
    for everyone.** Note the repo *does* fold case for the security-critical
    reserved names (`project.go:43-58` handles APFS/NTFS trailing dots and
    spaces); the gap is specifically content-path collision. Detect:
    `find . -type f | tr 'A-Z' 'a-z' | sort | uniq -d` — any output is live.

11. **Renaming a file twice destroys its version history.**
    `internal/webapp/moves.go:82-166` pairs create/delete by `(device, blob)`;
    after two renames of unchanged content the bucket holds two of each, the
    one-to-one check declines, and the chain is **dropped**, not mispaired.
    `restore` then dead-ends. Skipped repro:
    `TestMoveHistoryChainSurvivesTwoRenames` (`moves_test.go:381-449`). **This
    will hit us specifically** — on a case-insensitive filesystem
    `Foo.md → foo.md` *is* a rename. Mitigation without code: `moveWindow` is 30s
    (`moves.go:35`) and pairing requires `|Δt| ≤ moveWindow`, so renames of the
    same file more than a minute apart pair correctly. Signal: `bdrive log <file>`
    shows one row for a file you know has many versions.

12. **An open Office document never syncs, with no warning at all.** `os.Open`
    fails through 9p on an exclusively-locked file and `syncer.go:920` returns
    `nil` — no log line, no counter, no `status` field — while
    `bdrive scope --explain` lists it as in-scope, which makes it worse.

13. **A torn read leaves a junk version in history permanently.** An in-place
    Windows write caught mid-scan is hashed and journaled; the next 3s tick
    corrects *current* state, but blobs are retained forever, so the bad version
    stays in `bdrive log`. Self-healing for correctness, permanent in the record.
    Prefer editors that save atomically.

14. **Storage is unbounded by design.** Every version of every file is retained
    forever, locally *and* on the hub. `warnBigFolder` warns once at `init` and
    never again. Review `.bdriveignore` **before** the first push. `du -sh
    ~/.bdrive/volumes/*/blobs ~/bdrive-store` is the check.

15. **`bdrive init` must not be run from a lowercased path**, and the hub must be
    upgraded before clients (`CHANGELOG.md:9`, "Deploy hubs before clients").
    Same binary on one machine today, so no skew — but the ordering stops being
    automatic the day a teammate has a client elsewhere.

16. **A cron run cannot address an outbound email, and will guess.** `chat_id` is
    bound to `""` on purpose, so the requester's address is absent from the session.
    Observed 2026-09-22: the model tried `origin`, `harshil`, then
    `origin@nousresearch.com` before giving up. Harmless here only because the n8n
    guard refused all three. Never write a cron prompt that says "email X" — say
    what to report and let `deliver`/`origin` route it.

17. **`hermes_ping` is broken and costs a turn every run.** Returns
    `"query is not defined [line 1]"` — parent PRD gotcha 5: the n8n Code node's JS
    variable is `query` while the MCP schema exposes `input`. Agents call it first
    as a health check, burn an API call on the error, then recover. Fix the node.

18. **The model batches local tool calls; Hermes refuses batches.**
    `{"calls": [bear_read notes.md, bear_read project_notes.md]}` returned
    `"Local tools require one entry per tool_call; mixed and multi-local batches are
    not supported."` Recoverable (it retried singly) but it costs a round trip and
    it looks like a Bear failure in a log skimmed for `returned error`.

19. **Hermes's MCP circuit breaker masks the n8n guard.** After 3 rejected calls
    `n8n-tools` was paused ~2s with *"Do NOT repeat the same call"*, and the 3rd
    `send_email` never reached n8n. **The n8n execution log therefore undercounts
    attempts** — 2 executions for 3 tries on 2026-09-22. When auditing egress,
    reconcile `logs\agent.log` against the execution log; neither alone is complete.
    This sharpens parent gotcha 7 rather than contradicting it: the execution log is
    ground truth for what n8n *did*, not for what was *attempted*.

20. **The email agent's interactive session is effectively immortal.** The
    2026-09-22 email was handled in session `20260919_005542_f92f6e60` — created
    2026-09-19, still live three days later. That is parent gotcha 10 in practice
    (*"Only explicit suspension replaces a routed conversation; time never does"*)
    and the live form of parent §4 item 3. Cron runs are unaffected: they get a
    fresh `cron_*` session with `history=0`, which is why §4.0 was a clean test.

21. **Editing these files with Python rewrites every line ending, silently.** The
    Hermes tree is **LF**; the root `PRD.md` is **CRLF**. Python's text mode on
    Windows translates `\n` to `\r\n` on write, so `open(p,'w').write(s)` turns a
    1,168-line LF file into a 1,168-line CRLF file — a diff where *every* line
    changed, and, with no git here, no cheap way to see you did it. Hit twice on
    2026-09-22 (`cronjob_tools.py`, `Bear_Drive\PRD.md`), caught both times only by
    reading the diff. Read and write bytes, or pass `newline=''` to both `open`
    calls, and check afterwards:
    ```sh
    tr -cd '\r' < FILE | wc -c     # must match the original
    ```

22. **A same-size rewrite within one NTFS tick can be missed. ACCEPTED 2026-09-24.**
    Measured on `/mnt/c`: 40 rapid writes gave 19 distinct mtimes, in steps of
    about 16 ms; ext4 gave 40 of 40. The scan's "unchanged" test is size + mtime
    only (`internal/syncer/syncer.go:915`, cheap path, no re-hash). So if a file
    is rewritten **with the same size** inside one tick, **and** a scan (every 3s)
    hashes it between the two writes, the second version is never journaled
    until the file changes again. The materialize dirty checks
    (`syncer.go:1560`, `:1688`, `:1726`) share the same blind spot. For human
    edits this is close to impossible; for agents doing fast same-size rewrites
    it is rare, silent and real. No config fixes it. The fork fix is git's
    "racy clean" rule: re-hash when the cached mtime is within one tick of the
    scan time. Candidate for §7, not v1. Signal: `bdrive log <file>` missing a
    version you know you wrote.

23. **Symantec Endpoint Protection drops all outbound TCP from the WSL2 NAT.**
    Observed 2026-09-24: from WSL, DNS (UDP to 172.19.176.1) and ICMP work;
    every TCP connect times out (`curl -4 https://1.1.1.1`, `https://go.dev`,
    `http://example.com`). The same request from Windows returns 200; there is
    no WinHTTP or IE proxy. SEP is the registered firewall product. Windows →
    WSL `localhost:4173` through the relay **works**, so the hub and sync are
    unaffected. `apt`, `go get` and anything else in WSL that needs the
    internet will fail. Workaround used: download on Windows, copy in, build
    offline. Do **not** tunnel WSL traffic around SEP. A real fix is an IT
    exception for the `vEthernet (WSL)` subnet.

24. **WSL stops an idle distro, and the hub goes with it.** With no `wsl.exe`
    client attached, Ubuntu went `Stopped` within 80s even though systemd, linger
    and a running user unit were all in place. §4.1 step 9's logon task
    (`--exec /bin/true`) only *starts* the distro; it exits immediately and
    would not have kept it running. Fixed in `C:\Users\dlteam\.wslconfig`
    (`instanceIdleTimeout=-1`, `vmIdleTimeout=-1`). The cost is that the WSL VM
    stays resident on an 8 GB box (about 0.7 GB in use at idle). Related:
    Claude Code reaps its own background shells under memory pressure, so
    never run the hub as a foreground process under an agent's shell. It
    happened once on 2026-09-24 and is why the hub is a systemd unit.

---

## 6. ~~Open decision~~ — **DECIDED AND APPLIED 2026-09-22: `deliver` dropped from `CRONJOB_SCHEMA`**

Option 2. Rationale unchanged, and now backed by a live demonstration rather than
code reading.

### What the 2026-09-22 run proved

Job `c8e296de2edf` ran the two egress paths side by side:

```
guarded path   agent -> mcp__n8n_tools__send_email -> Guard (Code node)
               to:"origin"   -> exec 16  status=error  "recipient not permitted"
               to:"harshil"  -> exec 18  status=error  "recipient not permitted"
               (3rd attempt never reached n8n — Hermes MCP circuit breaker)

unguarded path scheduler -> platform adapter
               "Job 'c8e296de2edf': delivered to email:harshilkhandelwal71@gmail.com
                via live adapter  message_id=<hermes-b43c2299d9ca@gmail.com>"
```

Same content (`AMER $931,000`), same run. The guard refused every attempt the model
made; the scheduler shipped it anyway without consulting the guard. That is the
hole, observed, not inferred.

### A second finding that settles the design

**A cron agent cannot correctly address `send_email` even when it wants to.** Cron
binds `chat_id=""` deliberately, so the requester's address is not in the session.
Asked to "email the origin", the model guessed `origin`, then `harshil`, then
`origin@nousresearch.com` — all refused, correctly. It then tried to read
`profiles/default/config.yaml` to find the allowlist, and the jail refused that too.

So `send_email` is not merely unguarded-adjacent in cron, it is structurally
**unusable** there. Delivery in cron is `deliver`/`origin`'s job and nothing else's.
Keeping a model-visible `deliver` invites the model to aim an egress path it has no
information to aim correctly — the exact failure above, minus the guard.

### The change is smaller than this document assumed

`cronjob_job_args.py:51` documents that **an omitted `deliver` already defaults to
"origin-or-local"**. So no new defaulting logic is needed:

1. Remove the `deliver` and `failure_deliver` properties from
   `CRONJOB_SCHEMA["parameters"]["properties"]` (`tools/cronjob_tools.py`, the two
   blocks at ~`:1042` and ~`:1046`).
2. Remove both names from `_HANDLER_FORWARDED_ARGS` (~`:1116`).
3. Leave the plumbing untouched — `_normalize_deliver_param`,
   `_validate_bot_chat_deliver`, `_resolve_cron_context_deliver`
   (`cronjob_job_args.py:211`) all stay, and the CLI lane keeps passing `deliver`
   directly into `cronjob()`. The schema is the model-visible surface only; the
   comment at `cronjob_tools.py:611` already establishes this CLI-only pattern for
   `model`/`provider`.
4. Regression test: assert `deliver` and `failure_deliver` are **absent** from
   `CRONJOB_SCHEMA["parameters"]["properties"]`, and that a job created without
   `deliver` from an email session still resolves delivery to its `origin`.

**Not yet applied.** It was deliberately held back during §4.0 so a schema change
could not become a candidate cause for a failing gate. §4.0 has now passed, so it
is safe to apply — it is the next code change, and it is independent of the §4.1a
firmware blocker.

### 6.1 What shipped, and how it was verified

**`tools\cronjob_tools.py`** (backup `...bak-20260922-162833`):

- The `deliver` / `failure_deliver` property blocks are deleted from
  `CRONJOB_SCHEMA`, replaced by a comment recording *why* and citing `c8e296de2edf`.
- Both names removed from `_HANDLER_FORWARDED_ARGS`. **This matters on its own:** the
  handler forwards with `args.get(key)`, so leaving the names there would keep passing
  a `deliver` the model invented even after the schema stopped advertising it. Schema
  removal alone would not have closed the path.
- `attach_to_session`'s description rewritten — it explained itself in terms of
  `deliver='origin'` / `'slack'` / `'local'`, a parameter the model can no longer set.
  Behaviour unchanged; it now names CLI-created jobs as the case that has those.
- Nothing else touched. `_normalize_deliver_param`, `_validate_bot_chat_deliver`,
  `_resolve_cron_context_deliver` and `cronjob()`'s own `deliver=None` keyword all
  remain, so the CLI lane is untouched.

**`tests\cron\test_cron_deliver_not_model_settable.py`** (new, 7 tests): absent from
the static schema; absent from the **dynamic** per-profile rebuild
(`_cronjob_schema_overrides` deep-copies, so the removal must survive the copy);
absent from `_HANDLER_FORWARDED_ARGS`; a supplied `deliver` is **not** forwarded to
`cronjob()`; and an email-session job created with no `deliver` argument still stores
`origin: {platform: email, chat_id: …}`, while the formatted job returned to the model
carries no `origin` key at all.

**Verified differentially, not just "the tests pass".** `tests/cron/` was run against
the modified tree and against the pristine backup, and the failure sets compared:

```
modified : 16 failed, 1415 passed, 40 skipped
original : 22 failed, 1409 passed, 40 skipped
new failures introduced by the change : NONE
failures only in the original         : exactly the 6 new regression assertions
```

The 16 are identical in both runs — pre-existing Windows/POSIX failures
(`test_file_permissions` expecting 0600/0700, `test_lifecycle_guard_*`, and
`test_cron_workdir::test_tilde_expands`, which fails because `expanduser("~")` reads
`USERPROFILE` on Windows rather than the monkeypatched `HOME`). All were confirmed
failing against the untouched original.

The 6 that fail *only* against the original are the point: the new tests detect the
old behaviour, so they catch a revert instead of passing vacuously.

Gateway restarted 16:52 and the live catalog re-probed: `cronjob_manage` now serves
18 properties with `deliver`/`failure_deliver` absent **at the serving layer**, not
merely in the module literal.

**Not done, deliberately:** no live end-to-end re-test of cron delivery after the
change. `deliver: origin` resolution is covered by the new test plus the pre-existing
`TestLocalDeliveryNotice` cases, and the next real scheduled job exercises it for free.

## 7. Fork changes — scoped, not for v1

Upstream is at PR #244 and active (last commit 2026-09-20). Branch layout:
`main` a pristine mirror (ff-merge only), `deploy` what runs, `local/wsl` and
`local/ops` never upstreamable, `fix/<slug>` one general fix each off `main`.

**The rule that makes cherry-picking work: never mix categories in one commit.**
Use `fix(...)`/`feat(...)` vs `hack(wsl)`/`ops(...)` so the type alone names the
bucket, plus an `Upstream-Candidate: yes|no` trailer — then
`git log --grep='^Upstream-Candidate: yes' main..deploy` is your standing list of
PRs owed. Merge, don't rebase, onto `deploy`. Note
`.claude/hooks/check-arch-diagrams.sh` blocks `gh pr create` when `cmd/` or
`internal/*.go` changed without `architecture/`.

| Change | File:line | Upstreamable |
|---|---|---|
| **A.** Resolve the Hermes config path from `$HERMES_HOME`, `%LOCALAPPDATA%` on Windows | `internal/agenthooks/agenthooks.go:242` (also `:223-226`, `:573-618`) | The env-var/`%LOCALAPPDATA%` half **yes**; the `/mnt/c` bridge is ours. Two commits. |
| **B.** `wsl.exe` shim for the hook command | `agenthooks.go:125-130`, `:137-140`, `:145`; consumed at `:588`, `:594-596` | No. The *seam* might be. Write the shim as a single `.cmd` on disk that the hook invokes — inlining through two quoting regimes is a nightmare. stdin must survive `wsl.exe` or every change silently loses its session note. |
| **C.** `samePath` instead of raw string compare | `internal/daemon/daemon.go:474` | **Yes** — one line, removes gotcha 9 permanently. Good first PR. |
| **D.** Fold-collision refusal in scan/materialize | `internal/syncer/syncer.go:940-960` | **Yes** — a genuine upstream bug, not a WSL one (APFS is case-insensitive by default; `project.go:39` already names it). File the issue with a repro *before* writing a local fix. |

---

## 8. What we are knowingly accepting

- **Cross-member prompt injection is not solved, and v1 does not solve it.** Any
  member can plant a payload that fires on another member's run. The controls are
  the `untrusted_content` label (strengthened to a delimiter block, since the
  bottom of the model fallback chain will not reliably treat a sibling JSON key
  as a trust boundary), the §3.9 toolset lockdown, and the n8n recipient
  allowlist — all three tested working (parent §3.8, execution `id=7`). Real
  defence in depth, but mitigation, not prevention. BearDrive's own guidance
  agrees: *"a synced folder is a shared drive, not a trusted source."* If this
  becomes unacceptable, the answer is BearDrive's shipped folder permissions — a
  write-only `drop/` and a curated read tree — not more plugin code.
- **Content scanning was considered and rejected.** Pattern matching over
  arbitrary team documents produces false positives on legitimate content (any
  doc discussing prompt injection trips it) and will not stop a competent
  payload. False confidence is worse than none.
- **No access audit inside BearDrive.** Folder-permission Phase 4 (revocation
  hygiene, an audit entry per rule change) is specified upstream but not built.
  Hermes-side lines in `beardrive-access.jsonl` are what we get — and because
  BearDrive attributes to the **device**, all members collapse to one author in
  `bdrive log`, which makes the Hermes audit log a requirement rather than a
  nicety.
- **No encryption.** The hub operator and anyone with store access sees
  everything. It is an authorization boundary only. Fine while the hub is this
  machine; re-decide before it moves.
- **BearDrive device tokens never expire.** Every revocation path works; a token
  nobody revokes is valid forever.
- **Nothing on Windows has ever been security-tested upstream**, and the Windows
  autostart tests have never executed. We avoid this entirely by keeping every
  `bdrive` process in WSL.
- **Staleness is silent in v1.** No turn hooks means `drive_read` can return a
  file a teammate replaced 30 seconds ago and the agent will state it as current
  fact in a reply. `drive_status` is the mitigation; it is the agent's only way
  to say "the drive hasn't synced since 06:00" instead of answering confidently
  from stale numbers.

---

## 9. Next session — start here

> **END OF DAY 2026-09-25 02:20 — resume here.**
> 1. **`/new` by email only works if the email has no subject, or the subject starts
>    with `Re:`.** `adapter.py` ~:636 prepends `[Subject: …]` otherwise, so the
>    text no longer starts with `/` and goes to the model as chat. The first try
>    (subject "new chat", 01:38) went into the old session as ordinary chat. The
>    retry (a reply with body `/new` only) had **not arrived by 02:19**. Check the
>    log for it. **The pre-lockdown gmail session `20260919_005542_f92f6e60` (with
>    `terminal`) is still live** until it arrives.
> 2. **deep@lakeb2b.com emailed for real at 01:52** ("campaign's performance"). It
>    started a fresh session `20260925_015251_df4e5754` (post-lockdown, so its
>    index includes the learned skill). Tools used: `drive_list`,
>    `drive_search`×2, `drive_save_attachment`, `drive_read`×2, `skills_list`,
>    `skill_view`×2, plus tool-search. **No bypass tools.** It saved
>    `Epicor_Lead_Walkthrough_24_Sep_1.html` (14.7 KB) into the shared drive,
>    which is now permanent in BearDrive history. It sent 5 replies in one turn.
>    To do: check *which* skill it viewed (a partial signal for test 5) and why it
>    sent 5 replies.
> 3. Still owed: §4.6 test 5 (clean cross-member reuse), §4.5 cleanup (zip the old
>    Bear folder and drafts, delete `plugins\bear\` on the user's OK, update the
>    parent PRD). n8n still isn't a service; restart it with
>    `Start-Process powershell -ArgumentList '-NoExit','-Command','n8n start'`.
> 4. Gateway home-channel notices (shutdown and startup) go to the agent's own
>    inbox, `harshilchamp676@gmail.com`. They're harmless, and dropped as
>    non-allowlisted on ingest.
>
> **UPDATE 2026-09-25 01:32: `/new` now allowed for email senders.** The user
> reversed the earlier decline. `platforms.email.user_allowed_commands: [new]`
> (backup `config.yaml.bak-20260925-013146`). Verified: `/new` and `/reset` →
> allowed for both senders; `/skills` and `/model` → still refused. Containment
> suite 4/4. **Takes effect after a gateway restart.** Next: email `/new` from
> harshilkhandelwal71@gmail.com to retire the pre-lockdown `terminal` session,
> optionally from f20221771@… too, then §4.6 test 5.
>
> **UPDATE 2026-09-25 01:20: §4.6 E2E run.** Excel and PDF via email → BearDrive →
> answer: PASS. Staged → approved skill `sales-drive-workflow`: PASS on the second
> attempt (the first hit the 60-char description rule at approval time).
> Cross-member reuse: **NOT yet proven**; it needs a first email from a new
> allow-listed address. **OPEN and user-accepted:** harshilkhandelwal71@gmail.com's
> pre-lockdown session still has `terminal`. Details: §4.6 "E2E results".
>
> **UPDATE 2026-09-24 20:45: §4.6 built** — Excel/PDF reading and learning via
> Hermes skills (human-approved, at this PC only). It needs a gateway restart,
> then the §4.6 E2E test. §4.5 cleanup is still open and was deliberately
> deferred.
>
> **UPDATE 2026-09-24 19:48: §4.4 is closed — the live two-sender test passed**
> (cron-origin read by one sender, session read of the same file by another). The
> four data files are already in the shared drive. **Next: the rest of §4.5**:
> archive `C:\Users\dlteam\Bear` + `drafts\bear-shared-namespace` to a zip, delete
> `plugins\bear\`, and update the parent `..\PRD.md`.
>
> **UPDATE 2026-09-24 18:41: §4.4 items 1 and 2 are green** (3 + 31 tests, both
> mutation- or differential-checked). The stack is up. **Next: §4.4 item 3, the live
> two-sender test**, then §4.5.
>
> **UPDATE 2026-09-24 (session 3, later): §4.3 is done** — the plugin is written and
> `config.yaml`/`.env` are applied; gotcha 4 is closed for all 22 platforms. See
> §4.3 "As built". The step 9 logon task **is registered** (verified: `BearDrive -
> start WSL`, Ready). **Next:** start n8n and the gateway (§9 "Bringing the stack
> back up"), then §4.4.
>
> **UPDATE 2026-09-24 (session 3): §4.1a and §4.1 are closed.** BearDrive is up in
> WSL2 and syncing `C:\Users\dlteam\BearDrive`. **Next is §4.3** (plugin rewrite,
> closing gotcha 4 in the same `config.yaml` edit), then §4.4 and §4.5.
> `BEARDRIVE_ROOT` = `C:\Users\dlteam\BearDrive`. `hub-test.md` there is a GATE
> artifact; it can be deleted, but its history stays (gotcha 14).
>
> **Check it's up:** `curl http://localhost:4173/auth/login` from Windows → 200;
> `wsl -d Ubuntu-24.04 -- bash -lc "systemctl --user is-active bdrive-hub beardrive; cd /mnt/c/Users/dlteam/BearDrive && bdrive status"`.
> If Windows gets connection-refused while it works inside WSL: `wsl --shutdown`
> (gotcha 8). **Still open:** whether the user registered the step 9 logon task
> (§4.1, "As built").
>
> **Session 3 changed on disk (no git; these are the rollbacks):**
> `Bear_Drive\PRD.md` → `PRD.md.bak-20260924-180644`; WSL `/etc/wsl.conf` →
> `/etc/wsl.conf.bak-20260923-181156`; `~/bdrive-hub/hub.json` →
> `hub.json.bak-20260924-113651`; **new** `C:\Users\dlteam\.wslconfig` (delete to
> revert). No change to the Hermes tree, `config.yaml`, `.env`, `plugins\bear\`,
> or the Windows BearDrive clone.
>
> The rest of this section is session 2's handoff, kept for the record.

**§4.0 and §4.2 are both closed — do not re-run them.** Exactly one thing now blocks
the whole project, and it is not code.

### What session 2 changed on disk (there is no git — these backups are the rollback)

| File | Change | Backup |
|---|---|---|
| `hermes-agent\tools\cronjob_tools.py` | §6 applied: `deliver`/`failure_deliver` dropped from `CRONJOB_SCHEMA` + `_HANDLER_FORWARDED_ARGS`; `attach_to_session` description reworded | `tools\cronjob_tools.py.bak-20260922-162833` |
| `hermes-agent\tests\cron\test_cron_deliver_not_model_settable.py` | **new**, 7 tests | n/a — delete to revert |
| `PRD.md` (root) | §3.8, §4 items 1/2/4, gotcha 5 updated | `PRD.md.bak-20260922-161312` |
| `Bear_Drive\PRD.md` | this document | `Bear_Drive\PRD.md.bak-20260922-161312` |

**No change was made to** `config.yaml`, `.env`, `plugins\bear\`, the BearDrive clone
(still pristine at `42739b0`), or `C:\Users\dlteam\Bear\`. To roll back §6 entirely:
restore the one backup, delete the one new test file, `hermes gateway restart`.

### Ordered next steps

1. **BIOS** — enable VT-x (below). Everything else waits on it.
2. `wsl --install -d Ubuntu-24.04`, then §4.1 steps 1–9, stopping at the **GATE**.
3. §4.3 plugin rewrite — and close §5 gotcha 4 in the same `config.yaml` edit.
4. §4.4's three tests, then §4.5 migration/cleanup.

§4.2's blocking status on §4.5 is discharged: the egress fix is in.

### THE blocker: enable virtualization in firmware (§4.1a)

Reboot → F1 → Security → Virtualization → Intel(R) Virtualization Technology →
Enabled. Verify before trusting it:

```powershell
(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled   # must be True
wsl.exe --install -d Ubuntu-24.04
```

Then §4.1 from step 1, stopping at the GATE. Nothing in §4.3–§4.5 can start before
that, because every `bdrive` process runs in WSL2 (§3, "bdrive on Windows").

**Do not start §4.3 (the plugin rewrite) early to fill the time.** It writes against
`BEARDRIVE_ROOT`, which cannot exist until the daemon is syncing — you would be
writing a jail for a folder whose behaviour is still unobserved, and §4.1's GATE
exists precisely to convert the `/mnt/c` drvfs analysis into evidence first.

**Do close §5 gotcha 4 whenever you next touch `config.yaml`** — it is live today,
independent of BearDrive, and §4.3 already requires it.

### Bringing the stack back up

```powershell
n8n start                    # terminal 1, localhost:5678
hermes gateway restart       # terminal 2
# run-local-model.cmd is the optional fallback floor; skip it on 8 GB
```

Sanity checks, all four green on 2026-09-22:

```powershell
hermes mcp test n8n-tools     # 2 tools
hermes fallback list          # primary + 4
hermes cron list
python ...\n8n-workflows\sync_allowlist.py --check   # exit 0
```

**Test interpreter** is `...\hermes-agent\venv\Scripts\python.exe` — uv-built,
**no pip**; install with `...\hermes\bin\uv.exe pip install`.

**Measure the toolset the right way** (parent §3.9). The working probe, run green on
2026-09-22, needs the real module paths — there is no `get_tool_definitions` in
`hermes_cli.tools_config`, and no `hermes_cli.config_utils` at all:

```python
import os; os.environ["HERMES_GATEWAY_SESSION"] = "1"
from model_tools import get_tool_definitions
from hermes_cli.tools_config import _get_platform_tools
from hermes_cli.config import load_config
ts = sorted(_get_platform_tools(load_config(), "email"))
get_tool_definitions(enabled_toolsets=ts, quiet_mode=True, skip_tool_search_assembly=True)
```

Expected today, for `email` and `cron`: exactly
`bear_list bear_read bear_save_attachment bear_search cronjob_manage`.

**§5 gotcha 4 is still live and unfixed.** The same probe returns `bear=True` for
`cli`, `telegram`, `discord`, `slack`, `whatsapp` and `signal`; `config.yaml` still
carries only `known_plugin_toolsets: {cli: [a2a, spotify]}`. Close it in the same
change as §4.3, as that section already requires.

## 10. Scope note

**Session 3 (2026-09-24):** BearDrive is built and running from the pristine
`42739b0` with no fork edits. §4.1's GATE turned the `/mnt/c` analysis into
evidence: gotcha 1 is closed by `metadata` (observed), and gotcha 22 is new and
accepted. The Hermes tree is still untouched. Everything below is session 2's
note, kept as written.

**Still nothing built of BearDrive itself.** The clone is pristine at `42739b0`,
the Hermes tree is untouched, and `C:\Users\dlteam\BearDrive` does not
exist. Session 2 changed no code: it closed the §4.0 gate, decided §6, and found
the §4.1a firmware blocker.

Everything recorded here came from reading the two codebases on 2026-09-22 —
the claims carrying file:line are verified against source, and the four marked
"verified live" (the ambient plugin toolset, the absent notify dependency, the
5-tool §3.9 catalog, the WSL distro state) were executed. The `/mnt/c` verdict
and the drvfs hazards are read from code, **not** yet observed on this machine;
§4.1's GATE exists to convert them from analysis into evidence.
