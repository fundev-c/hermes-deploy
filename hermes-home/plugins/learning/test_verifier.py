"""Verifier tests: evidence rules, judge parsing, retry-then-flag through the learning hooks.

The judge model is faked; no network. Run with the Hermes venv:
    <hermes>/hermes-agent/venv/bin/python -m pytest -p no:cacheprovider plugins/learning/test_verifier.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hermes-agent"))

from learning import learning as L  # noqa: E402
from learning import verifier as V  # noqa: E402

REQ = "Check this file and make a separate sheet with the needed changes. Keep the columns the same."


def turn(request, *events, reply=None):
    """Build a message list: user request, then assistant tool calls and tool results."""
    msgs = [{"role": "user", "content": "old task"}, {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": request}]
    for i, (name, result) in enumerate(events):
        msgs.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": name, "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": result})
    return msgs


SAVED = ("sandbox_to_drive", json.dumps({"saved": "x_CHECKED.xlsx", "bytes": 10, "attach": "MEDIA:/c/out_x.xlsx"}))
TERM_OK = ("terminal", json.dumps({"output": "14042 rows", "exit_code": 0}))
TERM_FAIL = ("terminal", json.dumps({"output": "FileNotFoundError", "exit_code": 1}))


@pytest.fixture(autouse=True)
def no_judge(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(L, "_ctx", None)
    L._verdicts.clear()
    monkeypatch.setattr(V, "judge", lambda *a, **k: None)


def test_fabricated_attachment_claim_fails_on_rules():
    v = V.verify(turn(REQ, TERM_FAIL), "Done! I fixed all rows and attached the corrected sheet.")
    assert v["verdict"] == "fail"
    assert any("attached" in p for p in v["rules"])
    assert any("failed and was not retried" in p for p in v["rules"])


def test_requested_file_missing_fails():
    v = V.verify(turn(REQ, TERM_OK), "I checked the file. 164 rows need changes: ...")
    assert v["verdict"] == "fail" and any("none was saved" in p for p in v["rules"])


def test_workspace_media_path_fails():
    v = V.verify(turn(REQ, TERM_OK, SAVED), "Here it is.\nMEDIA:/workspace/x_CHECKED.xlsx")
    assert any("/workspace" in p for p in v["rules"])


def test_complete_task_passes():
    v = V.verify(turn(REQ, TERM_OK, SAVED), "Done. 164 rows need changes; the sheet is saved as x_CHECKED.xlsx.\n"
                                           "MEDIA:/c/out_x.xlsx")
    assert v["verdict"] == "pass" and v["rules"] == []


def test_chit_chat_is_not_verified():
    assert V.verify(turn("good"), "Thanks!") is None
    assert V.verify(turn("thanks", ("task_feedback", "{}")), "Noted.") is None


def test_judge_fail_overrides_clean_rules(monkeypatch):
    monkeypatch.setattr(V, "judge", lambda *a, **k: {"verdict": "fail", "problems": ["Kept only 4 of 5 columns."],
                                                     "fix": "Add the missing 'entity type' column."})
    v = V.verify(turn(REQ, TERM_OK, SAVED), "Done, saved to the drive as x_CHECKED.xlsx.")
    assert v["verdict"] == "fail"
    msg = V.retry_message(v, 0)
    assert "Kept only 4 of 5 columns." in msg and "entity type" in msg and "round 1 of 2" in msg


@pytest.mark.parametrize("raw,ok", [
    ('{"verdict": "pass", "problems": [], "fix": ""}', True),
    ('Sure!\n```json\n{"verdict":"FAIL","problems":["x"],"fix":"y"}\n```', True),
    ("I think it's fine.", False),
    ('{"verdict": "maybe"}', False),
])
def test_judge_parsing(raw, ok):
    assert (V._parse_judge(raw) is not None) == ok


def test_pre_verify_retries_twice_then_lets_it_go_and_flags(monkeypatch):
    msgs = turn(REQ, TERM_OK)
    bad = "I checked it; 164 rows need changes."
    r0 = L.on_pre_verify(session_id="s", platform="email", attempt=0, final_response=bad, messages=msgs)
    r1 = L.on_pre_verify(session_id="s", platform="email", attempt=1, final_response=bad, messages=msgs)
    r2 = L.on_pre_verify(session_id="s", platform="email", attempt=2, final_response=bad, messages=msgs)
    assert r0["action"] == "continue" and "round 1 of 2" in r0["message"]
    assert r1["action"] == "continue" and "round 2 of 2" in r1["message"]
    assert r2 is None  # third failure: the reply goes out...
    out = L.on_transform_llm_output(response_text=bad, platform="email", turn_id="t", session_id="s")
    assert "Verifier concerns" in out  # ...flagged
    L.on_post_llm_call(platform="email", turn_id="t", session_id="s", user_message=REQ,
                       assistant_response=out, conversation_history=msgs)
    row = L._read("ledger.jsonl")[-1]
    assert (row["verdict"], row["verdict_source"], row["verifier"]["rounds"]) == ("bad", "verifier", 3)
    assert len(L._read("verifier.jsonl")) == 3


def test_pass_leaves_reply_and_ledger_pending():
    msgs = turn(REQ, TERM_OK, SAVED)
    good = "Done; the sheet is saved to the drive as x_CHECKED.xlsx.\nMEDIA:/c/out_x.xlsx"
    assert L.on_pre_verify(session_id="s", platform="email", attempt=0, final_response=good, messages=msgs) is None
    out = L.on_transform_llm_output(response_text=good, platform="email", turn_id="t", session_id="s")
    assert "Verifier concerns" not in out
    L.on_post_llm_call(platform="email", turn_id="t", session_id="s", user_message=REQ,
                       assistant_response=out, conversation_history=msgs)
    row = L._read("ledger.jsonl")[-1]
    assert row["verdict"] == "pending" and row["verifier"]["verdict"] == "pass"


def test_other_platforms_and_crashes_never_block(monkeypatch):
    assert L.on_pre_verify(platform="cli", final_response="x", messages=turn(REQ)) is None
    monkeypatch.setattr(V, "verify", lambda *a, **k: 1 / 0)
    assert L.on_pre_verify(platform="email", final_response="x", messages=turn(REQ)) is None


def test_sender_can_override_a_verifier_bad(monkeypatch):
    from gateway.session_context import set_session_vars
    set_session_vars(platform="email", chat_id="alice@example.com")
    msgs = turn(REQ, TERM_OK)
    for a in range(3):
        L.on_pre_verify(session_id="s", platform="email", attempt=a, final_response="checked", messages=msgs)
    out = L.on_transform_llm_output(response_text="checked", platform="email", turn_id="t", session_id="s")
    L.on_post_llm_call(platform="email", turn_id="t", session_id="s", user_message=REQ,
                       assistant_response=out, conversation_history=msgs)
    tid = L._read("ledger.jsonl")[-1]["id"]
    json.loads(L.task_feedback({"task": tid, "verdict": "good"}, task_id=""))
    assert L._read("ledger.jsonl")[-1]["verdict"] == "good"


def test_giving_up_on_an_attachment_is_verified_even_without_tools(monkeypatch):
    monkeypatch.setattr(V, "judge", lambda *a, **k: {"verdict": "fail", "problems": ["No tools used."], "fix": "Read it."})
    req = "[The user sent a document: 'a.xlsx'] Check whether the details are correct."
    v = V.verify(turn(req), "I can't open xlsx files here. Please convert it to CSV.")
    assert v is not None and v["verdict"] == "fail"
