"""The opt-in switch for a harness's OWN file memory (its index, loaded into every request). Off by slopymemory only
when the user asks; on restores exactly what was there — and never over a choice made since. The memory files
themselves are never touched in either direction. User scope only: nothing is written into a project."""
from __future__ import annotations
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable
from . import paths

KEY = "autoMemoryEnabled"
ANCHOR = " — see SETUP.md#harness-memory"


class HarnessMemoryError(Exception):
    pass


def STATE_FILE() -> Path:
    return paths.home() / "harness-memory.json"


def claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _records() -> dict:
    f = STATE_FILE()
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text())
    except ValueError as e:
        raise HarnessMemoryError(f"{f} does not read ({e}); nothing changed{ANCHOR}") from None
    if not isinstance(d, dict):
        raise HarnessMemoryError(f"{f} is not a JSON object; nothing changed{ANCHOR}")
    return d


def _save_records(d: dict) -> None:
    if not d:
        STATE_FILE().unlink(missing_ok=True)   # no record left: no file left for `slopymem uninstall` to name
        return
    STATE_FILE().parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(STATE_FILE(), json.dumps(d, indent=2) + "\n")


def has_record(harness_id: str) -> bool:
    """Whether slopymemory holds a record of having switched `harness_id`'s own memory off — true even when
    the harness's CURRENT state cannot be read right now (e.g. `codex features list` failing), so a caller
    can tell "no record" apart from "a record exists but its live state can't be compared this moment"."""
    return harness_id in _records()


def _read_settings(f: Path) -> tuple[str | None, dict]:
    if not f.exists():
        return None, {}
    text = f.read_text()
    try:
        d = json.loads(text)
    except ValueError as e:
        raise HarnessMemoryError(f"{f} is not valid JSON ({e}); it was not changed{ANCHOR}") from None
    if not isinstance(d, dict):
        raise HarnessMemoryError(f"{f} is not a JSON object; it was not changed{ANCHOR}")
    return text, d


def _json_eq(a, b) -> bool:
    """Type-strict equality for parsed JSON values — unlike Python's `==`, `0` is not `false` and `1` is
    not `true`, since `bool` is a subclass of `int`."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _spell(v) -> str:
    """JSON spelling for a value, for messages and choose(): True/False/None/the "absent" sentinel as
    true/false/null/absent, not Python's spellings."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    return str(v)


def _target(f: Path) -> Path:
    """Where a write to `f` actually lands: the symlink's target if `f` is a symlink, so off/on write
    through a dotfile-manager's link instead of replacing the link itself with a regular file."""
    return f.resolve() if f.is_symlink() else f


def _replace(tmp: Path, target: Path) -> None:
    os.replace(tmp, target)


def _atomic_write_with_mode(target: Path, text: str, mode: int | None) -> None:
    """Write `text` to `target` atomically, with `mode` (if given) set on the temp file the moment it is
    CREATED — via `os.open`'s own mode argument, never a chmod once it already holds content — so a 0600
    file is never even briefly at the umask's mode. A failure anywhere here (opening, writing, or the
    rename) removes the temp file and leaves `target` completely untouched: there is no post-replace step
    left to fail, and no stray `.tmp` left behind either."""
    tmp = target.with_name(target.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode if mode is not None else 0o666)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        _replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_settings(f: Path, text: str) -> None:
    target = _target(f)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_with_mode(target, text, mode)


def _unlink_settings(f: Path) -> None:
    _target(f).unlink(missing_ok=True)


def _clear_record_after_restore(recs: dict, key: str, message: str) -> str:
    """Called once the setting itself has already been put back — this only clears slopymemory's own
    bookkeeping of that. If THIS write fails, the setting is already fine; only the record is stuck, so
    the failure says exactly that (never a bare OSError) rather than leaving the next `status` to wrongly
    report the setting as changed since slopymemory itself is the one that changed it back."""
    del recs[key]
    try:
        _save_records(recs)
    except OSError as e:
        raise HarnessMemoryError(
            f"the setting was restored, but the record of it could not be cleared ({e}); until it is, "
            f"`slopymem harness-memory status` may wrongly say it changed since — run `slopymem harness-memory on` "
            f"again to clear it{ANCHOR}"
        ) from None
    return message


def _claude_off() -> str:
    recs = _records()
    existing = recs.get("claude-code")
    f = claude_settings()
    text, d = _read_settings(f)
    current = d.get(KEY, "absent")

    if existing is None and current is False:
        # already off, by the user's own choice or an earlier install: record it as slopymemory's own so
        # `on` restores it in meaning, but never rewrite the file for a value that is already what we want
        recs["claude-code"] = {"file_existed": True, "original_text": text,
                               "prior": False, "wrote": False, "written_text": text}
        _save_records(recs)
        return "Claude Code file memory was already off; recorded, nothing changed"

    if existing is not None:
        if _json_eq(current, existing["wrote"]):
            return "Claude Code file memory is already off by slopymemory (unchanged)"
        # it changed back since the record was made — reapply, but keep the ORIGINAL restore target
        prior, original_text, file_existed = existing["prior"], existing["original_text"], existing["file_existed"]
    else:
        prior, original_text, file_existed = current, text, text is not None

    d[KEY] = False
    written = json.dumps(d, indent=2) + "\n"
    recs["claude-code"] = {"file_existed": file_existed, "original_text": original_text,
                           "prior": prior, "wrote": False, "written_text": written}
    _save_records(recs)                    # the record first, already complete: a crash after it still restores
    try:
        _write_settings(f, written)
    except OSError as e:
        if existing is not None:
            recs["claude-code"] = existing
        else:
            del recs["claude-code"]
        _save_records(recs)
        raise HarnessMemoryError(f"could not write {f} ({e}); it was not changed{ANCHOR}") from None

    if existing is not None:
        return f"Claude Code file memory switched OFF again ({f}: {KEY} = false) — it had been switched back on since"
    return f"Claude Code file memory switched OFF ({f}: {KEY} = false)"


def _claude_on(choose: Callable[[str, str], str]) -> str:
    recs = _records()
    rec = recs.get("claude-code")
    if rec is None:
        raise HarnessMemoryError("no record of slopymemory switching Claude Code's file memory off, so there is "
                                 f"nothing to restore; {KEY} in {claude_settings()} is yours to set{ANCHOR}")
    f = claude_settings()
    text, d = _read_settings(f)
    current = d.get(KEY, "absent")
    if not _json_eq(current, rec["wrote"]):                # changed since we wrote it: never clobber a newer choice
        if choose(_spell(rec["prior"]), _spell(current)) != "restore":
            del recs["claude-code"]; _save_records(recs)
            return f"Claude Code file memory: kept the current {KEY} = {_spell(current)} (it changed after slopymemory set it)"

    deleted = text is None
    written_text = rec.get("written_text")
    unchanged = (not deleted) and written_text is not None and _json_eq(d, json.loads(written_text))

    if deleted or unchanged:                                # nothing of substance survives to preserve: put the original back
        try:
            if rec["original_text"] is None:
                _unlink_settings(f)
            else:
                _write_settings(f, rec["original_text"])
        except OSError as e:
            # the record is left exactly as it was — untouched above — so the restore target survives and a
            # retry can succeed once whatever blocked the write clears
            raise HarnessMemoryError(f"could not restore {f} ({e}); it was not changed{ANCHOR}") from None
        message = (f"Claude Code file memory recreated {f} as it was before slopymemory switched it off"
                   if deleted and rec["original_text"] is not None else
                   f"Claude Code file memory restored ({KEY} {'removed' if rec['prior'] == 'absent' else '= ' + _spell(rec['prior'])})")
        return _clear_record_after_restore(recs, "claude-code", message)

    # other keys changed meanwhile: restore our key only, leave the rest of the current file as it is
    if rec["prior"] == "absent":
        d.pop(KEY, None)
    else:
        d[KEY] = rec["prior"]
    try:
        _write_settings(f, json.dumps(d, indent=2) + "\n")
    except OSError as e:
        raise HarnessMemoryError(f"could not restore {f} ({e}); it was not changed{ANCHOR}") from None
    message = f"Claude Code file memory restored ({KEY} {'removed' if rec['prior'] == 'absent' else '= ' + _spell(rec['prior'])})"
    return _clear_record_after_restore(recs, "claude-code", message)


def _codex_available() -> bool:
    return shutil.which("codex") is not None


def _codex(*args: str) -> str:
    if not _codex_available():
        raise HarnessMemoryError(f"codex is not on PATH; nothing to do for Codex's own memory{ANCHOR}")
    try:
        return subprocess.run(["codex", *args], check=True, capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        # OSError also catches FileNotFoundError — a race against the check above, not the normal path
        raise HarnessMemoryError(f"`codex {' '.join(args)}` failed: {getattr(e, 'stderr', '') or e}{ANCHOR}") from None


def codex_memories_enabled() -> bool:
    for line in _codex("features", "list").splitlines():
        parts = line.split()
        if parts and parts[0] == "memories":
            return parts[-1] == "true"
    raise HarnessMemoryError(f"`codex features list` has no `memories` feature in this Codex version{ANCHOR}")


def _codex_off() -> str:
    recs = _records()
    existing = recs.get("codex")
    current = codex_memories_enabled()

    if existing is not None and current == existing["wrote"]:
        return "Codex memories are already off by slopymemory (unchanged)"

    prior = existing["prior"] if existing is not None else current
    recs["codex"] = {"prior": prior, "wrote": False}
    _save_records(recs)                    # the record first, already complete: a crash after it still restores
    if current:
        try:
            _codex("features", "disable", "memories")
        except HarnessMemoryError:
            if existing is not None:
                recs["codex"] = existing
            else:
                del recs["codex"]
            _save_records(recs)
            raise

    if existing is not None:
        return "Codex memories switched OFF again — it had been switched back on since"
    return "Codex memories switched OFF" if current else "Codex memories were already off; recorded, nothing changed"


def _codex_on(choose: Callable[[str, str], str]) -> str:
    recs = _records()
    rec = recs.get("codex")
    if rec is None:
        raise HarnessMemoryError(f"no record of slopymemory switching Codex memories off; nothing to restore{ANCHOR}")
    current = codex_memories_enabled()
    if current != rec["wrote"]:                            # changed since we wrote it: never clobber a newer choice
        if choose(_spell(rec["prior"]), _spell(current)) != "restore":
            del recs["codex"]; _save_records(recs)
            return f"Codex memories: kept the current state ({'on' if current else 'off'})"

    if rec["prior"] and not current:
        _codex("features", "enable", "memories")
    elif not rec["prior"] and current:
        _codex("features", "disable", "memories")
    message = f"Codex memories restored ({'on' if rec['prior'] else 'off'})"
    return _clear_record_after_restore(recs, "codex", message)


def turn_off(harness_id: str) -> str:
    if harness_id == "claude-code":
        return _claude_off()
    if harness_id == "codex":
        return _codex_off()
    raise HarnessMemoryError(f"no known switch for {harness_id}'s own memory{ANCHOR}")


def turn_on(harness_id: str, choose: Callable[[str, str], str]) -> str:
    if harness_id == "claude-code":
        return _claude_on(choose)
    if harness_id == "codex":
        return _codex_on(choose)
    raise HarnessMemoryError(f"no known switch for {harness_id}'s own memory{ANCHOR}")


def status() -> dict[str, str]:
    recs = _records()
    _, d = _read_settings(claude_settings())
    rec = recs.get("claude-code")
    if rec is not None:
        current = d.get(KEY, "absent")
        s = "off (slopymemory)" if _json_eq(current, rec["wrote"]) else "on (changed since slopymemory switched it off)"
    elif d.get(KEY) is False:
        s = "off (not by slopymemory)"
    else:
        s = "on"
    out = {"claude-code": s}

    if _codex_available():
        crec = recs.get("codex")
        try:
            enabled = codex_memories_enabled()
        except HarnessMemoryError as e:
            out["codex"] = f"unknown ({e})"
        else:
            if crec is not None:
                out["codex"] = "off (slopymemory)" if enabled == crec["wrote"] else "on (changed since slopymemory switched it off)"
            elif not enabled:
                out["codex"] = "off (not by slopymemory)"
            else:
                out["codex"] = "on"
    return out
