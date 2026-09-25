"""The known-harness table: how to detect a harness, how to register the launcher at USER scope (once,
for every project), and where its machine-wide instruction file lives for the optional offer line.
The launcher itself knows nothing about harnesses; this table is the only place that does."""
from __future__ import annotations
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .. import SERVER_NAME, paths

LEGACY_NAME = "memory"      # what installs before the distinct name registered the launcher as

# The one line slopymem offers for a harness's machine-wide instruction file: the TRIGGER — when to use memory. The
# full rules ride the MCP server's instructions (usage_rules.py); a harness may show those only after the server is
# first used, so this line must be visible before the agent decides whether to use memory. It names the server as
# registered in that harness (a user's own name when they chose one). Its prefix is the stable part: a later version
# of the line REPLACES an earlier one found by this prefix or by a legacy prefix, so a file never carries two.
TRIGGER_PREFIX = "Before planning or building, and whenever something earlier comes up, check the"
LEGACY_PREFIXES = ("If the memory tools show only",)      # the offer line of earlier installs
OUR_PREFIXES = (TRIGGER_PREFIX, *LEGACY_PREFIXES)


def trigger_line(name: str) -> str:
    return (f"{TRIGGER_PREFIX} `{name}` memory tools (retrieve with query_concepts) and weigh what comes back; save "
            "each decision with its why when it is made. Never save passwords, keys or tokens; save where they live.")


OFFER_LINE = trigger_line(SERVER_NAME)
OFFER_LINE_PREFIX = TRIGGER_PREFIX       # kept for callers that name it


def offer_line_state(f: Path, line: str) -> str:
    """'present' (this exact line is there), 'stale' (a line with the same prefix but other text), 'absent'."""
    if not f.exists():
        return "absent"
    for existing in f.read_text().splitlines():
        if existing.strip().startswith(OUR_PREFIXES):
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
            if existing.strip().startswith(OUR_PREFIXES):
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
        if existing.strip().startswith(OUR_PREFIXES):
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
    from .. import harness_memory as hm
    rows, bad = [], False
    lp = launcher_path()
    for h in detected():
        ok = h.registered(lp)
        bad |= not ok
        rows.append(f"{h.name}: {'registered' if ok else 'NOT registered'}")
    try:
        for k, v in hm.status().items():
            if v == "off (slopymemory)":
                rows.append(f"{k} file memory is OFF (slopymemory harness-memory); projects without a store have no memory there")
    except (hm.HarnessMemoryError, OSError) as e:
        rows.append(str(e))
    return Finding(not bad, "; ".join(rows) or "no known harness detected")


def _register_one(h: Harness, lp: str, yes: bool, confirm) -> tuple[int, bool]:
    """Register (or bring up to date) ONE harness. Returns (rc, changed) for this harness alone — the
    caller aggregates across however many harnesses it is handling and decides, on its own, whether and
    when to print the first-run summary."""
    if not h.detect():
        print(f"{h.name}: not detected on this machine — to register by hand: {h.config_hint}")
        return 0, False
    if h.registered(lp):
        failed, renamed = _rename_step(h, lp, yes, confirm)
        if failed:
            return 1, renamed
        # Already registered: the mcp add is skipped, but the offer line is still brought up to date — a
        # re-run of `slopymem register` is how a machine that registered under an older line gets the
        # current one. A current line asks nothing.
        print(f"{h.name}: already registered")
        if h.instructions_file:
            step_rc, step_changed = _offer_line_step(h, yes, confirm, quiet_when_present=True)
            return step_rc, renamed or step_changed
        return 0, renamed
    if h.register_cmd is None:
        print(f"{h.name}: register by hand: {h.config_hint}"); return 0, False
    cmd = h.register_cmd(lp)
    print(f"{h.name}: {' '.join(cmd)}")
    if not confirm("run it?", yes):
        return 1, False
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"{h.name}: registration failed: {e} — see SETUP.md#harnesses", file=sys.stderr)
        return 1, False
    if h.instructions_file:
        step_rc, step_changed = _offer_line_step(h, yes, confirm)     # an unwritable file is a failure here too, not only a message
        return step_rc, True
    return 0, True


def register(harness_id: str | None, yes: bool, print_summary: bool = True) -> int:
    """Register one harness (harness_id) or every KNOWN one (harness_id is None). Prints the first-run
    summary once at the end, only when something changed — unless print_summary is False (used by
    register_detected() so IT can print a single summary across every harness it handles, instead of
    one copy per harness)."""
    from ..cli import confirm
    lp = launcher_path()
    targets = [h for h in KNOWN if harness_id in (None, h.id)]
    if not targets:
        print(f"unknown harness {harness_id}; known: {[h.id for h in KNOWN]}", file=sys.stderr); return 1
    rc = 0
    changed = False
    for h in targets:
        step_rc, step_changed = _register_one(h, lp, yes, confirm)
        rc |= step_rc; changed |= step_changed
    if changed and print_summary:
        print("\n".join(summary()))
    return rc


def _rename_step(h: Harness, lp: str, yes: bool, confirm) -> tuple[bool, bool]:
    """An older install registered the launcher as LEGACY_NAME: offer to re-register it as SERVER_NAME. The new entry
    is added BEFORE the old one is removed, so a failure never leaves the launcher unregistered. Any other name was
    the user's choice and is kept. Returns (failed, changed): failed is True when a step failed (said on stderr);
    a declined rename changes nothing and is not a failure. changed is True once the new name is registered, even
    if removing the old one then failed."""
    old = h.registered_as(lp) if h.registered_as is not None else None
    if old != LEGACY_NAME or h.register_cmd is None or h.unregister_cmd is None:
        return False, False
    print(f"{h.name}: registered as {old!r}, a name other memory servers also use. Renaming it to {SERVER_NAME!r} "
          f"changes the tool names to mcp__{SERVER_NAME}__*; update any mcp__{old}__ permission allowlists.")
    if not confirm(f"rename {old!r} to {SERVER_NAME!r}?", yes):
        print(f"{h.name}: left registered as {old!r}")
        return False, False
    add, remove = h.register_cmd(lp), h.unregister_cmd(old)
    try:
        subprocess.run(add, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"{h.name}: adding {SERVER_NAME!r} failed: {e}; still registered as {old!r} — see SETUP.md#harnesses", file=sys.stderr)
        return True, False
    try:
        subprocess.run(remove, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"{h.name}: registered as {SERVER_NAME!r}, but removing {old!r} failed: {e}; both names now run the launcher — "
              f"remove the old one with: {' '.join(remove)} — see SETUP.md#harnesses", file=sys.stderr)
        return True, True
    print(f"{h.name}: renamed {old!r} to {SERVER_NAME!r}")
    return False, True


def _offer_line_step(h: Harness, yes: bool, confirm, quiet_when_present: bool = False) -> tuple[int, bool]:
    """Offer (and on a yes, apply) the instruction line for one harness, naming the server as it is actually
    registered there (a user's own name when they chose one; SERVER_NAME when the config isn't readable yet).
    Returns (rc, changed): rc is 1 when the user declined an update that was needed or the file cannot be read or
    written, else 0 — a declined append after a fresh registration was never a failure. changed is True only when
    the line was actually appended or replaced."""
    f = h.instructions_file()
    name = (h.registered_as(launcher_path()) if h.registered_as is not None else None) or SERVER_NAME
    line = trigger_line(name)
    try:
        state = offer_line_state(f, line)
        if state == "present":
            if not quiet_when_present:
                print("offer line already present")
            return 0, False
        verb = "update the offer line in" if state == "stale" else "append the offer line to"
        if not confirm(f"{verb} {f}?", yes):
            print(f"offer line left as it is in {f}"); return (1 if state == "stale" else 0), False
        done = ensure_offer_line(f, line)
    except OSError as e:
        print(f"{h.name}: the offer line could not be read or written in {f}: {e} — see SETUP.md#harnesses", file=sys.stderr)
        return 1, False
    print("offer line updated" if done == "replaced" else f"offer line appended to {f}")
    return 0, done in ("replaced", "appended")


def _harness_memory_line(state: str, name: str) -> str:
    """One true line for one harness's harness_memory.status() state, spelled exactly as status() returns it —
    never lumped into a generic bucket that would be false for a state it wasn't written for."""
    if state == "on":
        return (f"  Your harness's own memory ({name}): unchanged. slopymemory is checked first; your harness "
                 "memory stays as a fallback.")
    if state == "off (slopymemory)":
        return f"  Your harness's own file memory: OFF in {name}, switched off by slopymemory (slopymem harness-memory on to restore)"
    if state == "on (changed since slopymemory switched it off)":
        return (f"  Your harness's own file memory in {name}: switched back on since slopymemory turned it off "
                 "(slopymem harness-memory on clears the record)")
    if state == "off (not by slopymemory)":
        return f"  Your harness's own memory ({name}) is off by your own choice — there is no fallback memory there."
    if state.startswith("unknown (") and state.endswith(")"):
        return f"  Your harness's own memory ({name}): state unknown ({state[len('unknown ('):-1]})."
    return f"  Your harness's own memory ({name}): {state}"      # a future status() spelling — never dropped silently


def summary() -> list[str]:
    """The first-run block: what slopymemory changed on this machine, how to switch a harness's own file memory
    off or restore it, and how to undo everything — only lines that are true, and never a token figure. Never
    raises: an unreadable harness-memory state is said as a line, not an exception."""
    from .. import harness_memory as hm
    lp = launcher_path()
    det = detected()
    reg = [h for h in det if h.registered(lp)]
    lines = ["slopymemory is set up."]

    by_name: dict[str, list[str]] = {}
    for h in reg:
        name = (h.registered_as(lp) if h.registered_as is not None else None) or SERVER_NAME
        by_name.setdefault(name, []).append(h.name)
    for name in sorted(by_name):
        lines.append(f'  Registered as the MCP server "{name}" in: {", ".join(by_name[name])}')

    files = []
    for h in reg:
        if h.instructions_file is None:
            continue
        f = h.instructions_file()
        if offer_line_state(f, OFFER_LINE) != "absent":
            files.append(str(f))
    if files:
        lines.append(f"  Added one line to: {', '.join(files)}  (tells the agent when to use memory)")

    try:
        hm_status = hm.status()
    except (hm.HarnessMemoryError, OSError) as e:
        from ..cli import _hm_reason      # strips a HarnessMemoryError's own trailing anchor so it is never doubled
        hm_status = None
        lines.append(f"  Harness memory state unreadable: {_hm_reason(e)} — see SETUP.md#harness-memory")

    if hm_status is not None:
        # A harness earns a line only if it is actually here (detected) or slopymemory holds a record for it — a
        # record is true information even for a harness that's since been removed. Without either, hm.status()'s
        # entry (it always checks claude-code, whether or not Claude Code exists on this machine) would be a line
        # about a harness that was never there.
        detected_ids = {h.id for h in det}
        record_states = ("off (slopymemory)", "on (changed since slopymemory switched it off)")
        shown = {k: v for k, v in hm_status.items() if k in detected_ids or v in record_states}
        id_to_name = {h.id: h.name for h in KNOWN}
        for key, state in shown.items():
            lines.append(_harness_memory_line(state, id_to_name.get(key, key)))
        if any(v == "on" for v in shown.values()):
            lines.append("  To turn your harness's own file memory off (its index is re-sent on every request): "
                          "slopymem harness-memory off")

    lines.append("  To undo everything: slopymem uninstall  (your harness memory comes back exactly as it was)")
    lines.append("  A project gets memory the first time you accept the agent's offer, or with: slopymem init")
    return lines


def register_detected(yes: bool, print_summary: bool = True) -> int:
    """The installer's step 5: register the launcher in every harness found on this machine, and say when there
    is none — a machine with no known harness is not a failure, it is told how to register later. Prints the
    first-run summary at most ONCE, after every harness has been handled, and only if any of them changed —
    delegating the per-harness print to register() would print one stale copy per harness instead."""
    found = detected()
    if not found:
        print("no known harness detected (Claude Code, Codex, pi); register later with: slopymem register <harness>")
        return 0
    from ..cli import confirm
    lp = launcher_path()
    rc = 0
    changed = False
    for h in found:
        step_rc, step_changed = _register_one(h, lp, yes, confirm)
        rc |= step_rc; changed |= step_changed
    if changed and print_summary:
        print("\n".join(summary()))
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
