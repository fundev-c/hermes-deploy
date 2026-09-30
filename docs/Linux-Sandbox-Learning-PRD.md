# Linux deployment: sandboxed terminal, learning loop, task verifier

**Last updated:** 2026-09-30 · **Owner:** harshil · **Branch:** `linux-sandbox-learning-verifier`
**Box:** Ubuntu 24.04, 7.7 GB RAM, Hermes pinned at `0138269` with patches 0001–0005
**Status:** built, unit-tested and live on the gateway. The live email tests of the verifier and the
Champ skills are still pending (§8).

---

## 1. Goal

The email agent (and cron) should be able to do real work on files: count, clean, transform and
produce spreadsheets and reports. This must not give anyone who can email it, or anything hidden in
an attachment, a shell on this machine. The agent should also get better over time, and a second
opinion should check its work before a reply goes out.

**What prompted it (2026-09-30):**
- An email task hit the drive reader's 5,000-row cap.
- To get around it, the agent created a cron job with `enabled_toolsets: ["terminal"]`.
- Hermes let that per-job list override `platform_toolsets.cron`, so the job ran pip and Python
  **on the host**.
- The job was contained, and this PRD is the fix and the follow-on work.

## 2. Decisions (made with the owner)

| Topic | Decision |
|---|---|
| Terminal for email and cron | Yes, but only inside a Docker sandbox |
| How email and cron are split from the CLI | Separate Hermes profile `team`. The owner's CLI stays on the default profile |
| Sandbox network | Allowlist only: PyPI, npm, GitHub |
| Workspace | Fresh per session (tmpfs). Only approved skills and lessons persist |
| Learning feedback | The sender's reply ("good" / "bad: why"), and silence counts as good after 3 days |
| Learning approval | Lessons apply automatically (guarded). Skills stay staged for approval at the PC |
| Verifier on fail | Retry up to 2 rounds, then send with "Verifier concerns" |
| Verifier engine | Rule checks plus an LLM judge. Laya was evaluated and rejected (§6) |
| Champ skills | Docs & writing and Data & research, synced daily |

## 3. Architecture

```
 sender ──email──> Gmail IMAP ──> hermes-gateway (one process, multiplexed profiles)
                                     │  profile "team": email + cron
                                     │  toolsets: beardrive, skills, terminal, cronjob, learning
                                     ├─ terminal ──> docker: hermes-sandbox:1  (network hermes-sbx, --internal)
                                     │                 └─ HTTPS proxy hermes-egress (squid): PyPI/npm/GitHub only
                                     ├─ drive_to_sandbox / sandbox_to_drive <─> ~/BearDrive (jail)
                                     ├─ learning: ledger, feedback, lessons, verifier (pre_verify hook)
                                     │              └─ judge: step-3.7-flash:free, then longcat-2.5-preview:free
                                     └─ reply ──> SMTP  (strict media: attaches only from the profile caches)
 profile "default": the owner's CLI, local terminal, no email
```

## 4. Components

### 4.1 Hermes core patches (`hermes-agent-patches/`, apply after 0001)

| Patch | Problem | Fix |
|---|---|---|
| 0002 | A cron job's own `enabled_toolsets` (model-settable) could widen past `platform_toolsets.cron` | Clamp to the platform list and log what was dropped. Fail closed if the lookup fails |
| 0003 | The multiplexed gateway polls email outside profile scope, so attachments landed in the *default* profile's cache, out of reach of the team jail and sandbox | Fetch mail with `HERMES_HOME` bound to the adapter's owner profile |
| 0004 | Skills were staged before validation, so a >60-char description sat in pending until `/skills approve` rejected it, with nobody left to fix it | Run the pure validators before staging; the model gets "Not staged: …" in-turn and retries |
| 0005 | `pre_verify` fires only after host file edits, and sandbox work never counts | `agent.pre_verify_platforms` (team: email, cron) runs it every turn and passes the turn's messages |

### 4.2 Sandbox (`sandbox/`)
- **Image `hermes-sandbox:1`:**
  - python 3.12-slim, digest-pinned
  - pandas, openpyxl, numpy, xlrd, node and git preinstalled
  - runs as uid 1000
  - `/workspace` and `/root` are made 0777, because Docker gives a tmpfs the mode of the image
    directory it covers
- **Hermes runtime flags:**
  - `--read-only`, `--cap-drop ALL`, `no-new-privileges`
  - 1 CPU, 1 GB memory, pids 256
  - no host volumes and no forwarded env
  - `container_persistent: false`, so each session gets a new container
  - read-only mounts of the profile skills and caches only
- **Network:**
  - `hermes-sbx` is `--internal`: no route out and no external DNS
  - `hermes-egress` (squid) allows HTTPS CONNECT to the allowlist and denies private and loopback
    destinations
  - The BearDrive hub was moved from `*:4173` to `127.0.0.1:4173` after a probe reached it through
    the network gateway
  - A full 65,535-port scan of the host from the sandbox now finds nothing open
- **Container approval prompts are off:** with no host mounts, Hermes skips dangerous-command prompts
  inside the container, so the container itself is the security boundary.

### 4.3 BearDrive bridge (`hermes-home/plugins/beardrive`)
- **`drive_to_sandbox(path)`:** copies a jailed drive file to `/workspace/drive/<name>` over the exec
  channel.
- **`sandbox_to_drive(source, name, on_exists)`:**
  - takes a file from `/workspace` only, checked by realpath
  - never overwrites
  - also stages a copy in the profile's document cache and returns an `attach: MEDIA:<path>` line
- **Strict media delivery** (`gateway.strict: true`): the gateway attaches files only from the profile
  caches. Before this, non-strict mode would attach any non-denylisted host file named in a `MEDIA:`
  line. `MEDIA:/workspace/...` cannot be delivered, because the tmpfs is gone by send time.

### 4.4 Learning loop (`hermes-home/plugins/learning`)
- **Ledger** (`learning/ledger.jsonl`): one row per email or cron turn. Each reply ends with
  `Task T-xxxxxx`.
- **`task_feedback`:**
  - only the task's sender or the owner can grade it
  - a "bad" grade needs a reason and may add a lesson
  - silence counts as good after 3 days
  - tasks can be regraded within 14 days
- **Lessons** (`learning/lessons.jsonl`): injected into every email or cron turn. Guardrails:
  - at most 300 characters each, at most 20 active
  - no URLs, commands or shell syntax
  - no wording about permissions, senders, secrets, scheduling or instructions
  - scanned by the skills guard
  - revoke with `learning_admin.py remove <id>`
- **Skills:** staged through `skill_manage` with `write_approval: true`. Approve at the PC with
  `hermes -p team`, then `/skills approve <id>`. `pending_skill_fix.py` shortens a staged
  description.
- **Digest:** `learning-digest.timer` runs at 20:00 and emails new lessons, bad grades and Champ skill
  sync changes to the owner over plain SMTP, with no LLM.

### 4.5 Task verifier (`learning/verifier.py`, `pre_verify` hook)
1. **Rules**, from the tool log. A reply fails if:
   - it is empty
   - it attaches a `/workspace` path
   - it claims an attached or saved file but no drive save succeeded
   - the request asked for a file and none was saved
   - the last tool call failed and was not retried
2. **Judge:** a different free model reads the request, the tool log (newest first, about 9k chars)
   and the reply, and returns JSON `{verdict, problems, fix}`. It is told the log is trimmed and to
   accept any reasonable reading of the request. Deadline: 60 s of wall-clock time.
3. **Retry, then flag:**
   - A fail on attempts 0 or 1 returns `continue` with the concrete problems.
   - After that, the reply is sent with "Verifier concerns".
   - The ledger row gets verdict `bad`, source `verifier`; the sender can override it with "good".
4. **Failure handling:**
   - If the judge is slow or down, the rules alone decide, so the verifier never blocks a reply.
   - Every run is logged to `learning/verifier.jsonl`.
   - Email doesn't send interim messages (display tier minimal), so a failed draft is never sent
     before the retry.
5. **Skipped turns:** chit-chat and grading replies. Tasks with attachments are always verified,
   even when no tools ran.

### 4.6 Champ skills (`champ-skills/`)
- **Synced skills**, from `Champ-Deep/champ-skills` into team `skills/champ/`:
  - `no-ai-slop`, `executive-one-pager`, `doc-coauthoring`, `internal-comms`, `asset-title`,
    `visual-report-builder`
- **Not synced:**
  - `meeting-followup`: tied to one person's notes vault
  - `pdf-explore`: needs a hosted PDF kernel
  - `firecrawl`, `signal-scout`, `literature-review`: need open web
  - `google-workspace`: needs a connector
- **What `sync.py` does:**
  - shallow fetch
  - copy only the allowlisted skills, without repo bookkeeping
  - rewrite each description to a curated one of at most 60 characters (upstream descriptions are
    140+, and the index truncates at 57), keeping the original text in the body
  - check it with Hermes' validator
  - run the skills guard; a `dangerous` verdict keeps the last good copy
  - swap the new copy in atomically and log to `logs/champ-skills-sync.jsonl`
- **Schedule:** `champ-skills-sync.timer` runs daily at 06:30.
- **Accepted risk:** a push to that repo changes the agent's instructions the next day without
  review. The guard catches dangerous code, not bad advice. To switch to a pinned commit, change
  `branch` in `skills.yaml`.

### 4.7 Profile split
- Email moved from the default profile to `team`. Its secrets are in the team `.env`, and the default
  profile's email is disabled.
- The team profile got the Nous login with `hermes -p team auth add nous` from the shared store.
- `sync_allowlist.py` reads the team `.env`.
- Snapshots of the team config and systemd units are in `linux/`.

## 5. Security tests (`tests/security/test_beardrive_cross_tool_containment.py`)

The suite fails the deployment if:
- email or cron gain a host file, code, delegation or computer-use tool
- the team terminal stops being the locked sandbox: local backend, host volumes, forwarded env, no
  `--read-only`, the wrong network, extra caps or mounts, or a shared container
- `gateway.strict` is off, `trust_recent_files` is on, or extra media roots are set
- the `beardrive` or `learning` toolsets become ambient on other platforms
- a cron job's toolsets can widen

Mutation-tested: flipping each sandbox or strict setting makes it fail.

## 6. Measurements and findings

- **Spreadsheet task, before vs after:**
  - Before: the drive reader capped at 5,000 rows.
  - After: in the sandbox, 25,544 rows, 25,112 distinct and 432 repeated, matching the host run.
  - A 14,042-row data-quality audit ran in about 6 minutes (16 model calls) and was reproduced in
    94 s.
- **Laya (github.com/NandhaKishorM/laya), evaluated as the verifier and rejected.** Zero-shot scores
  on the real reply vs two bad replies:

  | checkpoint | good reply done/unsupported | "gave up" done | fabricated "Done, attached" done/unsupported | RAM |
  |---|---|---|---|---|
  | english | 0.84 / 0.90 | 0.09 | 0.62 / 0.18 | 2.9 GB peak |
  | multilingual | 0.80 / 0.67 | 0.35 | 0.66 / 0.03 | |
  | typed_decisions | 0.55 / 0.33 | 0.18 | 0.52 / 0.21 | |

  It misses the fabricated-success case, which the rules catch directly. It could come back later as
  a fast pre-filter once fine-tuned on the ledger's graded tasks.
- **Judge, live:**
  - fabricated and gave-up replies failed in 7–10 s with correct critiques
  - the real completed task passed in about 25 s (`step-3.7-flash`); `longcat-2.5-preview` took 163 s
    on the same input
  - with a 350-char tool-log trim, the judge falsely failed good work, hence the ~9k budget and the
    "log is trimmed" instruction

## 7. Test status

| Suite | Result |
|---|---|
| cron (full) | 1474 passed |
| email gateway (`-k email`) | 99 passed |
| skill manager, batch, approval, pre-stage | 106 passed |
| broader `-k skill` | 754 passed, 3 failed (the same 3 fail on unpatched code; unrelated) |
| pre_verify, verify hooks, plugins | 105 passed |
| containment | 7 passed |
| beardrive, learning, verifier, champ sync | 96 passed |

## 8. Open items

1. **Live-email tests** of the verifier (a "make a corrected sheet" task) and of the Champ skills
   (a no-ai-slop rewrite).
2. **Approve** the staged skill `06184f81` (`data-quality-audit`, description already shortened).
3. **Clock:** it is about 5.5 h behind with NTP off (`sudo timedatectl set-ntp true`). This causes
   Nous 401s (expired keys look valid locally) and breaks apt date checks.
4. **Fallback chain:** the local `Qwen3-1.7B` fallback is still listed and not running. Remove it if
   the owner agrees.
5. **Laya cleanup:** `~/.local/share/laya` (949 MB) and about 3 GB of Hugging Face checkpoints can be
   deleted.
6. **Email IMAP:** the adapter can die on an IMAP read timeout (seen 2026-09-29/30) and doesn't
   reconnect until the gateway restarts.
7. **Web research:** no web tool for research tasks. Adding one needs its own review (egress, prompt
   injection).
8. **Docs:** `SETUP.md` is Windows-first; `linux/README.md` covers this box.
