"""Push EMAIL_ALLOWED_USERS into the n8n send_email guard.

The recipient allowlist has to exist in two places: Hermes decides who may
*send* to the agent, n8n decides who may be *sent to*. Keeping two hand-edited
copies in sync is a losing game -- and the drift fails confusingly (a user is
accepted on ingress, then send_email refuses to reply to them).

So: ~/.hermes/.env is the single source of truth, and this script rewrites the
n8n Code node from it. Run it after changing EMAIL_ALLOWED_USERS.

    python sync_allowlist.py [--check]

--check exits 1 on drift without writing anything (useful in a pre-flight).
"""
from __future__ import annotations

import json, pathlib, re, sys, urllib.error, urllib.request

HERMES_HOME = pathlib.Path(r"C:\Users\dlteam\AppData\Local\hermes")
WF_ID_FILE = HERMES_HOME / "n8n-workflows" / ".send_email_id"
BASE = "http://127.0.0.1:5678/api/v1"


def env() -> dict:
    out = {}
    for line in (HERMES_HOME / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def call(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
        headers={"X-N8N-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def main() -> int:
    check_only = "--check" in sys.argv
    e = env()
    wanted = sorted({a.strip().lower() for a in e["EMAIL_ALLOWED_USERS"].split(",") if a.strip()})
    wf_id = WF_ID_FILE.read_text().strip()
    wf = call("GET", f"/workflows/{wf_id}", e["N8N_API_KEY"])

    node = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.code")
    code = node["parameters"]["jsCode"]
    current = sorted(re.findall(r"'([^']+@[^']+)'",
                                re.search(r"const ALLOWED = \[(.*?)\];", code, re.S).group(1)))

    if current == wanted:
        print(f"in sync ({len(wanted)} recipient(s)): {', '.join(wanted)}")
        return 0
    print(f"DRIFT\n  .env : {', '.join(wanted)}\n  n8n  : {', '.join(current)}")
    if check_only:
        return 1

    block = "const ALLOWED = [\n" + "".join(f"  '{a}',\n" for a in wanted) + "];"
    node["parameters"]["jsCode"] = re.sub(r"const ALLOWED = \[.*?\];", block, code, flags=re.S)
    call("PUT", f"/workflows/{wf_id}", e["N8N_API_KEY"],
         {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"],
          "settings": wf.get("settings", {})})
    call("POST", f"/workflows/{wf_id}/publish", e["N8N_API_KEY"], {})
    print(f"synced + published: {', '.join(wanted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
