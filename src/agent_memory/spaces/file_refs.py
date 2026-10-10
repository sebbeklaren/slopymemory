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
        if ref not in out:
            out.append(ref)
    return out
