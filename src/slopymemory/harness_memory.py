"""The opt-in switch for a harness's OWN file memory (its index, loaded into every request). Off by slopymemory only
when the user asks; on restores exactly what was there — and never over a choice made since. The memory files
themselves are never touched in either direction. User scope only: nothing is written into a project."""
from __future__ import annotations
import json
import os
import stat
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
    STATE_FILE().parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(STATE_FILE(), json.dumps(d, indent=2) + "\n")


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
    """JSON spelling for a value that is only ever True/False/the "absent" sentinel, for messages and choose()."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def _target(f: Path) -> Path:
    """Where a write to `f` actually lands: the symlink's target if `f` is a symlink, so off/on write
    through a dotfile-manager's link instead of replacing the link itself with a regular file."""
    return f.resolve() if f.is_symlink() else f


def _write_settings(f: Path, text: str) -> None:
    target = _target(f)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(target, text)
    if mode is not None:
        os.chmod(target, mode)


def _unlink_settings(f: Path) -> None:
    _target(f).unlink(missing_ok=True)


def _claude_off() -> str:
    recs = _records()
    existing = recs.get("claude-code")
    f = claude_settings()
    text, d = _read_settings(f)
    current = d.get(KEY, "absent")

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
        if rec["original_text"] is None:
            _unlink_settings(f)
        else:
            _write_settings(f, rec["original_text"])
        del recs["claude-code"]; _save_records(recs)
        if deleted and rec["original_text"] is not None:
            return f"Claude Code file memory recreated {f} as it was before slopymemory switched it off"
        return f"Claude Code file memory restored ({KEY} {'removed' if rec['prior'] == 'absent' else '= ' + _spell(rec['prior'])})"

    # other keys changed meanwhile: restore our key only, leave the rest of the current file as it is
    if rec["prior"] == "absent":
        d.pop(KEY, None)
    else:
        d[KEY] = rec["prior"]
    _write_settings(f, json.dumps(d, indent=2) + "\n")
    del recs["claude-code"]; _save_records(recs)
    return f"Claude Code file memory restored ({KEY} {'removed' if rec['prior'] == 'absent' else '= ' + _spell(rec['prior'])})"


def turn_off(harness_id: str) -> str:
    if harness_id == "claude-code":
        return _claude_off()
    raise HarnessMemoryError(f"no known switch for {harness_id}'s own memory{ANCHOR}")


def turn_on(harness_id: str, choose: Callable[[str, str], str]) -> str:
    if harness_id == "claude-code":
        return _claude_on(choose)
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
    return {"claude-code": s}
