"""Daily digest email section: counts from logs/email.jsonl, verifier.jsonl and guard.jsonl since the last digest.

Run: <hermes>/hermes-agent/venv/bin/python -m pytest -p no:cacheprovider plugins/learning/test_digest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import digest  # noqa: E402


def write(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_email_section_counts_only_new_activity(tmp_path):
    write(tmp_path / "logs" / "email.jsonl", [
        {"ts": 50, "kind": "in", "sender": "old@corp.com", "subject": "before the last digest"},
        {"ts": 150, "kind": "in", "sender": "carol@corp.com", "subject": "Upper case"},
        {"ts": 160, "kind": "in", "sender": "carol@corp.com", "subject": "RE: Upper case"},
        {"ts": 170, "kind": "out", "to": "carol@corp.com", "files": [{"name": "a.xlsx", "bytes": 1}]},
        {"ts": 180, "kind": "ack", "to": "carol@corp.com"},
        {"ts": 190, "kind": "outage", "minutes": 27.5}])
    write(tmp_path / "learning" / "verifier.jsonl", [{"ts": 155, "verdict": "fail"}, {"ts": 165, "judge_skipped": True}])
    write(tmp_path / "learning" / "guard.jsonl", [{"ts": 175, "guard": "mail"}, {"ts": 176, "guard": "tls"}])
    lines, events, received = digest._email_section(tmp_path, since=100)
    text = "\n".join(lines)
    assert received == 2 and events == 7
    assert "from 1 sender(s)" in text and "1 reply/replies with 1 attachment(s); 1 still-working" in text
    assert "1 reply/replies sent back" in text and "1 checked by rules only" in text
    assert "1 send_email call(s) and 1 certificate bypass(es)" in text and "28 min in total" in text
    assert "old@corp.com" not in text


def test_quiet_day_has_no_events(tmp_path):
    lines, events, received = digest._email_section(tmp_path, since=0)
    assert events == 0 and received == 0
