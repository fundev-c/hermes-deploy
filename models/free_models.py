"""Keep the Hermes model chain on free models that work *today*.

Free models on Nous come and go without notice (2026-10-27: the primary and two fallbacks vanished
or stopped being free and the agent ran on whatever was left). This checks, every few hours:

1. Nous's catalog (the provider Hermes actually calls): models priced 0/0 with text output.
2. OpenRouter's public catalog: drops models OpenRouter says can't take tools, and logs which
   OpenRouter :free models Nous doesn't carry (for information only).
3. A two-step tool-calling probe per candidate, the shape of a real email task: save a file,
   then pass the returned MEDIA: paths in send_email's `attachments` (not in the body).

Then it rewrites model.default + fallback_providers in each profile's config.yaml. The gateway reads
config.yaml on every message, so no restart is needed. Rules:
- Order is sticky: models already in the chain keep their place while they pass; new ones are added
  after them, fastest first. A primary that still passes is never swapped out for a newer model.
  A profile whose primary died takes the team profile's ranking.
- Only free Nous models are managed. A paid primary, or a fallback that isn't a free Nous model,
  is left alone (paid fallbacks stay at the end of the chain).
- If nothing passes (network down, firewall interception), nothing is written.

It also writes ~/.hermes/free_models.json (the passing models with latencies). The learning plugin's
verifier reads its judge models from it.

    venv/bin/python free_models.py [--dry-run] [--profiles team,default,bench]
"""
from __future__ import annotations

import argparse, concurrent.futures as cf, datetime, json, os, pathlib, re, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path.home() / ".hermes"
PROFILE_HOMES = {"default": ROOT, "team": ROOT / "profiles" / "team", "bench": ROOT / "profiles" / "bench"}
STATE_FILE = ROOT / "free_models.json"
LOG_FILE = ROOT / "logs" / "free-models.jsonl"
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
CHAIN_LEN = 5          # primary + 4 fallbacks
MIN_CONTEXT = 64_000   # a tool loop over a long email thread needs room
PROBE_TIMEOUT = 90

os.environ.setdefault("HERMES_HOME", str(PROFILE_HOMES["team"]))  # credentials for the probe

TOOLS = [
    {"type": "function", "function": {
        "name": "sandbox_to_drive", "description": "Copy a sandbox file to the shared drive; returns a MEDIA: path for attaching.",
        "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "name": {"type": "string"}},
                       "required": ["source"]}}},
    {"type": "function", "function": {
        "name": "send_email", "description": "Send an email, optionally with file attachments. Pass the MEDIA: paths "
        "returned by sandbox_to_drive in `attachments` (one per line). MEDIA: lines typed into `body` are NOT attachments.",
        "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                        "body": {"type": "string"}, "attachments": {"type": "string"}},
                       "required": ["to", "subject", "body"]}}},
]
DOC = "/home/hemang/.hermes/profiles/team/cache/documents/out_ab12cd34_"
STEP1 = [{"role": "system", "content": "You are an email agent for a sales team. Files in /workspace must be saved "
                                       "with sandbox_to_drive before they can be attached."},
         {"role": "user", "content": "Please send /workspace/report.xlsx and /workspace/run.py to deep@lakeb2b.com "
                                     "with a one-line note."}]
STEP2 = STEP1 + [
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "sandbox_to_drive", "arguments": '{"source": "/workspace/report.xlsx"}'}},
        {"id": "c2", "type": "function", "function": {"name": "sandbox_to_drive", "arguments": '{"source": "/workspace/run.py"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"saved": "report.xlsx", "attach": f"MEDIA:{DOC}report.xlsx"})},
    {"role": "tool", "tool_call_id": "c2", "content": json.dumps({"saved": "run.py", "attach": f"MEDIA:{DOC}run.py"})}]


def http_json(url: str, key: str | None = None, body: dict | None = None, timeout: float = 30) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "hermes-free-models/1"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def is_free(m: dict) -> bool:
    p = m.get("pricing") or {}
    try:
        return float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


def candidates(nous: list[dict], openrouter: dict[str, dict]) -> tuple[list[dict], list[str]]:
    out, skipped = [], []
    for m in nous:
        arch = m.get("architecture") or {}
        if not is_free(m) or "text" not in (arch.get("output_modalities") or ["text"]) or "embed" in m["id"]:
            continue
        if (m.get("context_length") or 0) < MIN_CONTEXT:
            skipped.append(f"{m['id']} (context {m.get('context_length')})")
            continue
        orm = openrouter.get(m["id"]) or openrouter.get(m["id"].removesuffix(":free"))
        if orm and "tools" not in (orm.get("supported_parameters") or ["tools"]):
            skipped.append(f"{m['id']} (no tool support per OpenRouter)")
            continue
        out.append(m)
    return out, skipped


def probe(model: str, url: str, key: str) -> dict:
    """Both steps must produce the right tool call. One retry on 429 (upstream capacity)."""
    t0 = time.monotonic()

    def call(messages):
        for attempt in range(2):
            try:
                d = http_json(url, key, {"model": model, "messages": messages, "tools": TOOLS, "max_tokens": 2000},
                              timeout=PROBE_TIMEOUT)
                return (d["choices"][0]["message"].get("tool_calls") or [])
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:
                    time.sleep(8)
                    continue
                raise RuntimeError(f"HTTP {e.code}: {e.read()[:160].decode(errors='replace')}")
        raise RuntimeError("HTTP 429 twice (upstream at capacity)")

    try:
        tc = call(STEP1)
        if not tc or tc[0]["function"]["name"] != "sandbox_to_drive":
            return {"model": model, "ok": False, "why": "step 1: no sandbox_to_drive call"}
        tc = call(STEP2)
        if not tc or tc[0]["function"]["name"] != "send_email":
            return {"model": model, "ok": False, "why": "step 2: no send_email call"}
        args = json.loads(tc[0]["function"]["arguments"] or "{}")
        if str(args.get("attachments", "")).count(DOC) != 2 or "MEDIA:" in str(args.get("body", "")):
            return {"model": model, "ok": False, "why": "step 2: attachments not passed correctly"}
        return {"model": model, "ok": True, "seconds": round(time.monotonic() - t0, 1)}
    except Exception as e:  # noqa: BLE001 -- any failure just means "not usable right now"
        return {"model": model, "ok": False, "why": str(e)[:200]}


def ranked(current: list[str], passing: dict[str, dict]) -> list[str]:
    keep = [m for m in current if m in passing]
    new = sorted((m for m in passing if m not in keep), key=lambda m: passing[m]["seconds"])
    return keep + new


# ── config rewrite: text-level, so the rest of the file keeps its exact formatting ────────────────
MODEL_BLOCK = re.compile(r"(?m)^model:\n((?:[ \t]+\S.*\n)*)")
FALLBACK_BLOCK = re.compile(r"(?m)^fallback_providers:\n((?:(?:[ \t]*- |[ \t]+)\S.*\n)*)")


def plan_profile(text: str, cfg: dict, chain: list[str], catalog: dict[str, dict]) -> tuple[str, list[str]] | None:
    """New config text for one profile, or None when nothing should change."""
    import yaml
    model = cfg.get("model") or {}
    if not isinstance(model, dict) or model.get("provider") != "nous" or not FALLBACK_BLOCK.search(text):
        return None
    primary = model.get("default", "")
    managed = lambda m: m not in catalog or is_free(catalog[m])  # missing = gone; free = ours to manage
    fallbacks = cfg.get("fallback_providers") or []
    kept = [f for f in fallbacks if not (f.get("provider") == "nous" and managed(f.get("model", "")))]
    # Sticky while the primary still works; once it's gone, take the shared ranking wholesale rather than
    # promoting whichever leftover fallback happened to survive.
    order = (ranked([primary] + [f.get("model") for f in fallbacks], {m: {"seconds": i} for i, m in enumerate(chain)})
             if primary in chain or not managed(primary) else list(chain))
    if managed(primary):
        new_primary, free_fb = order[0], order[1:CHAIN_LEN]
    else:  # paid primary chosen by a human: keep it, still refresh the free fallbacks
        new_primary, free_fb = primary, [m for m in order if m != primary][:CHAIN_LEN - 1]
    new_fallbacks = [{"model": m, "provider": "nous"} for m in free_fb] + kept
    if new_primary == primary and new_fallbacks == fallbacks:
        return None

    mb = MODEL_BLOCK.search(text)
    body = mb.group(1)
    indent = re.match(r"[ \t]+", body).group(0)
    body = re.sub(r"(?m)^([ \t]+)default: .*$", lambda m: f"{m.group(1)}default: {new_primary}", body)
    ctx = (catalog.get(new_primary) or {}).get("context_length")
    if ctx:
        if re.search(r"(?m)^[ \t]+context_length: ", body):
            body = re.sub(r"(?m)^([ \t]+)context_length: .*$", lambda m: f"{m.group(1)}context_length: {ctx}", body)
        else:
            body = f"{indent}context_length: {ctx}\n" + body
    text2 = text[:mb.start(1)] + body + text[mb.end(1):]

    fb = FALLBACK_BLOCK.search(text2)
    item = re.match(r"[ \t]*", fb.group(1)).group(0)  # "" for "- model:", "  " for "  - model:"
    lines = "".join(f"{item}- model: {f['model']}\n{item}  provider: {f['provider']}\n"
                    + "".join(f"{item}  {k}: {json.dumps(v) if not isinstance(v, (int, float)) else v}\n"
                              for k, v in f.items() if k not in ("model", "provider"))
                    for f in new_fallbacks)
    text2 = text2[:fb.start(1)] + lines + text2[fb.end(1):]

    # Safety net: the rewrite may only have touched these keys.
    after = yaml.safe_load(text2)
    before = dict(cfg)
    for d in (after, before):
        d.pop("fallback_providers", None)
        d["model"] = {k: v for k, v in (d.get("model") or {}).items() if k not in ("default", "context_length")}
    if after != before:
        raise RuntimeError("config rewrite changed more than the model chain; refusing to write")
    changes = [f"primary {primary} -> {new_primary}"] if new_primary != primary else []
    changes.append("fallbacks " + ", ".join(f["model"] for f in new_fallbacks))
    return text2, changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="probe and report, write nothing")
    ap.add_argument("--profiles", default="team,default,bench")
    args = ap.parse_args()

    import yaml
    from hermes_cli.auth_nous import resolve_nous_runtime_credentials
    cr = resolve_nous_runtime_credentials()
    key, base = cr["api_key"], cr["base_url"].rstrip("/")
    nous = http_json(f"{base}/models", key)["data"]
    catalog = {m["id"]: m for m in nous}
    # A key that looks unexpired locally can already be dead (this host's clock has run hours slow),
    # and /models answers without auth, so test the key on a real completion and refresh on 401.
    try:
        free_id = next(m["id"] for m in nous if is_free(m) and "embed" not in m["id"])
        http_json(f"{base}/chat/completions", key, {"model": free_id, "max_tokens": 1,
                                                    "messages": [{"role": "user", "content": "hi"}]})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            cr = resolve_nous_runtime_credentials(force_refresh=True, stale_access_token=cr.get("access_token"))
            key = cr["api_key"]
    except Exception:  # noqa: BLE001 -- that model may just be busy; the probes will tell
        pass
    try:
        openrouter = {m["id"]: m for m in http_json(OPENROUTER_MODELS)["data"]}
    except Exception as e:  # noqa: BLE001 -- OpenRouter is advisory; Nous + the probe decide
        print(f"OpenRouter catalog unavailable ({e}); probing without it", file=sys.stderr)
        openrouter = {}

    cands, skipped = candidates(nous, openrouter)
    or_only = sorted(m for m, v in openrouter.items() if m.endswith(":free") and is_free(v) and m not in catalog)
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda m: probe(m["id"], f"{base}/chat/completions", key), cands))
    passing = {r["model"]: r for r in results if r["ok"]}
    for r in sorted(results, key=lambda r: (not r["ok"], r.get("seconds", 0))):
        print(("PASS %5.1fs " % r["seconds"] if r["ok"] else "FAIL        ") + r["model"] + ("" if r["ok"] else f"  ({r['why']})"))
    for s in skipped:
        print(f"SKIP        {s}")
    print(f"OpenRouter :free models Nous doesn't carry: {len(or_only)}")

    record = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "passing": {m: r["seconds"] for m, r in passing.items()},
              "failing": {r["model"]: r["why"] for r in results if not r["ok"]}, "skipped": skipped,
              "openrouter_free_not_on_nous": or_only, "changes": {}}
    if not passing:
        print("No free model passed; leaving every config as it is.", file=sys.stderr)
    else:
        team_cfg = yaml.safe_load((PROFILE_HOMES["team"] / "config.yaml").read_text())
        current = [team_cfg["model"]["default"]] + [f["model"] for f in team_cfg.get("fallback_providers") or []]
        chain = ranked(current, passing)
        for name in [p.strip() for p in args.profiles.split(",") if p.strip()]:
            path = PROFILE_HOMES[name] / "config.yaml"
            text = path.read_text()
            plan = plan_profile(text, yaml.safe_load(text), chain, catalog)
            if not plan:
                print(f"{name}: up to date")
                continue
            new_text, changes = plan
            record["changes"][name] = changes
            print(f"{name}: " + "; ".join(changes) + (" (dry run)" if args.dry_run else ""))
            if not args.dry_run:
                path.with_name("config.yaml.bak-free-models").write_text(text)
                tmp = path.with_name(".config.yaml.free-models.tmp")
                tmp.write_text(new_text)
                os.replace(tmp, path)
        if not args.dry_run:
            STATE_FILE.write_text(json.dumps({"updated_at": record["at"], "chain": chain,
                                              "by_speed": sorted(passing, key=lambda m: passing[m]["seconds"])}, indent=2))
    if not args.dry_run:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(record) + "\n")
    return 0 if passing else 1


if __name__ == "__main__":
    sys.exit(main())
