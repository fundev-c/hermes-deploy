"""Review and revoke auto-applied lessons, run at this PC (never exposed over email).

    python learning_admin.py [--home ~/.hermes/profiles/team] list
    python learning_admin.py remove L-1a2b3c
    python learning_admin.py tasks [N]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".hermes" / "profiles" / "team"))
    ap.add_argument("cmd", choices=["list", "remove", "tasks"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    d = Path(a.home).expanduser() / "learning"
    lessons_p = d / "lessons.jsonl"
    lessons = [json.loads(l) for l in lessons_p.read_text().splitlines()] if lessons_p.exists() else []
    if a.cmd == "list":
        for l in lessons:
            state = "active " if l.get("active", True) else "removed"
            print(f"{l['id']}  {state}  {time.strftime('%Y-%m-%d', time.localtime(l['ts']))}  "
                  f"{l['by']:<30} task {l['task']}\n    {l['text']}")
        print(f"{sum(l.get('active', True) for l in lessons)} active lesson(s)")
    elif a.cmd == "remove":
        hit = [l for l in lessons if l["id"] == a.arg]
        if not hit:
            print(f"no lesson {a.arg}")
            return 1
        hit[0]["active"] = False
        hit[0]["removed_ts"] = time.time()
        lessons_p.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lessons))
        print(f"removed {a.arg}")
    else:
        p = d / "ledger.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        for r in rows[-int(a.arg or 20):]:
            print(f"{r['id']}  {r.get('verdict'):<7} {r.get('sender', ''):<30} {r.get('request', '')[:70]}")
            if r.get("reason"):
                print(f"    why: {r['reason'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
