"""The opt-in switch for a harness's OWN file memory (its index, loaded into every request). Off by slopymemory only
when the user asks; on restores exactly what was there — and never over a choice made since. The memory files
themselves are never touched in either direction. User scope only: nothing is written into a project."""
from __future__ import annotations
import json
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


def _claude_off() -> str:
    recs = _records()
    if "claude-code" in recs:
        return "Claude Code file memory is already off by slopymemory"
    f = claude_settings()
    text, d = _read_settings(f)
    recs["claude-code"] = {"file_existed": text is not None, "original_text": text,
                           "prior": d.get(KEY, "absent"), "wrote": False}
    _save_records(recs)                                   # the record first: a crash after it still restores
    d[KEY] = False
    written = json.dumps(d, indent=2) + "\n"
    f.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(f, written)
    recs["claude-code"]["written_text"] = written
    _save_records(recs)
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
    if current != rec["wrote"]:                           # changed since we wrote it: never clobber a newer choice
        if choose(str(rec["prior"]), str(current)) != "restore":
            del recs["claude-code"]; _save_records(recs)
            return f"Claude Code file memory: kept the current {KEY} = {current} (it changed after slopymemory set it)"
    if text is None or text == rec.get("written_text"):   # nothing else survives to preserve: put the original back
        if rec["original_text"] is None:
            f.unlink(missing_ok=True)
        else:
            paths.write_atomic(f, rec["original_text"])
    else:                                                 # other keys changed meanwhile: restore our key only
        if rec["prior"] == "absent":
            d.pop(KEY, None)
        else:
            d[KEY] = rec["prior"]
        paths.write_atomic(f, json.dumps(d, indent=2) + "\n")
    del recs["claude-code"]; _save_records(recs)
    return f"Claude Code file memory restored ({KEY} {'removed' if rec['prior'] == 'absent' else '= ' + str(rec['prior']).lower()})"


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
    if "claude-code" in recs:
        s = "off (slopymemory)"
    elif d.get(KEY) is False:
        s = "off (not by slopymemory)"
    else:
        s = "on"
    return {"claude-code": s}
