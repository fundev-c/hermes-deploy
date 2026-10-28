"""send_email guard: no send_email to the sender being answered, no MEDIA: lines in the body.

Run: <hermes>/hermes-agent/venv/bin/python -m pytest -p no:cacheprovider plugins/learning/test_mailguard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hermes-agent"))

import gateway.session_context as SC  # noqa: E402
from learning import learning as L  # noqa: E402

TOOL = "mcp__n8n_tools__send_email"
FILE = "MEDIA:/home/x/.hermes/profiles/team/cache/documents/out_e02bd77e_Keyword_normalized.xlsx"


@pytest.fixture(autouse=True)
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    env = {"HERMES_SESSION_PLATFORM": "email", "HERMES_SESSION_CHAT_ID": "alice@corp.com"}
    monkeypatch.setattr(SC, "get_session_env", lambda name, default="": env.get(name, default))
    return env


def pre(**args):
    return L.on_pre_tool_call(tool_name=TOOL, args=args, session_id="s1")


def test_blocks_send_email_to_the_sender_being_answered():
    r = pre(to="Alice.S <Alice@corp.com>", subject="RE: Upper case", body="Attached.", attachments=FILE)
    assert r["action"] == "block" and "final reply" in r["message"]


def test_blocks_media_line_typed_into_the_body():
    r = pre(to="bob@corp.com", subject="Keywords", body=f"Hi Bob,\n\nAttachment:\n{FILE}\n")
    assert r["action"] == "block" and "attachments field" in r["message"]


def test_allows_a_proper_send_to_someone_else():
    assert pre(to="bob@corp.com", subject="Keywords", body="Hi Bob, file attached.", attachments=FILE) is None
    assert pre(to="bob@corp.com", subject="Note", body="The word MEDIA: appears mid-line here.") is None


def test_cron_runs_may_mail_the_job_owner(session):
    session["HERMES_SESSION_PLATFORM"] = "cron"
    assert pre(to="alice@corp.com", subject="Daily digest", body="Today's numbers.", attachments=FILE) is None


def test_other_tools_untouched():
    assert L.on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="s1") is None
