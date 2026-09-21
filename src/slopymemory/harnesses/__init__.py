"""The known-harness table: how to detect a harness, how to register the launcher at USER scope (once,
for every project), and where its machine-wide instruction file lives for the optional offer line.
The launcher itself knows nothing about harnesses; this table is the only place that does."""
from __future__ import annotations
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .. import paths

# The one line slopymem offers to append to a harness's machine-wide instruction file. Its prefix is the
# stable part: a later version of the line REPLACES an earlier one found by this prefix, so a file never
# carries two of them. The second sentence is the first layer against secrets in memory (the server's
# guard on memory_save is the second): the shape of a key can be recognised, a password in prose cannot.
OFFER_LINE_PREFIX = "If the memory tools show only"
OFFER_LINE = (f"{OFFER_LINE_PREFIX} `memory_init`, tell the user this project has no memory yet "
              "and offer to set it up. Never save passwords, keys or tokens to memory; save where they live.")


def offer_line_state(f: Path, line: str) -> str:
    """'present' (this exact line is there), 'stale' (a line with the same prefix but other text), 'absent'."""
    if not f.exists():
        return "absent"
    for existing in f.read_text().splitlines():
        if existing.strip().startswith(OFFER_LINE_PREFIX):
            return "present" if existing.strip() == line else "stale"
    return "absent"


def ensure_offer_line(f: Path, line: str) -> str:
    """Make `f` carry `line` exactly once: replace a stale version in place (the rest of the file byte-identical,
    written atomically), append under a `# slopymemory` heading when absent, touch nothing when present.
    Returns what was done: 'present' | 'replaced' | 'appended'."""
    state = offer_line_state(f, line)
    if state == "present":
        return "present"
    if state == "stale":
        # Through the link, at the real file: an atomic replace of the link path would sever a dotfiles symlink
        # and leave the repo copy on the old line. The target keeps its mode.
        target = f.resolve()
        mode = target.stat().st_mode & 0o7777
        lines = target.read_text().split("\n")
        for i, existing in enumerate(lines):
            if existing.strip().startswith(OFFER_LINE_PREFIX):
                lines[i] = line
                break
        paths.write_atomic(target, "\n".join(lines))
        target.chmod(mode)
        return "replaced"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(f"\n# slopymemory\n{line}\n")
    return "appended"


def remove_offer_line(f: Path) -> bool:
    """Take the offer line (this version or a stale one, found by its prefix) out of `f`, and the `# slopymemory`
    heading directly above it — nothing else in the file changes, written atomically through a symlink at the
    real file. False when the file has no such line (it is not touched). For `slopymem uninstall`."""
    if offer_line_state(f, OFFER_LINE) == "absent":
        return False
    target = f.resolve()
    mode = target.stat().st_mode & 0o7777
    lines = target.read_text().split("\n")
    for i, existing in enumerate(lines):
        if existing.strip().startswith(OFFER_LINE_PREFIX):
            start = i
            if i > 0 and lines[i - 1].strip() == "# slopymemory":
                start = i - 1
                if start > 0 and lines[start - 1].strip() == "":      # the blank line `ensure_offer_line` put above the heading
                    start -= 1
            del lines[start:i + 1]
            break
    paths.write_atomic(target, "\n".join(lines))
    target.chmod(mode)
    return True


def read_config(path: Path, parse: Callable[[str], dict]) -> dict | None:
    """Read a config file, parse it, and return the result. Returns None if missing or unparseable.
    On parse error, prints a message to stderr and returns None."""
    if not path.exists():
        return None
    try:
        return parse(path.read_text())
    except Exception as e:
        print(f"{path}: unreadable ({e}) — see SETUP.md#harnesses", file=sys.stderr)
        return None


def entry_named(path: Path, parse: Callable[[str], dict], key: str, launcher: str) -> str | None:
    """The name of the entry in the harness's MCP-server table whose `command` is `launcher`, else None."""
    return next((name for name, entry in servers_table(path, parse, key).items() if entry.get("command") == launcher), None)


def servers_table(path: Path, parse: Callable[[str], dict], key: str) -> dict[str, dict]:
    """The harness's MCP-server table, `key`, as {name: entry} — only the entries that ARE tables. A top
    level, a table or an entry of the wrong shape is noted on stderr and read as absent, never raised."""
    d = read_config(path, parse)
    if d is None:
        return {}
    if not isinstance(d, dict):
        print(f"{path}: the top level is not an object/table — see SETUP.md#harnesses", file=sys.stderr)
        return {}
    servers = d.get(key, {})
    if not isinstance(servers, dict):
        print(f"{path}: {key} is not an object/table — see SETUP.md#harnesses", file=sys.stderr)
        return {}
    good = {}
    for name, entry in servers.items():
        if isinstance(entry, dict):
            good[name] = entry
        else:
            print(f"{path}: {key}.{name} is not an object/table; skipped — see SETUP.md#harnesses", file=sys.stderr)
    return good


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
    # The NAME our launcher is registered under (None when it is not) — entries are matched on their COMMAND, never
    # on the name `memory`: a foreign server registered as `memory` is not ours and is never touched.
    registered_as: Callable[[str], str | None] | None = None
    unregister_cmd: Callable[[str], list[str]] | None = None   # takes that name; the inverse of register_cmd, for `slopymem uninstall`


def launcher_path() -> str:
    return str(paths.venv_python().parent / "slopymem-mcp")


from . import claude_code, codex, pi  # noqa: E402

KNOWN: list[Harness] = [claude_code.HARNESS, codex.HARNESS, pi.HARNESS]


def detected() -> list[Harness]:
    return [h for h in KNOWN if h.detect()]


def status() -> "Finding":
    from ..checks import Finding     # lazy: checks imports this module's status() at its top; this breaks the cycle
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
            # Already registered: the mcp add is skipped, but the offer line is still brought up to date — a
            # re-run of `slopymem register` is how a machine that registered under an older line gets the
            # current one. A current line asks nothing.
            print(f"{h.name}: already registered")
            if h.instructions_file:
                rc |= _offer_line_step(h, yes, confirm, quiet_when_present=True)
            continue
        if h.register_cmd is None:
            print(f"{h.name}: register by hand: {h.config_hint}"); continue
        cmd = h.register_cmd(lp)
        print(f"{h.name}: {' '.join(cmd)}")
        if not confirm("run it?", yes):
            rc = 1; continue
        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"{h.name}: registration failed: {e} — see SETUP.md#harnesses", file=sys.stderr)
            rc = 1; continue
        if h.instructions_file:
            rc |= _offer_line_step(h, yes, confirm)     # an unwritable file is a failure here too, not only a message
    return rc


def _offer_line_step(h: Harness, yes: bool, confirm, quiet_when_present: bool = False) -> int:
    """Offer (and on a yes, apply) the instruction line for one harness. Returns 1 when the user declined an
    update that was needed or the file cannot be read or written, else 0 — a declined append after a fresh
    registration was never a failure."""
    f = h.instructions_file()
    try:
        state = offer_line_state(f, h.offer_line)
        if state == "present":
            if not quiet_when_present:
                print("offer line already present")
            return 0
        verb = "update the offer line in" if state == "stale" else "append the offer line to"
        if not confirm(f"{verb} {f}?", yes):
            print(f"offer line left as it is in {f}"); return 1 if state == "stale" else 0
        done = ensure_offer_line(f, h.offer_line)
    except OSError as e:
        print(f"{h.name}: the offer line could not be read or written in {f}: {e} — see SETUP.md#harnesses", file=sys.stderr)
        return 1
    print("offer line updated" if done == "replaced" else f"offer line appended to {f}")
    return 0


def register_detected(yes: bool) -> int:
    """The installer's step 5: register the launcher in every harness found on this machine, and say when there
    is none — a machine with no known harness is not a failure, it is told how to register later."""
    if not detected():
        print("no known harness detected (Claude Code, Codex, pi); register later with: slopymem register <harness>")
        return 0
    rc = 0
    for h in detected():
        rc |= register(h.id, yes)
    return rc


def unregister(h: Harness, launcher: str) -> int:
    """Take the launcher out of one harness's MCP table with the harness's own remove command, by the NAME the
    entry with our command has — whatever it is called; an entry called `memory` with someone else's command is
    never touched. 0 when done or nothing to do; 1 with the reason on stderr when the command fails or the harness
    has none (then the hint says how, by hand)."""
    name = h.registered_as(launcher) if h.registered_as is not None else None
    if name is None:
        return 0
    if h.unregister_cmd is None:
        print(f"{h.name}: unregister by hand (the entry {name!r}): {h.config_hint} — see SETUP.md#harnesses", file=sys.stderr)
        return 1
    cmd = h.unregister_cmd(name)
    print(f"{h.name}: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"{h.name}: unregistering failed: {e} — see SETUP.md#harnesses", file=sys.stderr)
        return 1
    return 0
