"""Task verifier for email/cron turns: evidence rules, then a second-model judge; retry, then flag.

Runs as a ``pre_verify`` hook (patch 0005 fires it on ``agent.pre_verify_platforms`` even though
sandbox work is not a host file edit). On a failed verdict it returns ``continue`` with a concrete
message and the agent keeps working; after ``MAX_RETRIES`` failed rounds the reply goes out and the
learning plugin appends "Verifier concerns" and grades the task bad (source: verifier).

Why not Laya: measured zero-shot on this box (2026-09-30), it scored a fabricated "Done, attached
the sheet" reply as mostly done and not unsupported. The rules below catch exactly that case from
the tool log, and a generative judge can explain what is missing so the retry has something to do.

The judge is a DIFFERENT free model than the worker, reads only the request, a compact tool log and
the reply, and is told the log is ground truth. It never sees secrets beyond what the reply already
contains. If it is unavailable or slow the rules alone decide: verification never blocks a reply.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
# step-3.7-flash judged today's real task in ~25s; longcat-2.5-preview took 163s for the same input.
JUDGE_MODELS = ("stepfun/step-3.7-flash:free", "meituan/longcat-2.5-preview:free")
JUDGE_TIMEOUT = 45.0   # per request, as passed to the provider
JUDGE_DEADLINE = 60.0  # wall clock for the whole judge step; provider timeouts are not always honoured
_SKIP_TOOLS = {"task_feedback", "learning_stats", "skills_list", "skill_view"}

_MEDIA_WS = re.compile(r"MEDIA:\s*/workspace/", re.I)
_CLAIMS_FILE = re.compile(r"\b(attached|attaching|saved (it|them|the \w+)? ?(to|in|into) the (shared )?drive|"
                          r"you('ll| will) find (it|the \w+) in the (shared )?drive)\b", re.I)
_WANTS_OUTPUT = re.compile(r"\b(make|create|generate|build|produce|export|prepare|send( me)?|give me)\b[^.?!]{0,60}"
                           r"\b(sheet|spreadsheet|file|csv|xlsx|excel|report|document|pdf|deck|one-pager)\b", re.I)
_ATTACHMENT = re.compile(r"\[The user sent (a|an) (document|image|file)|\battach(ed|ment)\b", re.I)
_REFUSAL = re.compile(r"\b(i can(no|')t|i am unable|i'm unable|not able to|please (convert|send|resend|paste))\b", re.I)


# ----------------------------------------------------------------------------- transcript helpers

def _text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def _this_turn(messages: List[dict]) -> Tuple[str, List[dict]]:
    """(request text, messages after it) for the current turn; synthetic verifier nudges are
    part of the turn, not a new request."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if (isinstance(m, dict) and m.get("role") == "user"
                and not any(k.endswith("_synthetic") for k in m)):
            return _text(m.get("content")), messages[i + 1:]
    return "", list(messages)


def _tool_events(turn: List[dict]) -> List[Dict[str, Any]]:
    """[{name, args, result, error}] in order, pairing tool calls with their results."""
    calls: Dict[str, Dict[str, Any]] = {}
    order: List[Dict[str, Any]] = []
    for m in turn:
        if not isinstance(m, dict):
            continue
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            ev = {"name": fn.get("name", "?"), "args": str(fn.get("arguments", ""))[:300], "result": "", "error": False}
            calls[str(tc.get("id"))] = ev
            order.append(ev)
        if m.get("role") == "tool":
            ev = calls.get(str(m.get("tool_call_id")))
            res = _text(m.get("content"))
            if ev is None:
                ev = {"name": m.get("tool_name") or m.get("name") or "?", "args": "", "result": "", "error": False}
                order.append(ev)
            ev["result"] = res[:2000]
            ev["error"] = _is_error(res)
    return order


def _is_error(res: str) -> bool:
    try:
        d = json.loads(res)
    except (ValueError, TypeError):
        return res.lstrip().lower().startswith(("error", "traceback"))
    if not isinstance(d, dict):
        return False
    if d.get("error") or d.get("success") is False:
        return True
    code = d.get("exit_code")
    return isinstance(code, int) and code != 0


def _saved_files(events: List[Dict[str, Any]]) -> List[str]:
    out = []
    for ev in events:
        if ev["name"] in ("sandbox_to_drive", "drive_save_attachment") and not ev["error"]:
            try:
                out.append(json.loads(ev["result"]).get("saved", ""))
            except (ValueError, TypeError, AttributeError):
                pass
    return [s for s in out if s]


# ----------------------------------------------------------------------------- rules

def rule_problems(request: str, reply: str, events: List[Dict[str, Any]]) -> List[str]:
    problems = []
    if not reply.strip():
        problems.append("The reply is empty.")
        return problems
    saved = _saved_files(events)
    if _MEDIA_WS.search(reply):
        problems.append("The reply attaches a /workspace/... sandbox path, which cannot be emailed. Save the file "
                        "with sandbox_to_drive and use the 'attach' line it returns.")
    if _CLAIMS_FILE.search(reply) and not saved:
        problems.append("The reply says a file was attached or saved to the drive, but no sandbox_to_drive or "
                        "drive_save_attachment call succeeded this turn.")
    if _WANTS_OUTPUT.search(request) and not saved and not _REFUSAL.search(reply):
        problems.append("The request asks for a file (sheet/report/document) but none was saved to the drive.")
    if events and events[-1]["error"] and events[-1]["name"] not in _SKIP_TOOLS:
        problems.append(f"The last tool call ({events[-1]['name']}) failed and was not retried: "
                        f"{events[-1]['result'][:200]}")
    return problems


# ----------------------------------------------------------------------------- judge

_JUDGE_SYSTEM = (
    "You are a strict QA reviewer for an assistant that does tasks sent by email. You get the REQUEST, "
    "a TOOL LOG of what the assistant actually ran (ground truth), and its final REPLY. Decide whether the "
    "reply really completes the request. Fail it if it skips part of the request, states results or "
    "files that the tool log does not show, gives up when the tools could have done the work, or ignores "
    "an explicit constraint. Do not fail it for style, and do not demand things the request did not ask "
    "for; when the request is ambiguous, accept any reasonable reading of it. The tool log is TRIMMED, so "
    "do not fail a claim just because its evidence is not visible; fail it only when the log contradicts "
    "it, or when a file or output the reply claims is missing from the log. If the tools could not do "
    "something (for example no internet), saying so plainly is fine. "
    'Answer with JSON only: {"verdict": "pass" | "fail", "problems": ["..."], "fix": "one or two '
    'sentences telling the assistant exactly what to do next"}.')


def _judge_input(request: str, reply: str, events: List[Dict[str, Any]]) -> str:
    lines, budget = [], 9000  # newest first, so the final state of the work always fits
    for e in reversed(events):
        line = f"- {e['name']}({e['args'][:150]}) -> {'ERROR ' if e['error'] else ''}{e['result'][:900]}"
        if budget - len(line) < 0:
            lines.append(f"- ... {len(events) - len(lines)} earlier tool calls omitted")
            break
        budget -= len(line)
        lines.append(line)
    log = "\n".join(reversed(lines)) or "(no tools were used)"
    return f"REQUEST:\n{request[:3000]}\n\nTOOL LOG:\n{log}\n\nREPLY:\n{reply[:4000]}"


def _parse_judge(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    verdict = str(d.get("verdict", "")).lower()
    if verdict not in ("pass", "fail"):
        return None
    probs = [str(p)[:300] for p in (d.get("problems") or []) if str(p).strip()][:5]
    return {"verdict": verdict, "problems": probs, "fix": str(d.get("fix") or "")[:500]}


def judge(request: str, reply: str, events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Second-model verdict within JUDGE_DEADLINE, or None (then the rules alone decide)."""
    import threading
    box: Dict[str, Any] = {}
    worker = threading.Thread(target=lambda: box.update(v=_judge(request, reply, events)),
                              name="task-verifier-judge", daemon=True)
    worker.start()
    worker.join(JUDGE_DEADLINE)
    if worker.is_alive():
        logger.warning("verifier: judge exceeded %.0fs; deciding on rules only", JUDGE_DEADLINE)
        return None
    return box.get("v")


def _judge(request: str, reply: str, events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    from agent.auxiliary_client import call_llm
    msgs = [{"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": _judge_input(request, reply, events)}]
    for model in JUDGE_MODELS:
        t = time.monotonic()
        try:
            resp = call_llm(task="task_verifier", provider="nous", model=model, messages=msgs,
                            temperature=0, max_tokens=600, timeout=JUDGE_TIMEOUT)
            text = resp.choices[0].message.content
        except Exception as e:
            logger.warning("verifier: judge %s failed: %s", model, str(e)[:200])
            continue
        parsed = _parse_judge(_text(text))
        if parsed:
            parsed.update(model=model, seconds=round(time.monotonic() - t, 1))
            return parsed
        logger.warning("verifier: judge %s returned no usable verdict", model)
    return None


# ----------------------------------------------------------------------------- entry point

def verify(messages: List[dict], final_response: str, *, use_judge: bool = True) -> Optional[Dict[str, Any]]:
    """Verdict dict, or None when this turn is not a task worth verifying."""
    request, turn = _this_turn(messages)
    events = _tool_events(turn)
    real = [e for e in events if e["name"] not in _SKIP_TOOLS]
    looks_like_task = (len(request) >= 300 or _ATTACHMENT.search(request) or _WANTS_OUTPUT.search(request)
                       or _CLAIMS_FILE.search(final_response or "") or _REFUSAL.search(final_response or ""))
    if not real and not looks_like_task:
        return None  # chit-chat, a grade ("good"), a short question: nothing to verify
    rules = rule_problems(request, final_response or "", events)
    j = judge(request, final_response or "", events) if use_judge else None
    passed = not rules and (j is None or j["verdict"] == "pass")
    return {"verdict": "pass" if passed else "fail", "rules": rules, "judge": j,
            "tools": [e["name"] for e in events]}


def retry_message(v: Dict[str, Any], attempt: int) -> str:
    items = list(v["rules"])
    if v.get("judge") and v["judge"]["verdict"] == "fail":
        items += v["judge"]["problems"]
        if v["judge"].get("fix"):
            items.append("Next: " + v["judge"]["fix"])
    body = "\n".join(f"- {p}" for p in items) or "- The reply does not complete the request."
    return (f"[Task verifier, round {attempt + 1} of {MAX_RETRIES}] Before this reply is sent, fix these:\n{body}\n"
            "Do the missing work with your tools, then write the complete reply again. If something truly "
            "cannot be done here, say so plainly in the reply instead of claiming it.")


def concerns_text(v: Dict[str, Any]) -> str:
    items = list(v["rules"]) + (v["judge"]["problems"] if v.get("judge") and v["judge"]["verdict"] == "fail" else [])
    return "Verifier concerns (automatic check, may be wrong):\n" + "\n".join(f"- {p}" for p in items[:5])
