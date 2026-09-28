"""Path jail for the shared BearDrive folder.

One folder, synced by the bdrive daemon in WSL, shared by every allow-listed
sender. There is no per-sender subtree any more, so the jail's job narrows to
one thing: no path argument a prompt-injected email can supply reaches outside
BEARDRIVE_ROOT, or into the parts of it that belong to BearDrive or to code.

Identity no longer selects a root. It is still recovered -- from the gateway's
per-turn ContextVar, or from the originating cron job -- never from a tool
argument, because it is what the access audit records and what gates writes.

Everything here fails closed. If we cannot prove a path is inside the root, it
is rejected.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Tuple

MAX_READ_BYTES = 1_000_000
MAX_LIST_ENTRIES = 500
MAX_ATTACHMENT_BYTES = 25_000_000

# Windows traps that a naive resolve() will happily walk straight through.
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_ADS_RE = re.compile(r":")           # alternate data stream  (file.txt:hidden)
_TRAILING_JUNK_RE = re.compile(r"[ .]$")   # "name " and "name." alias "name" on NTFS

# BearDrive's own names. `.bdrive/` and `.git/` are never synced and are not the
# team's content; `.bdrive-tmp-*` is a half-written file the daemon owns.
_HIDDEN_DIRS = {".bdrive", ".git"}
_HIDDEN_PREFIX = ".bdrive-tmp-"
# Readable, never writable: the shared scope control, and code agents run.
_READONLY_NAMES = {".bdriveignore"}
_READONLY_DIRS = {".claude"}
# "<name>.bdrive-conflict-<device>-<time>" -- syncer.go:1596.
CONFLICT_RE = re.compile(r"\.bdrive-conflict-.*$", re.IGNORECASE)


class BearError(Exception):
    """Raised for anything the caller is not allowed to do."""


def drive_root() -> Path:
    """The shared folder. Never created here.

    A typo'd BEARDRIVE_ROOT that silently mkdir'd an unsynced folder would be the
    worst failure available: every tool would work and nothing would sync. So the
    directory must already exist and must carry the `.bdrive/` that `bdrive init`
    writes -- proof it is a mount, not merely a folder.
    """
    raw = os.environ.get("BEARDRIVE_ROOT", "").strip()
    if not raw:
        raise BearError("the shared drive is not configured (BEARDRIVE_ROOT is unset)")
    root = Path(os.path.expanduser(raw)).resolve()
    if not root.is_dir():
        raise BearError("the shared drive folder does not exist")
    if not (root / ".bdrive").is_dir():
        raise BearError("the shared drive folder is not a BearDrive mount (no .bdrive/)")
    return root


def _fold(part: str) -> str:
    # NTFS: case-insensitive, and "name." / "name " alias "name".
    return part.rstrip(" .").casefold()


def classify(rel_parts) -> str:
    """'hidden', 'readonly' or 'ok' for a root-relative path given as components.

    Checked at any depth. Hidden paths are neither listed nor readable; read-only
    paths are listed and readable but never written.
    """
    parts = [p for p in rel_parts if p not in ("", ".")]
    folded = [_fold(p) for p in parts]
    if any(f in _HIDDEN_DIRS or f.startswith(_HIDDEN_PREFIX) for f in folded):
        return "hidden"
    if any(f in _READONLY_DIRS for f in folded):
        return "readonly"
    if folded and (folded[-1] in _READONLY_NAMES or CONFLICT_RE.search(parts[-1])):
        return "readonly"
    return "ok"


def effective_suffix(name: str) -> str:
    """Extension of the file a conflict copy preserves.

    `report.csv.bdrive-conflict-dev-...` has suffix `.bdrive-conflict-dev-...`;
    without stripping it first, the one class of file BearDrive keeps for a human
    to read would be the one class the agent cannot open.
    """
    return Path(CONFLICT_RE.sub("", name)).suffix.lower()


def _owner_from_cron_task(task_id: str) -> str:
    """Owner of a scheduled run, recovered from the job that created it.

    A cron run binds platform/chat_id to "" on purpose (cron cannot receive a
    reply, so leaving routing keys set risks delivering a subagent's output into
    an unrelated chat). That leaves Bear with no identity. The job record still
    remembers who created it in job["origin"], and delivery already trusts that
    field to decide where results go -- so it is the right source here too, and
    it is just as unreachable by the model as the ContextVar.
    """
    if not task_id.startswith("cron:"):
        return ""
    parts = task_id.split(":")
    if len(parts) < 2 or not parts[1]:
        return ""
    try:
        from cron.jobs import get_job
        job = get_job(parts[1]) or {}
        origin = job.get("origin")
        if isinstance(origin, dict) and str(origin.get("platform", "")).lower() == "email":
            return str(origin.get("chat_id", "")).strip().lower()
    except Exception:
        pass
    return ""


def resolve_actor(task_id: str = "") -> Tuple[str, str]:
    """``(actor, source)`` for this turn; ``("", "")`` when unknown.

    source is ``"session"`` (the gateway's per-turn ContextVar) or
    ``"cron-origin"`` (the job that scheduled this run). Never a tool argument.
    """
    try:
        from gateway.session_context import get_session_env
        actor = (get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip().lower()
    except Exception:
        actor = ""
    if actor:
        return actor, "session"
    if task_id:
        actor = _owner_from_cron_task(str(task_id))
        if actor:
            return actor, "cron-origin"
    return "", ""


def current_actor(task_id: str = "") -> str:
    """Who this turn belongs to, or "" -- reads do not require identity."""
    return resolve_actor(task_id)[0]


def require_actor(task_id: str = "") -> str:
    """Who this turn belongs to; raises when unknown -- writes require identity."""
    actor = current_actor(task_id)
    if not actor:
        raise BearError(
            "no session identity available; Bear access requires a message "
            "delivered through a gateway platform"
        )
    return actor


def _reject_hostile_component(part: str) -> None:
    if _ADS_RE.search(part):
        raise BearError(f"illegal path component (alternate data stream): {part!r}")
    if _TRAILING_JUNK_RE.search(part):
        raise BearError(f"illegal path component (trailing dot/space): {part!r}")
    stem = part.split(".", 1)[0].upper()
    if stem in _WIN_RESERVED:
        raise BearError(f"illegal path component (reserved device name): {part!r}")


def resolve_in_bear(rel: str) -> Tuple[Path, Path]:
    """Resolve *rel* inside the shared drive.

    Returns ``(resolved_path, root)``. Raises :class:`BearError` on anything that
    escapes, or that merely *looks* like it is trying to. Reserved BearDrive paths
    are a separate question -- see :func:`classify`.
    """
    root = drive_root()
    # Validate components BEFORE stripping: a trailing space or dot on a
    # component is an NTFS alias for the bare name, and silently normalising it
    # would hide the caller's intent rather than surface it.
    unstripped = (rel or "").replace("\\", "/")
    raw = unstripped.strip()

    if not raw or raw in (".", "./"):
        return root, root
    if raw.startswith("//") or raw.startswith("\\\\"):
        raise BearError("UNC paths are not allowed")
    if os.path.isabs(raw) or re.match(r"^[A-Za-z]:", raw):
        raise BearError("absolute paths are not allowed; use a path relative to your Bear folder")

    for part in unstripped.split("/"):
        if part.strip() in ("", "."):
            continue
        if part.strip() == "..":
            raise BearError("'..' is not allowed in Bear paths")
        _reject_hostile_component(part)

    candidate = (root / raw).resolve()

    # The containment check. resolve() has already collapsed any symlink, so a
    # link pointing outside the root lands outside it here and is caught.
    if candidate != root and root not in candidate.parents:
        raise BearError("path escapes the Bear folder")

    # Belt and braces: refuse to traverse a link even when it resolves inside,
    # so nobody can stage a link today and repoint it tomorrow.
    probe = root
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        probe = probe / part
        if probe.is_symlink():
            raise BearError(f"symlinks are not allowed in Bear paths: {part!r}")

    return candidate, root
