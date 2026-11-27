"""`install.sh --plan --json`: what an install would do, reported before it does anything. Standard library only — it
runs on the bare interpreter before the package exists, like the preflight — and it never writes, asks or downloads
(it does not run uv either: `uv python find` and `uv cache dir` create uv's folders). It may ask package indexes for
wheel sizes they do not publish in the lock (HEAD requests). A harness may build a consent screen from this and
nothing else, so every place the install writes, every other program it changes, and every download is named here,
following the same rules the install's own steps follow."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import install_steps as st
from . import harnesses as hs

SCHEMA = 1


def _version() -> str:
    return tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())["project"]["version"]


def _env_dir(var: str, xdg: str, default: Path, *rest: str) -> Path:
    """`$var`, else `$xdg/<rest>`, else `default/<rest>` — the XDG rule uv and huggingface_hub follow."""
    if (d := os.environ.get(var)):
        return Path(d)
    return Path(os.environ.get(xdg) or default).joinpath(*rest)


def _uv_cache() -> Path:
    return _env_dir("UV_CACHE_DIR", "XDG_CACHE_HOME", Path.home() / ".cache", "uv")


def _uv_python_dir() -> Path:
    return _env_dir("UV_PYTHON_INSTALL_DIR", "XDG_DATA_HOME", Path.home() / ".local" / "share", "uv", "python")


def _bin_dir() -> Path:
    """Where uv puts its own binary and, from uv 0.8, the `python3.13` links of a Python it installs."""
    if (d := os.environ.get("UV_PYTHON_BIN_DIR") or os.environ.get("XDG_BIN_HOME")):
        return Path(d)
    return Path.home() / ".local" / "bin"


def _python_version(exe: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run([exe, "-c", "import sys; print(*sys.version_info[:2])"], capture_output=True,
                             text=True, timeout=10, check=True).stdout.split()
        return int(out[0]), int(out[1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _has_python313() -> bool:
    """What `uv python find --system 3.13` would find (3.13 exactly), looked up without running uv: a 3.13 on PATH
    under any of the usual names, or a uv-managed 3.13."""
    for name in ("python3.13", "python3", "python"):
        if (exe := shutil.which(name)) and _python_version(exe) == (3, 13):
            return True
    d = _uv_python_dir()
    return d.is_dir() and any(c.name.startswith("cpython-3.13") for c in d.iterdir())


def _packages(lock: Path, cache) -> dict:
    """What `uv sync` downloads: the lock's wheels for this platform, minus what uv's cache already holds."""
    wheels = st.locked_wheels(lock, st.platform_tags(), root=st.ROOT_PACKAGE)
    offline = os.environ.get("SLOPYMEM_PLAN_OFFLINE") == "1"           # tests: never ask an index for a size
    sizes = {} if offline else {w.name: s for w in wheels if w.size is None if (s := st.remote_size(w.url)) is not None}
    full, per_all = st.download_size(lock, st.platform_tags(), set(), sizes, root=st.ROOT_PACKAGE)
    cached = st.cached_wheels(cache, wheels) if cache is not None else None
    total = st.download_size(lock, st.platform_tags(), cached, sizes, root=st.ROOT_PACKAGE)[0] if cached else full
    unknown = sorted(n for n, v in per_all.items() if v is None)
    return {"what": "packages", "bytes": total, "bytes_without_cache": full, "bytes_complete": not unknown,
            "count": len(per_all), "unknown_size": unknown,
            "note": "Python packages from uv.lock into the venv (PyTorch CPU included)"
                    + ("; the total leaves out the packages of unknown size" if unknown else "")}


def _harness_changes(h, launcher: str) -> list[str]:
    """What `register --detected --yes` does to this harness, step for step (harnesses._register_one)."""
    changes = []
    name = h.registered_as(launcher) if h.registered_as is not None else None
    if h.registered(launcher):
        if name == hs.LEGACY_NAME and h.register_cmd and h.unregister_cmd:          # _rename_step
            changes += [f"run: {' '.join(h.register_cmd(launcher))}", f"run: {' '.join(h.unregister_cmd(name))}",
                        f"the tools are renamed mcp__{hs.LEGACY_NAME}__* -> mcp__{hs.SERVER_NAME}__*"]
            name = hs.SERVER_NAME
    elif h.register_cmd:
        changes.append(f"run: {' '.join(h.register_cmd(launcher))}")
    else:
        return [f"nothing automatic; register by hand: {h.config_hint}"]
    f = h.instructions_file() if h.instructions_file else None
    if f is not None:                                                                 # _offer_line_step
        state = hs.offer_line_state(f, hs.trigger_line(name or hs.SERVER_NAME))
        if state == "stale":
            changes.append(f"replace the slopymemory line in {f}")
        elif state == "absent":
            changes.append(f"add one line to {f}")
    return changes


def plan(venv: Path, home: Path, *, harness: bool = True, postgres: str | None = None, update_check: bool = True,
         known=None, lock: Path | None = None, cache="auto") -> dict:
    from agent_memory.config import settings          # standard library only: the pinned model revisions
    venv, home = Path(venv), Path(home)
    lock = Path(lock) if lock is not None else st.checkout_lock()
    cache = (_uv_cache() if _uv_cache().is_dir() else None) if cache == "auto" else cache
    blockers, downloads, writes = [], [], [str(home)]
    if platform.system() != "Linux":
        blockers.append(f"the installer is Linux only; this is {platform.system()}")
    ok, msg = st.check_tools()
    if not ok:
        blockers.append(msg)
    free = st.free_bytes(venv)
    if free < st.MIN_FREE_BYTES:
        blockers.append(f"only {free / 2**30:.1f} GB free where the venv goes; the install wants 4 GB")
    if shutil.which("uv") is None:
        if shutil.which("curl") is None:
            blockers.append("uv is not installed and curl is not there to fetch its installer")
        downloads.append({"what": "uv", "bytes": None, "note": "the official installer from astral.sh"})
        writes += [str(_bin_dir()), str(_env_dir("XDG_CONFIG_HOME", "XDG_CONFIG_HOME", Path.home() / ".config") / "uv")]
    if not _has_python313():
        downloads.append({"what": "python", "bytes": None, "note": "Python 3.13 through uv (about 30 MB)"})
        writes += [str(_uv_python_dir()), str(_bin_dir())]
    downloads.append(_packages(lock, cache))
    writes.append(str(cache or _uv_cache()))
    weights = st.weights_complete(settings.embed_model, settings.embed_revision)
    if weights is None:
        downloads.append({"what": "model", "bytes": None,
                          "note": f"{settings.embed_model} and the code repository it names, {st.MODEL_SIZE_NOTE}"})
    else:
        missing = [r for r in st.remote_code_repos(weights) if st.local_snapshot(r, settings.embed_code_revision) is None]
        if missing:
            downloads.append({"what": "model code", "bytes": None, "note": f"{', '.join(missing)} (small)"})
    if any(d["what"] in ("model", "model code") for d in downloads):
        writes.append(str(st.hub_cache_from_env()))
    pg = {"choice": postgres or "the system Postgres when it can create databases, else the embedded one",
          "writes": ([str(home / "pg")] if postgres == "embedded" else [] if postgres == "system"
                     else [str(home / "pg") + " (only if the embedded one is chosen)"]),
          "note": ("PostgreSQL 18 + pgvector from a package, under the home" if postgres == "embedded" else
                   "the system Postgres gets one database per store, and a scratch database to verify it")}
    launcher = str(venv / "bin" / "slopymem-mcp")
    found = [h for h in (hs.KNOWN if known is None else known) if h.detect()] if harness else []
    return {"schema": SCHEMA, "version": _version(), "home": str(home), "venv": str(venv),
            "launcher": launcher, "unattended_flag": "--yes",
            "writes": list(dict.fromkeys(writes)), "postgres": pg,
            "update_check": {"enabled": update_check,
                             "note": "once a day, `git ls-remote` against the remote of the clone it was installed from; "
                                     "nothing about you is sent. Off: --no-update-check"},
            "harnesses": [{"id": h.id, "name": h.name, "registered": h.registered(launcher), "config": h.config_hint,
                           "instructions_file": str(h.instructions_file()) if h.instructions_file else None,
                           "changes": _harness_changes(h, launcher)} for h in found],
            "downloads": downloads, "free_bytes_needed": st.MIN_FREE_BYTES, "free_bytes": free,
            "blockers": blockers}


def main(argv: list[str]) -> int:
    """install.sh's entry: venv, home, harness (1/0), postgres ('' = default), update check ('no' = off). Prints one
    JSON object either way: the plan, or a blocker naming what stopped the planning."""
    venv, home, harness, pg, check = argv
    try:
        out, code = plan(Path(venv), Path(home), harness=harness == "1", postgres=pg or None,
                         update_check=check != "no"), 0
    except Exception as e:                       # a consent screen needs JSON, never a traceback
        out, code = {"schema": SCHEMA, "blockers": [f"the plan could not be made: {type(e).__name__}: {e}"]}, 1
    print(json.dumps(out))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
