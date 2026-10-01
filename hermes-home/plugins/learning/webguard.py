"""Web exfiltration guard for the email/cron agent.

With ``web`` enabled, an email (or a page it leads to) could steer the agent into sending private
data out through a search query or an extract URL. ``web_search`` queries go to Firecrawl cloud
and ``web_extract`` URLs are fetched from the public web, so either one can carry data out.

post_tool_call keeps the recent output of every non-web tool per session (drive files, sandbox
output and so on). pre_tool_call then blocks a web_search or web_extract whose query or URL:
  - contains 40+ consecutive characters of that output
  - contains an email address, or a token/key-like string
  - has a URL query string over 200 characters, or a path segment over 120 characters
Every web call is logged to learning/web.jsonl, allowed or not, and the daily digest reports it.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

WEB_TOOLS = ("web_search", "web_extract")
WINDOW = 40                 # this many consecutive characters copied from tool output = a leak
MAX_CORPUS = 400_000        # chars of tool output kept per session
SESSION_TTL = 4 * 3600
MAX_QUERY_STRING = 200
MAX_PATH_SEGMENT = 120

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Key-like: 32+ word chars mixing upper, lower and digits (hyphenated URL slugs and lowercase hex ids
# do not match), or a well-known key prefix.
_TOKEN = re.compile(r"(?<![\w-])(?=\w*\d)(?=\w*[a-z])(?=\w*[A-Z])\w{32,}(?![\w-])"
                    r"|\b(sk|pk|rk)[-_][A-Za-z0-9_-]{16,}|\b(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"
                    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}")
_lock = threading.Lock()
_corpus: Dict[str, List[Any]] = {}  # session_id -> [last_seen_ts, normalized text]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def remember(session_id: str, tool_name: str, result: Any) -> None:
    if tool_name in WEB_TOOLS or not session_id:
        return
    text = _norm(result if isinstance(result, str) else str(result))
    if len(text) < WINDOW:
        return
    now = time.time()
    with _lock:
        for sid in [s for s, (ts, _) in _corpus.items() if now - ts > SESSION_TTL]:
            del _corpus[sid]
        ts, old = _corpus.get(session_id, [now, ""])
        _corpus[session_id] = [now, (old + " \x00 " + text)[-MAX_CORPUS:]]


def _payloads(tool_name: str, args: Dict[str, Any]) -> List[str]:
    if tool_name == "web_search":
        return [str(args.get("query") or "")]
    urls = args.get("urls") or args.get("url") or []
    return [str(u) for u in (urls if isinstance(urls, list) else [urls])]


def problem(session_id: str, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    with _lock:
        corpus = (_corpus.get(session_id) or [0, ""])[1]
    for raw in _payloads(tool_name, args):
        text = unquote(raw)
        if _EMAIL.search(text):
            return "it contains an email address"
        if _TOKEN.search(text):
            return "it contains a key or token-like string"
        if tool_name == "web_extract":
            parts = urlsplit(raw)
            if len(parts.query) > MAX_QUERY_STRING:
                return f"its URL query string is over {MAX_QUERY_STRING} characters"
            if any(len(seg) > MAX_PATH_SEGMENT for seg in parts.path.split("/")):
                return f"its URL has a path segment over {MAX_PATH_SEGMENT} characters"
        n = _norm(text)
        if corpus and len(n) >= WINDOW:
            step = max(1, WINDOW // 4)
            for i in range(0, len(n) - WINDOW + 1, step):
                if n[i:i + WINDOW] in corpus:
                    return f"it repeats {WINDOW}+ characters of a file or tool output from this task"
    return None


def block_message(reason: str) -> str:
    return (f"Blocked by the web exfiltration guard: {reason}. Web searches and URLs leave this machine, so "
            "they must not carry drive contents, tool output, addresses or keys. Search with your own short "
            "keywords instead, or tell the sender this part needs a human.")
