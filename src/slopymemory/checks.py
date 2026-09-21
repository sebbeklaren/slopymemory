"""The one list of checks: `doctor` runs it, SETUP.md is generated from it. Each check names the
symptom an agent will be shown, what it verifies, the command that shows the same thing, the fix."""
from __future__ import annotations
import datetime as dt
import importlib.metadata as md
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from . import embedded_pg, install_steps, paths, server as srv
from .embedded_pg import EmbeddedPostgres
from .harnesses import status as harness_status
from .provision import InitRefused, SystemPostgres, TEMPLATE_HINT
from .paths import ConfigError
from .registry import Registry
from .store import Store, all_stores, measure, store_problems

WARN_TOTAL_GB = 1.0
WARN_FREE_GB = 2.0


@dataclass
class Finding:
    ok: bool
    detail: str


@dataclass
class Check:
    id: str
    symptom: str          # what the user or agent sees
    verify: str           # what the check verifies, in prose
    command: str          # the shell command that shows the same thing
    fix: str
    run: Callable[[], Finding]


def _python() -> Finding:
    py = paths.venv_python()
    if sys.version_info < (3, 13):
        return Finding(False, f"python {sys.version.split()[0]} < 3.13")
    if not py.exists():
        return Finding(False, f"venv python missing: {py}")
    return Finding(True, f"{py} ({sys.version.split()[0]})")


def _package() -> Finding:
    """The substrate is vendored inside the slopymemory distribution (src/agent_memory) — there is no separate
    `agent-memory` package to look up; what matters is that both import from this venv."""
    try:
        version = md.version("slopymemory")
    except md.PackageNotFoundError as e:
        return Finding(False, f"not installed: {e}")
    try:
        import agent_memory
    except ImportError as e:
        return Finding(False, f"slopymemory {version} but the vendored substrate does not import: {e}")
    return Finding(True, f"slopymemory {version}; substrate agent_memory {Path(agent_memory.__file__).parent}")


def _postgres() -> Finding:
    """Both backends, each when it matters: the embedded Postgres when its data dir exists or a store is on it; the
    system one when a store is on it, or when nothing else exists yet (a fresh machine: which one will `init` get?).
    A store whose backend cannot be reached is a FAIL naming the store. The doctor is read-only: an embedded cluster
    that is down is reported as down, never started to look inside."""
    stores = all_stores()
    on_embedded = [s for s in stores if s.postgres == "embedded"]
    on_system = [s for s in stores if s.postgres == "system"]
    pgdir = paths.embedded_pg_dir()
    parts, failures = [], []
    if pgdir.exists() or on_embedded:
        parts.append(_embedded_part(pgdir, on_embedded, failures))
    if on_system or not (pgdir.exists() or on_embedded):
        parts.append(_system_part(on_system, failures, fresh=not stores))
    if failures:
        detail = "; ".join(failures)              # a psql refusal already carries the anchor: not a second one
        return Finding(False, detail if "SETUP.md#postgres" in detail else detail + " — see SETUP.md#postgres")
    return Finding(True, "; ".join(parts))


def _embedded_part(pgdir: Path, on_it: list[Store], failures: list[str]) -> str:
    n = f"{len(on_it)} store(s)"
    if (reason := embedded_pg.unavailable_reason()) is not None:
        if on_it:
            failures.append(f"{', '.join(s.name for s in on_it)}: on the embedded Postgres, but {reason}")
        return f"embedded: unavailable — {reason}"
    if not pgdir.exists():                        # only reachable with a store on it
        failures.append(f"{', '.join(s.name for s in on_it)}: on the embedded Postgres, but its data dir {pgdir} does not exist")
        return f"embedded: no data dir at {pgdir}"
    epg = EmbeddedPostgres(pgdir)
    size = f"data {epg.data_size() / 2**20:.0f} MB"
    if not epg.is_running():
        return f"embedded: not running (starts with a store's server); pgvector available; {size}; {n}, databases not verified while down"
    try:
        if not epg.has_pgvector():
            failures.append("embedded Postgres is running without pgvector")
        for st in on_it:
            if not epg.database_exists(st.database):
                failures.append(f"{st.name}: database {st.database} missing on the embedded Postgres")
    except InitRefused as e:
        failures.append(f"embedded Postgres: {e}")
    return f"embedded: running; pgvector present; {size}; {n}"


def _system_part(on_it: list[Store], failures: list[str], fresh: bool) -> str:
    """The system Postgres as before. Its failure is a FAIL when a store depends on it, or when the machine has
    nothing else to provision with; otherwise it is said, and new stores go to the embedded one."""
    names = ", ".join(s.name for s in on_it)
    try:
        pg = SystemPostgres()
        if not pg.has_pgvector():
            raise InitRefused("pgvector is not available on this server")
        for st in on_it:
            if not pg.database_exists(st.database):
                failures.append(f"{st.name}: database {st.database} missing on the system Postgres")
        tpl, su, cdb, can = pg.template_exists(), pg.is_superuser(), pg.can_create_databases(), pg.can_provision()
        detail = (f"system: reachable; pgvector present; template {'yes' if tpl else 'no'}; superuser {'yes' if su else 'no'}; "
                  f"role can create databases: {'yes' if cdb else 'no'}")
        if not can:
            detail += (f"; NEW stores cannot be created on it yet — {TEMPLATE_HINT}"
                       + ("; new stores use the embedded Postgres" if embedded_pg.available() else ""))
        return detail
    except InitRefused as e:            # carries the tool's own stderr and the anchor
        why = str(e)
    except Exception as e:
        why = f"psql failed: {e} — is Postgres running and psql on PATH?"
    if on_it:
        failures.append(f"{names}: system Postgres: {why}")
    elif fresh and embedded_pg.available():
        return f"system: {why}; new stores use the embedded Postgres"
    elif fresh:
        failures.append(f"no Postgres can provision stores: system: {why}; embedded: {embedded_pg.unavailable_reason()}")
    return f"system: {why}"


def _model() -> Finding:
    """The PINNED snapshot must be COMPLETE in the cache (huggingface_hub's own test, not a directory listing) — every
    stored coordinate was embedded with it — and the model's code repository at ITS pin beside it, or the first
    offline start fails. Another snapshot of the same model is not the model the stores were built with: loading it
    would shift every coordinate, so that is a FAIL naming the migration, not a warning."""
    try:
        from agent_memory.config import settings
    except ImportError as e:
        return Finding(False, f"the vendored substrate does not import, so the pin cannot be read: {e} — see SETUP.md#package")
    model, pin, code_pin = settings.embed_model, settings.embed_revision, settings.embed_code_revision
    where = install_steps.model_cache_dir(model)
    fetch = "run `slopymem install-model`"
    local = install_steps.model_cached(pin, model)
    if local is None:
        present = install_steps.cached_revisions(model)
        if not present:
            return Finding(False, f"{model} is not in {where} — {fetch} (once, about 0.5 GB); a server started offline fails without it")
        if pin in present:
            return Finding(False, f"{where}/snapshots/{pin[:12]}… exists but the snapshot is incomplete (an interrupted download?) — {fetch}")
        others = ", ".join(r[:12] for r in present)
        return Finding(False, f"{where} holds revision(s) {others} but not the pinned {pin[:12]} the stores' coordinates were embedded with. "
                              f"Changing the model or its revision is a migration (every store re-embedded), not a config edit: "
                              f"{fetch} to fetch the pinned snapshot, or set AM_EMBED_REVISION only as part of that migration")
    repos = install_steps.remote_code_repos(Path(local))
    missing = [r for r in repos if install_steps.model_cached(code_pin, r, allow_patterns=["*.py"]) is None]
    if missing:
        return Finding(False, f"the model's code ({', '.join(missing)}) at its pinned revision {code_pin[:12]} is not in the cache — {fetch}")
    code = f"; code {', '.join(repos)} at {code_pin[:12]}" if repos else ""
    return Finding(True, f"{model} at revision {pin[:12]} in {where}{code}")


def _registry() -> Finding:
    problems = store_problems()          # store.toml files that do not read, each with its reason
    stores = all_stores()
    for k in ("port", "database"):
        for v, n in Counter(getattr(s, k) for s in stores).items():
            if n > 1:
                problems.append(f"{k} {v} shared by {n} stores")
    try:
        reg = Registry.load()
    except ConfigError as e:
        problems.append(str(e)); reg = Registry()
    for l in reg.links:
        if not Store.exists(l.store):
            problems.append(f"{l.path} links to missing store {l.store}")
    for st in stores:
        if not st.path().exists():
            problems.append(f"{st.name}: state dir missing")
    return Finding(not problems, "; ".join(problems) or f"{len(stores)} store(s), {len(reg.links)} link(s), no collisions")


def _servers() -> Finding:
    rows = []
    bad = False
    try:
        for st in all_stores():
            up = srv.probe(st.port)
            pid = srv.pid_on_port(st.port)
            rows.append(f"{st.name}: port {st.port} {'up' if up else 'down'}" + (f" (pid {pid})" if pid else ""))
            if pid and not up:
                bad = True; rows[-1] += " — a process holds the port but does not answer HTTP"
    except RuntimeError as e:          # pid_on_port: ss not found (the message names iproute2 and the anchor)
        return Finding(False, str(e))
    except OSError as e:
        return Finding(False, f"ss failed: {e} — install iproute2")
    return Finding(not bad, "; ".join(rows) or "no stores")


def _harnesses() -> Finding:
    return harness_status()


def _space() -> Finding:
    root = paths.home()
    # Store data = the state dirs, the logs, an embedded Postgres, and the files each store's env names
    # (adopted stores keep theirs elsewhere). The venv is measured separately: it is the install, not data.
    data_paths = [paths.stores_dir(), paths.logs_dir(), paths.embedded_pg_dir()]
    for st in all_stores():
        data_paths += st.state_paths()
    data_total = measure(data_paths)
    venv_total = measure([root / "venv"])
    free = shutil.disk_usage(root if root.exists() else Path.home()).free
    warn = []
    if data_total > WARN_TOTAL_GB * 2**30:
        warn.append(f"store data is {data_total / 2**20:.0f} MB (> {WARN_TOTAL_GB * 1024:.0f} MB)")
    if free < WARN_FREE_GB * 2**30:
        warn.append(f"only {free / 2**30:.1f} GB free")
    detail = "; ".join(warn) or f"{data_total / 2**20:.0f} MB of store data; install (venv) {venv_total / 2**20:.0f} MB; {free / 2**30:.0f} GB free"
    return Finding(not warn, detail)


LOG_TAIL_BYTES = 64 * 1024


def _logs() -> Finding:
    """Error lines are REPORTED, not failed (a server that logged an error and recovered is not a broken
    machine); a log that cannot be read IS a failure — the check would otherwise read as 'all clean'."""
    lines, unreadable = [], False
    for st in all_stores():
        f = st.log_file()
        if not f.exists():
            continue
        try:
            with open(f, "rb") as log:                # only the tail: a log can be huge
                size = log.seek(0, 2)
                log.seek(max(0, size - LOG_TAIL_BYTES))
                tail = log.read().decode(errors="ignore")
        except OSError as e:
            lines.append(f"{st.name}: log unreadable: {e}"); unreadable = True
            continue
        errs = [l for l in tail.splitlines() if "ERROR" in l or "Traceback" in l]
        if errs:
            lines.append(f"{st.name}: {len(errs)} error line(s) in the last {LOG_TAIL_BYTES // 1024} KB; last: {errs[-1][:160]}")
    return Finding(not unreadable, "; ".join(lines) or "no error lines in any server log")


def _when(t) -> str:
    try:
        return dt.datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def _secrets() -> Finding:
    """Saves the server REFUSED as secrets, per store, from its invocation log: the count, the kinds and when.
    Informational (ok stays True) — the guard doing its job is not a broken machine — but a log that cannot be
    read IS a failure, as for `logs`: the check would otherwise read as 'nothing refused'. The texts were never
    logged, so there is nothing here to repeat."""
    rows, unreadable = [], False
    for st in all_stores():
        f = Path(st.server_env()["AM_MCP_INVOCATION_LOG"])
        if not f.exists():
            continue
        first, refused, kinds = None, [], Counter()
        try:
            with open(f, errors="ignore") as log:
                for line in log:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue                    # a torn line: not a record, not a failure of the check
                    if not isinstance(r, dict):
                        continue
                    if first is None:
                        first = r.get("t")
                    if r.get("refused") == "secret":
                        refused.append(r.get("t")); kinds[str(r.get("kind"))] += 1
        except OSError as e:
            rows.append(f"{st.name}: invocation log unreadable: {e}"); unreadable = True
            continue
        if refused:
            kind_list = ", ".join(f"{k} x{n}" for k, n in sorted(kinds.items()))
            rows.append(f"{st.name}: {len(refused)} save(s) refused as secrets since {_when(first)} "
                        f"(first {_when(refused[0])}, last {_when(refused[-1])}; {kind_list})")
    return Finding(not unreadable, "; ".join(rows) or "no save refused as a secret in any store's invocation log")


def _local_paths() -> Finding:
    import slopymemory
    root = Path(slopymemory.__file__).parent
    hits = [str(p) for p in root.rglob("*.py") if "/home/" in p.read_text(errors="ignore") and "checks.py" not in p.name]
    return Finding(not hits, "; ".join(hits) or "no machine-local path in the installed package")


CHECKS: list[Check] = [
    Check("python", "the command or the launcher fails to start", "Python 3.13+ and the venv interpreter exist",
          "ls ~/.slopymemory/venv/bin/python; python3 --version", "re-run the installer or the dev install script", _python),
    Check("package", "import errors on start", "slopymemory and the substrate are installed in the venv",
          "~/.slopymemory/venv/bin/python -c 'import slopymemory, agent_memory'", "re-run the install", _package),
    Check("postgres", "init fails or a server dies on start",
          "each backend in use: the embedded Postgres under ~/.slopymemory/pg (running or not — the doctor never starts it; pgvector; data size) when its data dir exists or a store is on it; "
          "the system Postgres (reachable, pgvector, template/superuser/CREATEDB) when a store is on it or nothing exists yet; one database per store on its own backend. "
          "A store whose backend is unreachable fails, by name. `slopymem init --postgres system|embedded` picks; the default is embedded when ~/.slopymemory/pg exists, else system if it can provision, else embedded",
          "slopymem list; ls ~/.slopymemory/pg; tail ~/.slopymemory/pg/log; psql -d postgres -Atc \"select 1 from pg_available_extensions where name='vector'\"",
          "embedded: read ~/.slopymemory/pg/log, re-run the install if the wheel is missing; to stop it manually "
          "(no `slopymem stop` verb for it yet): "
          "~/.slopymemory/venv/lib/python3.13/site-packages/embedded_postgres/pginstall/bin/pg_ctl -D ~/.slopymemory/pg -m fast stop; "
          "system: install pgvector / the template; a missing database: `slopymem init` or adopt with `link`", _postgres),
    Check("model", "the first server start is slow or fails offline, or every retrieval comes back subtly wrong",
          "the embedder (nomic-embed-text-v1.5) is COMPLETE in the Hugging Face cache AT THE PINNED REVISION the stores' coordinates were embedded with "
          "(AM_EMBED_REVISION), and its code repository (nomic-bert-2048, which trust_remote_code loads) at ITS pin (AM_EMBED_CODE_REVISION); "
          "`slopymem install-model` fetches exactly those. Another snapshot of the same model fails: changing the model is a migration (re-embed every store), not a config edit",
          "ls ~/.cache/huggingface/hub/models--nomic-ai--nomic-embed-text-v1.5/snapshots ~/.cache/huggingface/hub/models--nomic-ai--nomic-bert-2048/snapshots",
          "slopymem install-model (once, about 0.5 GB, needs the network)", _model),
    Check("registry", "a directory resolves to the wrong store, or two stores collide", "registry parses; every linked store exists; no two stores share a port or database",
          "cat ~/.slopymemory/registry.toml; slopymem list", "slopymem unlink / link; fix the port in store.toml", _registry),
    Check("servers", "tools hang or the launcher reports 'no server answering'", "each store's server answers HTTP on its port",
          "slopymem list; ss -ltnp | grep 87", "slopymem start <store>; read ~/.slopymemory/logs/<store>.log", _servers),
    Check("harnesses", "the agent has no memory tools", "each detected harness has slopymem-mcp registered at user scope",
          "claude mcp list; codex mcp list", "slopymem register", _harnesses),
    Check("space", "disk is filling", "store data (state dirs, logs, and the files each store's env names — adopted stores keep theirs elsewhere) and free disk space under ~/.slopymemory; the venv is measured separately",
          "du -sh ~/.slopymemory; df -h ~", "slopymem list shows per-store sizes; remove a store you no longer want", _space),
    Check("logs", "something failed and nobody knows what", "the last error line of each server log (tail of the last 64 KB); error lines are reported, not failed; an unreadable log fails",
          "tail -50 ~/.slopymemory/logs/<store>.log", "read the line; the error names its anchor", _logs),
    Check("secrets", "an agent tried to save a key or password",
          "refused saves per store from the invocation logs (count, kinds, first/last time; the texts were never logged); "
          "existing stores can be scanned with `slopymem scan-store <name>`, which reports and never deletes",
          "slopymem scan-store <name>", "remove the memory by hand; tell the agent to save where the secret lives", _secrets),
    Check("local-paths", "a leak of the author's machine into the package", "no installed file contains a machine-local path",
          "grep -r '/home/' ~/.slopymemory/venv/lib/python3.13/site-packages/slopymemory", "report it — the export scanner missed it", _local_paths),
]


def by_id(i: str) -> Check:
    return next(c for c in CHECKS if c.id == i)


def run_all() -> int:
    bad = 0
    for c in CHECKS:
        try:
            f = c.run()
        except Exception as e:      # a check that crashes is a failed check with the exception as its detail
            f = Finding(False, f"check crashed: {e!r}")
        anchored = f.ok or "SETUP.md#" in f.detail       # a detail that already names its anchor is not given a second
        print(f"{'ok  ' if f.ok else 'FAIL'} {c.id:<12} {f.detail}" + ("" if anchored else f"  — see SETUP.md#{c.id}"))
        bad += not f.ok
    print("doctor: all checks passed" if not bad else f"doctor: {bad} check(s) failed")
    return 0 if not bad else 1


def render_setup(head: str) -> str:
    parts = [head.rstrip() + "\n\n## Checks (generated from `slopymemory.checks` — do not edit below this line)\n"]
    for c in CHECKS:
        parts.append(f"\n## {c.id}\n\n**Symptom:** {c.symptom}\n\n**Verifies:** {c.verify}\n\n"
                     f"**See for yourself:**\n\n```bash\n{c.command}\n```\n\n**Fix:** {c.fix}\n")
    return "".join(parts)
