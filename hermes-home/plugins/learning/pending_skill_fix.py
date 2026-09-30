"""Shorten the description of a staged (pending) skill so `/skills approve` accepts it.

New skills must fit a 60-char description (the skill index truncates longer ones and loses the
routing signal). Patch 0004 now rejects over-long descriptions before staging, but writes staged
before it -- or ones you want to reword -- can be fixed here, at this PC only:

    python pending_skill_fix.py [--home ~/.hermes/profiles/team] <pending-id> "<new description>"

A .bak of the pending record is kept. Then: hermes -p team -> /skills diff <id> -> /skills approve <id>.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

LIMIT = 60
_DESC_RE = re.compile(r"^description:.*$", re.M)


def _fix(content: str, desc: str) -> str:
    if not content.lstrip("﻿").startswith("---"):
        raise SystemExit("staged SKILL.md has no frontmatter")
    head, sep, body = content.partition("\n---")
    if not _DESC_RE.search(head):
        raise SystemExit("staged SKILL.md has no description line")
    return _DESC_RE.sub("description: " + json.dumps(desc), head, count=1) + sep + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".hermes" / "profiles" / "team"))
    ap.add_argument("pending_id")
    ap.add_argument("description")
    a = ap.parse_args()
    desc = " ".join(a.description.split())
    if len(desc) > LIMIT:
        print(f"description is {len(desc)} chars; the limit is {LIMIT}")
        return 1
    p = Path(a.home).expanduser() / "pending" / "skills" / f"{a.pending_id}.json"
    if not p.is_file():
        print(f"no pending skill {a.pending_id} at {p}")
        return 1
    rec = json.loads(p.read_text())
    payload = rec["payload"]
    ops = payload.get("operations") or [payload]
    hits = [op for op in ops if op.get("action") in ("create", "edit") and op.get("content")]
    if not hits:
        print("no create/edit operation with SKILL.md content in this pending write")
        return 1
    for op in hits:
        op["content"] = _fix(op["content"], desc)
    sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
    try:
        from tools.skill_manager_tool import _validate_frontmatter
        for op in hits:
            if (err := _validate_frontmatter(op["content"], new_skill=op["action"] == "create")):
                print(f"still invalid: {err}")
                return 1
    except ImportError:
        pass
    shutil.copy2(p, p.with_suffix(".json.bak"))
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    print(f"updated {a.pending_id}: description -> {desc!r} ({len(desc)} chars). Backup: {p.name}.bak")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
