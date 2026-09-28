"""Which version is installed, where it came from, and whether a newer one is out. Nothing here updates anything: it
records the install, checks once a day — only when the user said yes at install — and words one notice line.

The check is `git ls-remote --tags` against the user's own clone's remote: the only thing sent is that git request,
and a fork checks its fork. A failure (no git, no network, a timeout) is recorded in the cache for `slopymem doctor`
and never raised: an optional check must never break the tool that runs it."""
from __future__ import annotations

import importlib.metadata as md
import json
import os
import re
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import tomli_w

from . import paths

DAY = 24 * 3600
TIMEOUT_S = 3
_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")


def install_file() -> Path:
    return paths.home() / "install.toml"


def cache_file() -> Path:
    return paths.home() / "update-check.json"


def installed() -> str:
    return md.version("slopymemory")


def read_install() -> dict | None:
    try:
        return tomllib.loads(install_file().read_text())
    except (OSError, ValueError):             # TOMLDecodeError and UnicodeDecodeError are both ValueErrors
        return None


def install_state() -> str:
    """missing (installed before 0.4), unreadable (the file exists and does not read), or ok."""
    if not install_file().exists():
        return "missing"
    return "ok" if read_install() is not None else "unreadable"


def install_flags() -> list[str]:
    """The installer options recorded at install — a list, or a string someone wrote by hand."""
    import shlex
    flags = (read_install() or {}).get("flags", [])
    if isinstance(flags, str):
        try:
            return shlex.split(flags)
        except ValueError:
            return []
    return [str(f) for f in flags] if isinstance(flags, list) else []


def write_install(source: Path, flags: list[str], update_check: bool, remote: str | None) -> None:
    info = {"source": str(source), "flags": list(flags), "version": installed(),
            "installed": datetime.now(timezone.utc), "update_check": bool(update_check)}
    if remote:
        info["remote"] = remote
    paths.write_atomic(install_file(), tomli_w.dumps(info))


def set_check(on: bool) -> None:
    """Change the answer in an existing install record. Without a valid record there is nothing to change: writing
    a record with only this key would look like an install that has no source."""
    info = read_install()
    if not info or not isinstance(info.get("source"), str) or not info["source"]:
        raise ValueError("there is no valid install record (~/.slopymemory/install.toml)")
    info["update_check"] = bool(on)
    paths.write_atomic(install_file(), tomli_w.dumps(info))


def parse(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION.fullmatch(tag.strip())
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def latest_of(tags: list[str]) -> str | None:
    parsed = [p for p in (parse(t) for t in tags) if p]
    return ".".join(map(str, max(parsed))) if parsed else None


def read_cache() -> dict:
    try:
        c = json.loads(cache_file().read_text())
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh(now: float | None = None, run=subprocess.run) -> dict:
    """Check the remote when the user said yes, a remote is recorded and the cache is older than a day; return the
    cache either way. Never raises."""
    now = time.time() if now is None else now
    info, cache = read_install(), read_cache()
    if not info or not info.get("update_check") or not info.get("remote"):
        return cache
    if isinstance(cache.get("checked"), (int, float)) and now - cache["checked"] < DAY:
        return cache
    latest, error = None, None
    try:
        # Never a prompt: no terminal, no ssh question, no credential helper asking — a background check that
        # needs one simply fails. Its own session, so a timeout's kill takes ssh with it.
        out = run(["git", "ls-remote", "--tags", "--refs", info["remote"]], capture_output=True, text=True,
                  timeout=TIMEOUT_S, stdin=subprocess.DEVNULL, start_new_session=True,
                  env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"})
        if out.returncode != 0:
            first = (out.stderr or "").strip().splitlines()
            error = f"git ls-remote failed: {first[0] if first else f'exit status {out.returncode}'}"
        else:
            tags = [line.rsplit("refs/tags/", 1)[-1] for line in out.stdout.splitlines() if "refs/tags/" in line]
            latest = latest_of([t for t in tags if t.startswith("v")])      # release tags only, as `update` counts
    except FileNotFoundError:
        error = "git not found on PATH — the update check needs it"
    except subprocess.TimeoutExpired:
        error = f"git ls-remote timed out after {TIMEOUT_S} s"
    except OSError as e:
        error = f"git ls-remote could not run: {e}"
    cache = {"checked": now, "latest": latest, "installed": installed(), "error": error}
    try:
        paths.write_atomic(cache_file(), json.dumps(cache))
    except OSError:
        pass
    return cache


def newer() -> str | None:
    latest = read_cache().get("latest")
    info = read_install()
    if not isinstance(latest, str) or not info or not info.get("update_check"):
        return None
    lp, ip = parse(latest), parse(installed())
    return latest if lp and ip and lp > ip else None


def notice() -> str | None:
    v = newer()
    return (f"slopymemory v{v} is available (installed v{installed()}) — tell the user: run `slopymem update`."
            if v else None)
