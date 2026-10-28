"""send_email guard for the email/cron agent.

The model kept mailing files the wrong way (email tasks, 27 and 28 Oct): it called send_email to the person
it was already replying to, with the MEDIA: line typed into the body. That mail arrives with no file
and a raw server path, and the gateway reply then claims the file was sent. pre_tool_call blocks:
  - send_email to the sender of the email being handled (the reply itself carries the files)
  - a MEDIA: line in a send_email body (only the attachments field attaches files)
The block message tells the model the right way, so it can fix the call in the same turn.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_ADDR = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_MEDIA = re.compile(r"(?m)^\s*MEDIA:\s*\S")


def is_send_email(tool_name: str) -> bool:
    """n8n's tool arrives as mcp__n8n_tools__send_email; match on the suffix."""
    return str(tool_name or "").split("__")[-1] == "send_email"


def problem(args: Dict[str, Any], platform: str, sender: str) -> Optional[str]:
    """Why this send_email call must not run, or None."""
    sender = (sender or "").strip().lower()
    to = {a.lower() for a in _ADDR.findall(str(args.get("to") or ""))}
    if str(platform).lower() == "email" and sender and sender in to:
        return (f"{sender} sent the email you are answering, so your reply already goes to them. Do not "
                "send_email them: write the file to /workspace/out/ and it is attached to your final reply "
                "automatically (or put a MEDIA: line from sandbox_to_drive on its own line in the reply).")
    if _MEDIA.search(str(args.get("body") or "")):
        return ("a MEDIA: line in the body is only text, not an attachment. Remove it from the body and pass "
                "the MEDIA: path(s) in the attachments field.")
    return None


def block_message(reason: str) -> str:
    return f"send_email not sent: {reason}"
