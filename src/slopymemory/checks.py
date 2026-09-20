"""The one list of checks: `doctor` runs it, SETUP.md is generated from it. Each check names the
symptom an agent will be shown, what it verifies, the command that shows the same thing, the fix."""
from __future__ import annotations
import importlib.metadata as md
import os
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from . import paths, server as srv
from .registry import Registry
from .store import Store, all_stores

WARN_TOTAL_GB = 1.0
WARN_FREE_GB = 2.0
MODEL = "nomic-ai/nomic-embed-text-v1.5"


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
    try:
        return Finding(True, f"slopymemory {md.version('slopymemory')}; substrate agent-memory {md.version('agent-memory')}")
    except md.PackageNotFoundError as e:
        return Finding(False, f"not installed: {e}")


def _postgres() -> Finding:
    r = subprocess.run(["psql", "-d", "postgres", "-Atc", "select 1 from pg_available_extensions where name='vector'"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return Finding(False, f"psql failed: {r.stderr.strip()}")
    if r.stdout.strip() != "1":
        return Finding(False, "pgvector is not available")
    missing = []
    for st in all_stores():
        q = subprocess.run(["psql", "-d", "postgres", "-Atc", f"select 1 from pg_database where datname='{st.database}'"],
                           capture_output=True, text=True, check=False).stdout.strip()
        if q != "1":
            missing.append(f"{st.name}: database {st.database} missing")
    return Finding(not missing, "; ".join(missing) or "reachable, pgvector present, every store's database exists")


def _model() -> Finding:
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / ("models--" + MODEL.replace("/", "--"))
    return Finding(hub.exists(), f"{hub} {'present' if hub.exists() else 'missing — the first server start will download it'}")


def _registry() -> Finding:
    problems = []
    stores = all_stores()
    for k in ("port", "database"):
        for v, n in Counter(getattr(s, k) for s in stores).items():
            if n > 1:
                problems.append(f"{k} {v} shared by {n} stores")
    reg = Registry.load()
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
    for st in all_stores():
        up = srv.probe(st.port)
        pid = srv.pid_on_port(st.port)
        rows.append(f"{st.name}: port {st.port} {'up' if up else 'down'}" + (f" (pid {pid})" if pid else ""))
        if pid and not up:
            bad = True; rows[-1] += " — a process holds the port but does not answer HTTP"
    return Finding(not bad, "; ".join(rows) or "no stores")


def _harnesses() -> Finding:
    try:
        from .harnesses import status
    except ImportError:
        return Finding(True, "no harness table yet")
    return status()


def _space() -> Finding:
    root = paths.home()
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
    free = shutil.disk_usage(root if root.exists() else Path.home()).free
    warn = []
    if total > WARN_TOTAL_GB * 2**30:
        warn.append(f"{root} holds {total / 2**30:.1f} GB (> {WARN_TOTAL_GB} GB)")
    if free < WARN_FREE_GB * 2**30:
        warn.append(f"only {free / 2**30:.1f} GB free")
    return Finding(not warn, "; ".join(warn) or f"{total / 2**20:.0f} MB under {root}; {free / 2**30:.0f} GB free")


def _logs() -> Finding:
    lines = []
    for st in all_stores():
        f = st.log_file()
        if f.exists():
            errs = [l for l in f.read_text(errors="ignore").splitlines() if "ERROR" in l or "Traceback" in l]
            if errs:
                lines.append(f"{st.name}: {errs[-1][:160]}")
    return Finding(True, "; ".join(lines) or "no error lines in any server log")


def _local_paths() -> Finding:
    import slopymemory
    root = Path(slopymemory.__file__).parent
    hits = [str(p) for p in root.rglob("*.py") if "/home/" in p.read_text(errors="ignore") and "checks.py" not in p.name]
    return Finding(not hits, "; ".join(hits) or "no machine-local path in the installed package")


CHECKS: list[Check] = [
    Check("python", "the command or the launcher fails to start", "Python 3.13+ and the venv interpreter exist",
          "ls ~/.slopymemory/venv/bin/python; python3 --version", "re-run install.sh (Plan B) or the dev install script", _python),
    Check("package", "import errors on start", "slopymemory and the substrate are installed in the venv",
          "~/.slopymemory/venv/bin/python -c 'import slopymemory, agent_memory'", "re-run the install", _package),
    Check("postgres", "init fails or a server dies on start", "Postgres reachable, pgvector available, one database per store",
          "psql -d postgres -Atc \"select 1 from pg_available_extensions where name='vector'\"", "install pgvector / create the missing database with `slopymem init` or adopt with `link`", _postgres),
    Check("model", "the first server start is slow or fails offline", "the embedder is in the Hugging Face cache",
          "ls ~/.cache/huggingface/hub | grep nomic", "start any store once while online", _model),
    Check("registry", "a directory resolves to the wrong store, or two stores collide", "registry parses; every linked store exists; no two stores share a port or database",
          "cat ~/.slopymemory/registry.toml; slopymem list", "slopymem unlink / link; fix the port in store.toml", _registry),
    Check("servers", "tools hang or the launcher reports 'no server answering'", "each store's server answers HTTP on its port",
          "slopymem list; ss -ltnp | grep 87", "slopymem start <store>; read ~/.slopymemory/logs/<store>.log", _servers),
    Check("harnesses", "the agent has no memory tools", "each detected harness has slopymem-mcp registered at user scope",
          "claude mcp list; codex mcp list", "slopymem register", _harnesses),
    Check("space", "disk is filling", "state under ~/.slopymemory and free space on its filesystem",
          "du -sh ~/.slopymemory; df -h ~", "slopymem list shows per-store sizes; remove a store you no longer want", _space),
    Check("logs", "something failed and nobody knows what", "the last error line of each server log",
          "tail -50 ~/.slopymemory/logs/<store>.log", "read the line; the error names its anchor", _logs),
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
        print(f"{'ok  ' if f.ok else 'FAIL'} {c.id:<12} {f.detail}" + ("" if f.ok else f"  — see SETUP.md#{c.id}"))
        bad += not f.ok
    print("doctor: all checks passed" if not bad else f"doctor: {bad} check(s) failed")
    return 0 if not bad else 1


def render_setup(head: str) -> str:
    parts = [head.rstrip() + "\n\n## Checks (generated from `slopymemory.checks` — do not edit below this line)\n"]
    for c in CHECKS:
        parts.append(f"\n## {c.id}\n\n**Symptom:** {c.symptom}\n\n**Verifies:** {c.verify}\n\n"
                     f"**See for yourself:**\n\n```bash\n{c.command}\n```\n\n**Fix:** {c.fix}\n")
    return "".join(parts)
