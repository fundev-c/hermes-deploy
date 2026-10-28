"""Certificate-check guard for the email/cron agent's terminal.

On 27 Oct, when the office firewall's interception certificate failed a download, the model switched
to `curl -k`. That only fetched the firewall's block page, but with checks off any interception goes
unnoticed. pre_tool_call blocks terminal commands that turn certificate verification off.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_INSECURE = re.compile(
    r"\bcurl\b[^\n|;&]*?\s(?:-[a-zA-Z]*k[a-zA-Z]*\b|--insecure\b)"   # curl -k / -skL / --insecure
    r"|\bwget\b[^\n|;&]*?--no-check-certificate\b"
    r"|\bverify\s*=\s*False\b"                                         # requests / httpx
    r"|\bPYTHONHTTPSVERIFY\s*=\s*0\b|\bNODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0\b"
    r"|\bssl\._create_unverified_context\b|\bCERT_NONE\b")


def problem(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Why this terminal command must not run, or None."""
    if tool_name != "terminal":
        return None
    m = _INSECURE.search(str(args.get("command") or ""))
    if not m:
        return None
    return (f"'{m.group(0).strip()[:60]}' turns certificate checks off. A certificate error here usually means "
            "the office firewall blocked the site: tell the sender that site is blocked instead of bypassing it.")


def block_message(reason: str) -> str:
    return f"Command not run: {reason}"
