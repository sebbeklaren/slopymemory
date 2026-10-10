"""The launcher's half of file anchors. It runs in the project on the user's machine, so it can fingerprint the files
an agent names; the store server never reads the disk. Only the sha256 leaves this module, never a byte of a file."""
import hashlib
from pathlib import Path

from agent_memory.spaces.file_refs import clean_path

TOOLS = ("memory_save", "memory_retrieve")
_CHUNK = 1 << 20


class Unreadable(Exception):
    """A save names a file the launcher cannot read under the project root."""


def sha256_of(root: Path, rel: str) -> str | None:
    """The file's sha256, or None when `rel` is not a regular readable file inside `root` — a symlink that leads out
    of the project counts as outside."""
    if not isinstance(rel, str) or not rel.strip() or rel.startswith("/"):
        return None
    root = root.resolve()
    try:
        p = (root / rel).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):       # ValueError: a NUL byte in the path
        return None
    if not p.is_relative_to(root) or not p.is_file():
        return None
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def needs(tool: str, arguments: dict) -> bool:
    return tool in TOOLS and isinstance(arguments.get("files"), list) and bool(arguments["files"])


def fill(tool: str, arguments: dict, root: Path) -> dict:
    """A copy of `arguments` in which every `files` entry without a sha256 key has one. An entry with a sha256 (a hash
    or null) and a malformed entry pass unchanged, for the server to judge. The path is cleaned the way the store
    cleans it, so the hash belongs to the file the store will name. On a save an unreadable file raises Unreadable
    naming it; on a retrieve it is sent as null — the file is gone."""
    out = []
    for e in arguments["files"]:
        if isinstance(e, dict) and "sha256" not in e and (path := clean_path(e.get("path"))) is not None:
            h = sha256_of(root, path)
            if h is None and tool == "memory_save":
                raise Unreadable(f"cannot anchor {path!r}: not a readable file under the project root")
            e = {**e, "path": path, "sha256": h}
        out.append(e)
    return {**arguments, "files": out}


def server_takes_files(tools) -> bool:
    """Whether the store server's tools accept `files` — a server from before anchors would drop them silently."""
    found = {t.name: t for t in tools}
    return all(n in found and "files" in ((found[n].input_schema or {}).get("properties") or {}) for n in TOOLS)
