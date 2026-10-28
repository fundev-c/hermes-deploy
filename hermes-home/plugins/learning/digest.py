"""Daily digest of email activity, new lessons and bad grades, mailed to the owner (EMAIL_HOME_ADDRESS).

Plain SMTP with the team profile's own mailbox; no LLM involved. Sends nothing when there is
nothing new. Run by the learning-digest.timer systemd user unit:

    python digest.py [--home ~/.hermes/profiles/team] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path


def _env(home: Path) -> dict:
    out = {}
    for line in (home / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _rows(p: Path) -> list:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _email_section(home: Path, since: float) -> tuple:
    """(lines, events, emails received) for mail in/out (logs/email.jsonl, patch 0013), checker and guards."""
    mail = [r for r in _rows(home / "logs" / "email.jsonl") if r.get("ts", 0) > since]
    checks = [r for r in _rows(home / "learning" / "verifier.jsonl") if r.get("ts", 0) > since]
    guards = [r for r in _rows(home / "learning" / "guard.jsonl") if r.get("ts", 0) > since]
    inbound = [r for r in mail if r["kind"] == "in"]
    out = [r for r in mail if r["kind"] == "out"]
    files = [f for r in out for f in r.get("files", [])]
    outages = [r for r in mail if r["kind"] == "outage"]
    lines = [f"Received: {len(inbound)} email(s) from {len({r['sender'] for r in inbound})} sender(s)",
             f"Sent: {len(out)} reply/replies with {len(files)} attachment(s); "
             f"{sum(r['kind'] == 'ack' for r in mail)} still-working note(s)",
             f"Answer checker: {sum(r.get('verdict') == 'fail' for r in checks)} reply/replies sent back for fixes, "
             f"{sum(bool(r.get('judge_skipped')) for r in checks)} checked by rules only (judge too slow)",
             f"Guards: {sum(r.get('guard') == 'mail' for r in guards)} send_email call(s) and "
             f"{sum(r.get('guard') == 'tls' for r in guards)} certificate bypass(es) blocked",
             f"Inbox outages: {len(outages)}" + (f" ({sum(r['minutes'] for r in outages):.0f} min in total)" if outages else "")]
    lines += [f"  {r.get('sender')}: {str(r.get('subject', ''))[:90]}" for r in inbound[:20]]
    return lines, len(mail) + len(guards), len(inbound)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".hermes" / "profiles" / "team"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    home = Path(a.home).expanduser()
    d = home / "learning"
    d.mkdir(parents=True, exist_ok=True)
    mark = d / ".digest_ts"
    since = float(mark.read_text()) if mark.exists() else 0.0
    now = time.time()
    lessons = [l for l in _rows(d / "lessons.jsonl") if l.get("ts", 0) > since]
    bad = [r for r in _rows(d / "ledger.jsonl")
           if r.get("verdict") == "bad" and r.get("graded_ts", 0) > since]
    sync = [r for r in _rows(home / "logs" / "champ-skills-sync.jsonl") if r.get("ts", 0) > since]
    web = [r for r in _rows(d / "web.jsonl") if r.get("ts", 0) > since]
    email_lines, email_events, received = _email_section(home, since)
    if not lessons and not bad and not sync and not web and not email_events:
        print("nothing new")
        mark.write_text(str(now))
        return 0
    body = ["Email:"] + email_lines + ["", "New lessons (already applied to later email/cron tasks). Revoke with:",
            "  python learning_admin.py remove <id>", ""]
    body += [f"{l['id']} by {l['by']} (task {l['task']}):\n    {l['text']}" for l in lessons] or ["(none)"]
    body += ["", "Tasks graded bad:"]
    body += [f"{r['id']} by {r.get('graded_by')}: {r.get('request', '')[:120]}\n    why: {r.get('reason', '')[:300]}"
             for r in bad] or ["(none)"]
    body += ["", "Champ skills sync (github.com/Champ-Deep/champ-skills):"]
    body += [f"{r['skill']}: {r['result']} at {str(r.get('commit', ''))[:12]}"
             + (f" ({r.get('verdict')})" if r.get("verdict") else "")
             + (f" findings: {', '.join(r.get('findings', [])[:3])}" if r.get("findings") else "")
             for r in sync] or ["(no changes)"]
    blocked = [r for r in web if r.get("blocked")]
    body += ["", f"Web use: {sum(r['tool'] == 'web_search' for r in web)} search(es) (Firecrawl cloud credits), "
             f"{sum(r['tool'] == 'web_extract' for r in web)} extract(s), {len(blocked)} blocked by the exfiltration guard"]
    body += [f"  BLOCKED {r['tool']}: {(r.get('query') or ', '.join(r.get('urls', [])))[:160]}\n    why: {r.get('reason')}"
             for r in blocked[:10]]
    e = _env(home)
    msg = EmailMessage()
    msg["From"], msg["To"] = e["EMAIL_ADDRESS"], e.get("EMAIL_HOME_ADDRESS") or e["EMAIL_ADDRESS"]
    msg["Subject"] = (f"Hermes daily digest: {received} email(s) in, "
                      f"{len(lessons)} lesson(s), {len(bad)} bad grade(s), {len(blocked)} blocked web call(s)")
    msg.set_content("\n".join(body))
    if a.dry_run:
        print(msg)
        return 0
    port = int(e.get("EMAIL_SMTP_PORT") or 587)
    if port == 465:  # implicit TLS (Gmail's default here)
        conn = smtplib.SMTP_SSL(e["EMAIL_SMTP_HOST"], port, timeout=30)
    else:
        conn = smtplib.SMTP(e["EMAIL_SMTP_HOST"], port, timeout=30)
        conn.starttls()
    with conn as s:
        s.login(e["EMAIL_ADDRESS"], e["EMAIL_PASSWORD"])
        s.send_message(msg)
    mark.write_text(str(now))
    print(f"sent to {msg['To']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
