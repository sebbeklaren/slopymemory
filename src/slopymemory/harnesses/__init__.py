"""The known-harness table: how to detect a harness, how to register the launcher at USER scope (once,
for every project), and where its machine-wide instruction file lives for the optional offer line.
The launcher itself knows nothing about harnesses; this table is the only place that does."""
from __future__ import annotations
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .. import paths
from ..checks import Finding

OFFER_LINE = ("If the memory tools show only `memory_init`, tell the user this project has no memory yet "
              "and offer to set it up.")


@dataclass
class Harness:
    id: str
    name: str
    detect: Callable[[], bool]
    registered: Callable[[str], bool]
    register_cmd: Callable[[str], list[str]] | None
    config_hint: str
    instructions_file: Callable[[], Path] | None
    offer_line: str = OFFER_LINE


def launcher_path() -> str:
    return str(paths.venv_python().parent / "slopymem-mcp")


from . import claude_code, codex, pi  # noqa: E402

KNOWN: list[Harness] = [claude_code.HARNESS, codex.HARNESS, pi.HARNESS]


def detected() -> list[Harness]:
    return [h for h in KNOWN if h.detect()]


def status() -> Finding:
    rows, bad = [], False
    lp = launcher_path()
    for h in detected():
        ok = h.registered(lp)
        bad |= not ok
        rows.append(f"{h.name}: {'registered' if ok else 'NOT registered'}")
    return Finding(not bad, "; ".join(rows) or "no known harness detected")


def register(harness_id: str | None, yes: bool) -> int:
    from ..cli import confirm
    lp = launcher_path()
    targets = [h for h in KNOWN if harness_id in (None, h.id)]
    if not targets:
        print(f"unknown harness {harness_id}; known: {[h.id for h in KNOWN]}", file=sys.stderr); return 1
    rc = 0
    for h in targets:
        if not h.detect():
            print(f"{h.name}: not detected on this machine — to register by hand: {h.config_hint}")
            continue
        if h.registered(lp):
            print(f"{h.name}: already registered"); continue
        if h.register_cmd is None:
            print(f"{h.name}: register by hand: {h.config_hint}"); continue
        cmd = h.register_cmd(lp)
        print(f"{h.name}: {' '.join(cmd)}")
        if not confirm("run it?", yes):
            rc = 1; continue
        subprocess.run(cmd, check=True)
        if h.instructions_file and confirm(f"append the offer line to {h.instructions_file()}?", yes):
            f = h.instructions_file(); f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a") as fh:
                fh.write(f"\n# slopymemory\n{h.offer_line}\n")
    return rc
