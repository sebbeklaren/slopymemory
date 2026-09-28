"""Held saves: a save is written to disk before anything is claimed, so a database that is down, hanging or failing
never loses one. One file per record in `held/` beside the pre-warm buffer, named so the directory lists in the
order the saves were made; the server drains them through the same save path once the database answers. A record
that fails for a reason other than the connection moves to `held-failed/` with its error and is never deleted by the
software. `db-status.json` carries the database state for `slopymem doctor`, which reads files only."""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_memory.config import settings

_URL = re.compile(r"postgres(?:ql)?://\S+")
_PASSWORD = re.compile(r"password\s*=\s*(?:'(?:[^'\\]|\\.)*'|\S+)", re.IGNORECASE)


def state_dir() -> Path:
    return Path(settings.m3_buffer_path).parent


def _held() -> Path:
    return state_dir() / "held"


def _failed() -> Path:
    return state_dir() / "held-failed"


def record(memory_id, text, session_key, scope, now, thread, facets, save_concepts, supersedes) -> dict:
    return {"memory_id": memory_id, "text": text, "session_key": session_key, "scope": scope,
            "now": now.isoformat(), "thread": thread, "facets": facets, "save_concepts": save_concepts,
            "supersedes": supersedes}


def _write_durably(path: Path, data: dict) -> None:
    """Write to a temporary name, fsync, rename, fsync the directory: after this returns the record survives a
    power loss, and a crash part-way leaves only a `.tmp` that no reader treats as a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def hold(rec: dict) -> Path:
    """The file name orders the drain, so it must grow even if the wall clock steps back: never below the newest
    held record's number."""
    last = held_paths()
    ns = time.time_ns()
    if last:
        try:
            ns = max(ns, int(last[-1].name.split("-", 1)[0]) + 1)
        except ValueError:
            pass
    path = _held() / f"{ns:020d}-{rec['memory_id']}.json"
    _write_durably(path, rec)
    return path


def held_paths() -> list[Path]:
    d = _held()
    return sorted(d.glob("*.json")) if d.exists() else []


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def held_ids() -> set[str]:
    return {p.stem.split("-", 1)[1] for p in held_paths()}


def release(path: Path) -> None:
    path.unlink(missing_ok=True)


def fail(path: Path, error: str) -> None:
    try:
        rec = load(path)
    except (OSError, ValueError):
        rec = {"unreadable": path.read_text(encoding="utf-8", errors="replace")}
    _write_durably(_failed() / path.name, {"record": rec, "error": error,
                                           "failed_at": datetime.now(timezone.utc).isoformat()})
    path.unlink(missing_ok=True)


def counts() -> tuple[int, int]:
    f = _failed()
    return len(held_paths()), (len(list(f.glob("*.json"))) if f.exists() else 0)


def failed_errors() -> list[str]:
    f = _failed()
    return [json.loads(p.read_text(encoding="utf-8")).get("error", "") for p in sorted(f.glob("*.json"))] if f.exists() else []


def write_status(state: dict) -> None:
    _write_durably(state_dir() / "db-status.json", state)


def read_status(d: Path) -> dict | None:
    p = d / "db-status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clean_error(exc: BaseException) -> str:
    """The exception's class and the first line of its message, with any database URL and any password removed —
    a reply and a log line may be pasted into an issue."""
    first = (str(exc).splitlines() or [""])[0]
    first = _URL.sub("<database url>", first)
    first = _PASSWORD.sub("password=<hidden>", first)
    return f"{type(exc).__name__}: {first}"
