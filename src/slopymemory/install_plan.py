"""`install.sh --plan --json`: what an install would do, reported before it does anything. Standard library only — it
runs on the bare interpreter before the package exists, like the preflight — and it never writes, asks or downloads.
A harness may build a consent screen from this and nothing else, so every place the install writes, every other
program it changes, and every download is named here, by the same code the install itself runs."""
from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

from . import install_steps as st
from . import harnesses as hs

SCHEMA = 1


def _version() -> str:
    return tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())["project"]["version"]


def _uv_cache() -> Path:
    """uv's cache folder by uv's own documented rules (UV_CACHE_DIR, else XDG_CACHE_HOME/uv, else ~/.cache/uv) —
    never by running `uv cache dir`, which creates the folder as a side effect."""
    if (d := os.environ.get("UV_CACHE_DIR")):
        return Path(d)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv"


def _has_python313() -> bool:
    """A Python 3.13+ on PATH or among uv's managed ones — looked up without running uv (`uv python find` writes
    its interpreter cache)."""
    for name in ("python3.13", "python3.14", "python3.15"):
        if shutil.which(name):
            return True
    data = Path(os.environ.get("UV_PYTHON_INSTALL_DIR")
                or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "uv" / "python")
    return data.is_dir() and any(c.name.startswith(("cpython-3.13", "cpython-3.14", "cpython-3.15"))
                                 for c in data.iterdir())


def _packages(lock: Path, cache) -> dict:
    """What `uv sync` downloads: the lock's wheels for this platform, minus what uv's cache already holds."""
    wheels = st.locked_wheels(lock, st.platform_tags(), root=st.ROOT_PACKAGE)
    offline = os.environ.get("SLOPYMEM_PLAN_OFFLINE") == "1"           # tests: never ask an index for a size
    sizes = {} if offline else {w.name: s for w in wheels if w.size is None if (s := st.remote_size(w.url)) is not None}
    full, per_all = st.download_size(lock, st.platform_tags(), set(), sizes, root=st.ROOT_PACKAGE)
    cached = st.cached_wheels(cache, wheels) if cache is not None else None
    total = st.download_size(lock, st.platform_tags(), cached, sizes, root=st.ROOT_PACKAGE)[0] if cached else full
    unknown = sorted(n for n, v in per_all.items() if v is None)
    return {"what": "packages", "bytes": total, "bytes_without_cache": full, "count": len(per_all),
            "unknown_size": unknown, "note": "Python packages from uv.lock into the venv (PyTorch CPU included)"}


def _harness_changes(h, launcher: str) -> list[str]:
    changes = []
    if not h.registered(launcher):
        changes.append(f"run: {' '.join(h.register_cmd(launcher))}" if h.register_cmd
                       else f"nothing automatic; register by hand: {h.config_hint}")
    f = h.instructions_file() if h.instructions_file else None
    if f is not None and hs.offer_line_state(f, h.offer_line) != "present":
        changes.append(f"add one line to {f}")
    return changes


def plan(venv: Path, home: Path, *, harness: bool = True, postgres: str | None = None, update_check: bool = True,
         known=None, lock: Path | None = None, cache="auto") -> dict:
    from agent_memory.config import settings          # standard library only: the pinned model revision
    venv, home = Path(venv), Path(home)
    lock = Path(lock) if lock is not None else st.checkout_lock()
    cache = (_uv_cache() if _uv_cache().is_dir() else None) if cache == "auto" else cache
    blockers, downloads, writes = [], [], [str(home)]
    ok, msg = st.check_tools()
    if not ok:
        blockers.append(msg)
    free = st.free_bytes(venv)
    if free < st.MIN_FREE_BYTES:
        blockers.append(f"only {free / 2**30:.1f} GB free where the venv goes; the install wants 4 GB")
    if shutil.which("uv") is None:
        downloads.append({"what": "uv", "bytes": None, "note": "the official installer from astral.sh, into ~/.local/bin"})
        writes.append(str(Path.home() / ".local" / "bin"))
    if not _has_python313():
        downloads.append({"what": "python", "bytes": None, "note": "Python 3.13 through uv (about 30 MB)"})
        writes.append("uv's Python directory (`uv python dir`)")
    downloads.append(_packages(lock, cache))
    writes.append(str(cache or _uv_cache()))
    if st.weights_complete(settings.embed_model, settings.embed_revision) is None:
        downloads.append({"what": "model", "bytes": None, "note": f"{settings.embed_model}, {st.MODEL_SIZE_NOTE}"})
        writes.append(str(st.hub_cache()))
    pg_writes = [str(home / "pg")] if postgres == "embedded" else []
    pg = {"choice": postgres or "the system Postgres when it can create databases, else the embedded one",
          "writes": pg_writes,
          "note": ("the system Postgres gets one database per store, and a scratch database to verify it"
                   if postgres != "embedded" else "PostgreSQL 18 + pgvector from a package, under the home")}
    if postgres is None:
        pg["writes"] = [str(home / "pg") + " (only if the embedded one is chosen)"]
    launcher = str(venv / "bin" / "slopymem-mcp")
    found = [h for h in (hs.KNOWN if known is None else known) if h.detect()] if harness else []
    return {"schema": SCHEMA, "version": _version(), "home": str(home), "venv": str(venv),
            "launcher": launcher, "unattended_flag": "--yes",
            "writes": writes, "postgres": pg,
            "update_check": {"enabled": update_check,
                             "note": "once a day, `git ls-remote` against the remote of the clone it was installed from; "
                                     "nothing about you is sent. Off: --no-update-check"},
            "harnesses": [{"id": h.id, "name": h.name, "registered": h.registered(launcher),
                           "changes": _harness_changes(h, launcher)} for h in found],
            "downloads": downloads, "free_bytes_needed": st.MIN_FREE_BYTES, "free_bytes": free,
            "blockers": blockers}
