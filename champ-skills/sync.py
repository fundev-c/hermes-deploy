"""Daily sync of selected Champ-Deep/champ-skills into the team profile (skills/champ/<name>).

For each skill in skills.yaml:
  1. fetch the repo (shallow) into ~/.hermes/champ-skills-src
  2. stage a copy without repo bookkeeping (.catalog_stamp, .sync-org, evals, archives)
  3. rewrite the frontmatter description to the curated <= 60-char one (upstream text is kept at
     the top of the body) and check it with Hermes' own skill validator
  4. scan it with Hermes' skills guard; a "dangerous" verdict keeps the last good copy
  5. swap it in atomically; record commit, hash and verdict in skills/champ/.sync-state.json and
     one line per change in <profile>/logs/champ-skills-sync.jsonl (the learning digest mails these)

The agent sees skills/champ/* like any profile skill, and the sandbox mounts them read-only.

    python sync.py [--home ~/.hermes/profiles/team] [--src ~/.hermes/champ-skills-src] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
EXCLUDE = shutil.ignore_patterns(".catalog_stamp", ".sync-org", ".verify", "evals", "*.zip", "__pycache__", ".git*")
LIMIT = 60


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=300).stdout.strip()


def fetch(repo: str, branch: str, src: Path) -> str:
    if (src / ".git").is_dir():
        git("fetch", "--depth", "1", "origin", branch, cwd=src)
        git("reset", "--hard", f"origin/{branch}", cwd=src)
    else:
        src.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--depth", "1", "--branch", branch, repo, str(src))
    return git("rev-parse", "HEAD", cwd=src)


def tree_hash(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(d)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def rewrite_frontmatter(skill_md: Path, description: str) -> None:
    text = skill_md.read_text(encoding="utf-8").lstrip("﻿")
    if not text.startswith("---"):
        raise ValueError("SKILL.md has no frontmatter")
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    upstream = " ".join(str(meta.get("description", "")).split())
    meta["description"] = description
    note = f"\n> Upstream description: {upstream}\n" if upstream else "\n"
    skill_md.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000)
                        + "---\n" + note + body.lstrip("\n"), encoding="utf-8")


def validate(skill_md: Path) -> str | None:
    try:
        from tools.skill_manager_tool import _validate_frontmatter
    except ImportError:
        return None
    return _validate_frontmatter(skill_md.read_text(encoding="utf-8"), new_skill=True)


def scan(d: Path) -> tuple[str, list]:
    try:
        from tools.skills_guard import scan_skill
    except ImportError:
        return "unscanned", []
    r = scan_skill(d, source="community")
    findings = [f"{f.severity}:{f.pattern_id}:{f.file}:{f.line}" for f in getattr(r, "findings", [])][:20]
    return str(getattr(r, "verdict", r)), findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".hermes" / "profiles" / "team"))
    ap.add_argument("--src", default=str(Path.home() / ".hermes" / "champ-skills-src"))
    ap.add_argument("--config", default=str(HERE / "skills.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="use --src as is (tests)")
    a = ap.parse_args()
    sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
    cfg = yaml.safe_load(Path(a.config).read_text())
    home, src = Path(a.home).expanduser(), Path(a.src).expanduser()
    dest_root = home / "skills" / "champ"
    state_p = dest_root / ".sync-state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {"skills": {}}
    commit = git("rev-parse", "HEAD", cwd=src) if a.no_fetch else fetch(cfg["repo"], cfg.get("branch", "main"), src)
    log = []
    for rel, desc in cfg["skills"].items():
        name = Path(rel).name
        rec = {"ts": time.time(), "skill": name, "commit": commit}
        if len(desc) > LIMIT:
            log.append({**rec, "result": "error", "error": f"curated description is {len(desc)} chars"})
            continue
        upstream = src / rel
        if not (upstream / "SKILL.md").is_file():
            log.append({**rec, "result": "missing-upstream"})
            continue
        with tempfile.TemporaryDirectory(dir=home) as tmp:
            staged = Path(tmp) / name
            shutil.copytree(upstream, staged, ignore=EXCLUDE, symlinks=False)
            try:
                rewrite_frontmatter(staged / "SKILL.md", desc)
            except Exception as e:
                log.append({**rec, "result": "error", "error": f"frontmatter: {e}"[:300]})
                continue
            if (err := validate(staged / "SKILL.md")):
                log.append({**rec, "result": "invalid", "error": err[:300]})
                continue
            digest = tree_hash(staged)
            if state["skills"].get(name, {}).get("hash") == digest and (dest_root / name).is_dir():
                continue  # unchanged
            verdict, findings = scan(staged)
            if verdict == "dangerous":
                log.append({**rec, "result": "blocked", "verdict": verdict, "findings": findings,
                            "kept": state["skills"].get(name, {}).get("commit")})
                continue
            if not a.dry_run:
                dest_root.mkdir(parents=True, exist_ok=True)
                target = dest_root / name
                old = dest_root / f".{name}.old"
                if old.exists():
                    shutil.rmtree(old)
                if target.exists():
                    target.rename(old)
                shutil.move(str(staged), str(target))
                shutil.rmtree(old, ignore_errors=True)
                state["skills"][name] = {"hash": digest, "commit": commit, "verdict": verdict, "path": rel,
                                         "updated": time.time()}
            log.append({**rec, "result": "updated" if not a.dry_run else "would-update", "verdict": verdict,
                        "findings": findings})
    for gone in set(state["skills"]) - {Path(r).name for r in cfg["skills"]}:
        if not a.dry_run:
            shutil.rmtree(dest_root / gone, ignore_errors=True)
            state["skills"].pop(gone)
        log.append({"ts": time.time(), "skill": gone, "commit": commit, "result": "removed"})
    if not a.dry_run:
        state["commit"], state["synced"] = commit, time.time()
        dest_root.mkdir(parents=True, exist_ok=True)
        state_p.write_text(json.dumps(state, indent=2))
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "champ-skills-sync.jsonl", "a") as f:
            for r in log:
                f.write(json.dumps(r) + "\n")
    for r in log:
        print(f"{r['skill']:<24} {r['result']:<14} {r.get('verdict', '')} {r.get('error', '')}")
    print(f"commit {commit[:12]}; {len(log)} change(s)")
    return 1 if any(r["result"] in ("error", "invalid", "blocked") for r in log) else 0


if __name__ == "__main__":
    raise SystemExit(main())
