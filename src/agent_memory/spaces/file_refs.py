"""File anchors: a memory may point at the files it is about, by path and by the sha256 the file had when the memory
was saved. A plain table beside m3_memory, outside the space machinery — a path is matched exactly, never by
similarity. The store never reads the disk: the caller (or the launcher) sends the hashes."""
import re

MAX_FILES = 20
_HEX = re.compile(r"[0-9a-fA-F]{64}")
_SAVE_KEYS = frozenset({"path", "sha256", "line", "lines"})
_RETRIEVE_KEYS = frozenset({"path", "sha256"})


class FileRefError(ValueError):
    """A malformed `files` argument; the message names the entry and what to fix."""


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _path(raw, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise FileRefError(f"{where}.path must be a non-empty string, got {raw!r}")
    raw = raw.strip()
    if "\\" in raw:
        raise FileRefError(f"{where}.path must use / separators, got {raw!r}")
    if raw.startswith("/") or re.match(r"[A-Za-z]:", raw):
        raise FileRefError(f"{where}.path must be relative to the project root, got {raw!r}")
    if raw.endswith("/"):
        raise FileRefError(f"{where}.path must name a file, not a folder, got {raw!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise FileRefError(f"{where}.path must stay inside the project root (no '..'), got {raw!r}")
    if not parts:
        raise FileRefError(f"{where}.path must name a file, got {raw!r}")
    return "/".join(parts)


def clean_path(raw) -> str | None:
    """The path as the store keeps it, or None when the store would refuse it — the launcher hashes exactly this."""
    if isinstance(raw, str) and "\x00" in raw:            # refused by the server, by name
        return None
    try:
        return _path(raw, "files")
    except FileRefError:
        return None


def _lines(e: dict, where: str) -> tuple[int | None, int | None]:
    if "line" in e and "lines" in e:
        raise FileRefError(f"{where} takes line or lines, not both")
    if "line" in e:
        if not _whole(e["line"]):
            raise FileRefError(f"{where}.line must be a whole number of at least 1, got {e['line']!r}")
        return e["line"], e["line"]
    if "lines" in e:
        v = e["lines"]
        if not (isinstance(v, list) and len(v) == 2 and all(_whole(x) for x in v) and v[0] <= v[1]):
            raise FileRefError(f"{where}.lines must be [start, end], whole numbers with 1 <= start <= end, got {v!r}")
        return v[0], v[1]
    return None, None


def normalize(files, *, arg: str, save: bool) -> list[dict] | None:
    """`files` as the store keeps it, or FileRefError naming the first bad entry. A save ref always has a hash; a
    retrieve ref has `sha256` only when the caller sent the key (null = the file is gone)."""
    if files is None:
        return None
    shape = "{path, sha256, line | lines}" if save else "{path, sha256?}"
    if not isinstance(files, list):
        raise FileRefError(f"{arg} must be a list of {shape} objects, got {type(files).__name__}")
    if len(files) > MAX_FILES:
        raise FileRefError(f"{arg} takes at most {MAX_FILES} files, got {len(files)}")
    allowed = _SAVE_KEYS if save else _RETRIEVE_KEYS
    out: list[dict] = []
    for i, e in enumerate(files):
        where = f"{arg}[{i}]"
        if not isinstance(e, dict):
            raise FileRefError(f"{where} must be an object like {{\"path\": \"src/app.py\"}}, got {e!r}")
        if unknown := set(e) - allowed:
            raise FileRefError(f"{where} has unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")
        ref: dict = {"path": _path(e.get("path"), where)}
        if "sha256" in e:
            h = e["sha256"]
            if h is None and not save:
                ref["sha256"] = None
            elif isinstance(h, str) and _HEX.fullmatch(h):
                ref["sha256"] = h.lower()
            else:
                raise FileRefError(f"{where}.sha256 must be 64 hex characters{'' if save else ' or null (the file is gone)'}"
                                   f", got {h!r}")
        elif save:
            raise FileRefError(f"{where}.sha256 is required — send the file's sha256, or update slopymemory so its "
                               "launcher computes it")
        if save:
            ref["line_start"], ref["line_end"] = _lines(e, where)
        if not save and any(r["path"] == ref["path"] and r.get("sha256", ref.get("sha256")) != ref.get("sha256")
                            for r in out):
            raise FileRefError(f"{where} names {ref['path']!r} twice with different sha256 values; send each file once")
        if ref not in out:
            out.append(ref)
    return out


def write(conn, memory_id: str, refs: list[dict] | None) -> int:
    """Insert one memory's anchors, on the caller's connection (inside the save's transaction)."""
    if not refs:
        return 0
    with conn.cursor() as cur:
        for r in refs:
            cur.execute("INSERT INTO m3_file_ref (memory_id, path, sha256, line_start, line_end) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (memory_id, r["path"], r["sha256"], r["line_start"], r["line_end"]))
    return len(refs)


def _shown(path, sha256, start, end) -> dict:
    ref = {"path": path, "sha256": sha256}
    if start is not None:
        if start == end:
            ref["line"] = start
        else:
            ref["lines"] = [start, end]
    return ref


def refs_of(conn, memory_ids: list[str]) -> dict[str, list[dict]]:
    if not memory_ids:
        return {}
    out: dict[str, list[dict]] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT memory_id, path, sha256, line_start, line_end FROM m3_file_ref "
                    "WHERE memory_id = ANY(%s) ORDER BY memory_id, path, line_start NULLS FIRST, created_at",
                    (list(memory_ids),))
        for mid, path, sha, start, end in cur.fetchall():
            out.setdefault(mid, []).append(_shown(path, sha, start, end))
    return out


def anchored(conn, paths: list[str]) -> list[tuple[str, str | None]]:
    """The memories anchored to any of `paths`, newest first, with their text."""
    if not paths:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT m.memory_id, m.text FROM m3_memory m WHERE EXISTS "
                    "(SELECT 1 FROM m3_file_ref f WHERE f.memory_id = m.memory_id AND f.path = ANY(%s)) "
                    "ORDER BY m.created_at DESC, m.memory_id", (list(paths),))
        return [(m, t) for m, t in cur.fetchall()]


def anchor(conn, results: list[dict], files: list[dict] | None, k: int) -> list[dict]:
    """ADD-ONLY for results the query reached; then the memories anchored to `files`' paths come first: those the
    query also reached (in its order), then the others newest first (score None — found by path, not ranked), then
    the rest of the results; the list stops at k. Every result with anchors shows them under `files`; an anchor whose
    path the caller sent a hash for gains stale (the hash differs) or missing (the caller sent null)."""
    out = list(results)
    paths = [f["path"] for f in files or ()]
    if paths:
        hits = anchored(conn, paths)
        ids = {m for m, _ in hits}
        if ids:
            reached = [r for r in results if r["memory_id"] in ids]
            seen = {r["memory_id"] for r in reached}
            by_path = [{"memory_id": m, "score": None, "text": t} for m, t in hits if m not in seen]
            out = (reached + by_path + [r for r in results if r["memory_id"] not in ids])[:k]
            for r in out:
                if r["memory_id"] in ids:
                    r["anchored"] = True
    current = {f["path"]: f["sha256"] for f in files or () if "sha256" in f}
    refs = refs_of(conn, [r["memory_id"] for r in out])
    for r in out:
        mine = refs.get(r["memory_id"])
        if not mine:
            continue
        for ref in mine:
            if ref["path"] in current:
                now = current[ref["path"]]
                if now is None:
                    ref["missing"] = True
                else:
                    ref["stale"] = now != ref["sha256"]
        r["files"] = mine
    return out
