"""`slopymem`: one command for humans and agents. Every subcommand says what it will change and asks
(unless --yes); every failure ends with a SETUP.md anchor. The `memory_init` tool is `init` — one
implementation, the CLI is the reference."""
from __future__ import annotations
import argparse
import os
import shutil
import sys
from pathlib import Path
import psycopg
from . import embedded_pg, harness_memory as hm, install_steps, paths, server as srv
from .checks import run_all
from .embedded_pg import EmbeddedPostgres
from . import harnesses
from .harnesses import register, register_detected
from .provision import InitRefused, SystemPostgres, TEMPLATE_HINT, apply_init, choose_backend, plan_init, refuse_unsuitable_dir
from .paths import ConfigError
from .registry import Registry
from .store import Store, all_stores, store_problems, valid_name


def postgres(backend: str = "system"):
    """The Postgres a store is on (`store.postgres`), or the one `init` chose."""
    return EmbeddedPostgres(paths.embedded_pg_dir()) if backend == "embedded" else SystemPostgres()


def confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    print(f"{prompt} [y/N] ", end="", flush=True)
    return sys.stdin.readline().strip().lower() == "y"


def fail(msg: str, code: int = 1) -> int:
    print(f"slopymem: {msg}", file=sys.stderr)
    return code


def _hm_reason(e: Exception) -> str:
    """The text of a harness_memory read failure, its own trailing anchor stripped — a caller that wraps
    it in a longer message adds the anchor itself, once."""
    s = str(e)
    return s[: -len(hm.ANCHOR)] if s.endswith(hm.ANCHOR) else s


def cmd_init(a) -> int:
    try:
        backend, note = choose_backend(postgres("system"), embedded_pg.available(), a.postgres)
        plan = plan_init(Path.cwd(), a.name, a.dialect, postgres(backend), backend=backend)
    except InitRefused as e:
        return fail(str(e))
    if note:
        print(f"note: {note}")
    print(plan.describe())
    if not confirm("create it?", a.yes):
        return fail("nothing created")
    try:
        st = apply_init(plan, postgres(backend))
    except InitRefused as e:
        return fail(str(e))
    print(f"created store {st.name}; its server starts on first use")
    return 0


def _bad_name(name: str) -> int | None:
    if not valid_name(name):
        return fail(f"{name!r} is not a valid store name (lowercase letters, digits, underscore) — `slopymem list` — see SETUP.md#registry")
    return None


def cmd_link(a) -> int:
    if (rc := _bad_name(a.store)) is not None:
        return rc
    if not Store.exists(a.store):
        return fail(f"no store named {a.store} — `slopymem list` — see SETUP.md#registry")
    path = Path(a.path or Path.cwd()).resolve()
    if not path.is_dir():
        return fail(f"{path} is not an existing directory — see SETUP.md#registry")
    reg = Registry.load()
    try:
        refuse_unsuitable_dir(path, reg)        # the same refusals as init: home, /, an ancestor of a link
    except InitRefused as e:
        return fail(str(e))
    print(f"link {path} → store {a.store}")
    if not confirm("link it?", a.yes):
        return fail("nothing linked")
    try:
        reg.link(path, a.store)
    except ValueError as e:
        return fail(f"{e} — see SETUP.md#registry")
    reg.save(); print("linked"); return 0


def cmd_unlink(a) -> int:
    path = Path(a.path or Path.cwd()).resolve()
    reg = Registry.load()
    hit = reg.resolve(path)
    if hit is None or not any(l.path.resolve() == path for l in reg.links):
        return fail(f"{path} is not linked (it resolves to {hit or 'nothing'}) — see SETUP.md#registry")
    print(f"unlink {path} (store {hit} is kept)")
    if not confirm("unlink it?", a.yes):
        return fail("nothing unlinked")
    reg.unlink(path); reg.save(); print("unlinked"); return 0


def cmd_list(a) -> int:
    try:
        reg = Registry.load()
    except ConfigError as e:                        # list what can be listed; the problem is shown, not fatal
        print(f"?? {e}"); reg = Registry()
    stores = all_stores()
    problems = store_problems()
    if not stores and not problems:
        print("no stores — `slopymem init` in a project directory"); return 0
    for st in stores:
        up = "up" if srv.probe(st.port) else "down"
        pg = postgres(st.postgres)
        size = pg.database_size(st.database)
        if size is not None:
            db = f"{size // 1024} KB"
        elif st.postgres == "embedded" and not pg.is_running():     # idle, not broken: list never starts it
            db = "unknown (embedded Postgres not running)"
        else:
            db = "unknown — see SETUP.md#postgres"
        print(f"{st.name}  {st.dialect}  port {st.port}  {up}  db {st.database}  db size {db}  state {st.state_size() // 1024} KB")
        for p in reg.paths_of(st.name):
            print(f"    {p}")
    for problem in problems:
        print(f"?? {problem}")
    return 0


def cmd_start(a) -> int:
    if (rc := _bad_name(a.store)) is not None:
        return rc
    if not Store.exists(a.store):
        return fail(f"no store named {a.store} — `slopymem list` — see SETUP.md#registry")
    st = Store.load(a.store)
    try:
        srv.ensure_up(st)
    except srv.ServerNotUp as e:
        return fail(str(e))
    print(f"{st.name} up on port {st.port}"); return 0


def cmd_stop(a) -> int:
    if a.store is None and not a.all:
        return fail("stop needs a store name or --all — see SETUP.md#servers")
    if a.store is not None and a.all:
        return fail("give a store name OR --all, not both — see SETUP.md#servers")
    if a.store is not None and (rc := _bad_name(a.store)) is not None:
        return rc
    if a.store is not None and not Store.exists(a.store):
        return fail(f"no store named {a.store} — see SETUP.md#registry")
    targets = all_stores() if a.all else [Store.load(a.store)]
    rc = 0
    for st in targets:
        try:
            print(f"{st.name}: {'stopped' if srv.stop(st) else 'was not running'}")
        except RuntimeError as e:          # ss missing, or the port held by something that is not ours
            rc = fail(str(e))
    return rc


def cmd_remove(a) -> int:
    if a.yes:
        return fail("remove refuses --yes: the memories are the user's", code=2)
    if (rc := _bad_name(a.store)) is not None:
        return rc
    if not Store.exists(a.store):
        return fail(f"no store named {a.store} — see SETUP.md#registry")
    st = Store.load(a.store)
    print(f"REMOVE store {st.name}: drops database {st.database} and deletes {st.path()}; unlinks {Registry.load().paths_of(st.name)}")
    if not confirm("are you sure?", False):
        return fail("nothing removed")
    print(f"type the store name ({st.name}) to confirm: ", end="", flush=True)
    if sys.stdin.readline().strip() != st.name:
        return fail("name did not match; nothing removed")
    try:
        srv.stop(st)
    except RuntimeError as e:
        return fail(str(e))
    pg = postgres(st.postgres)
    try:
        exists = pg.database_exists(st.database)
    except InitRefused as e:
        return fail(str(e))
    if exists:
        try:
            pg.drop_database(st.database)
        except Exception as e:
            return fail(str(e))
    errors = []
    try:
        shutil.rmtree(st.path(), onexc=lambda fn, p, e: errors.append(f"{p}: {e}"))
    except Exception as e:
        errors.append(f"rmtree: {e}")
    reg = Registry.load(); reg.links = [l for l in reg.links if l.store != st.name]; reg.save()
    if errors:
        return fail("state directory not fully removed: " + "; ".join(errors) + " — see SETUP.md#registry")
    print(f"removed {st.name}"); return 0


def cmd_doctor(a) -> int:
    return run_all()


def read_memories(st: Store) -> list[tuple]:
    """Every memory of a store as (memory_id, created_at, text), read over the DSN the store's own server connects
    with (an adopted store names its own in [env]) — one SELECT, nothing else can be issued through this path."""
    with psycopg.connect(st.server_env()["DATABASE_URL"], connect_timeout=10) as conn:
        conn.read_only = True                     # the session refuses a write too: "never deletes" is enforced, not promised
        with conn.cursor() as cur:
            cur.execute("SELECT memory_id, created_at, text FROM m3_memory ORDER BY created_at")
            return cur.fetchall()


def cmd_scan_store(a) -> int:
    """A REPORT of the memories that look like they carry a secret — the same test `memory_save` refuses new
    saves with, run over what is already stored. Shows memory_id, when, and the kind; never the text (the point
    is not to repeat it), and never deletes: the memories are the user's."""
    if (rc := _bad_name(a.store)) is not None:
        return rc
    if not Store.exists(a.store):
        return fail(f"no store named {a.store} — `slopymem list` — see SETUP.md#registry")
    st = Store.load(a.store)
    try:
        from agent_memory.guard import find_secret     # the vendored substrate's guard: one test for saves and scans
    except ImportError as e:
        return fail(f"the vendored substrate does not import: {e} — see SETUP.md#package")
    try:
        rows = read_memories(st)
    except psycopg.Error as e:
        why = str(e).strip()
        if st.postgres == "embedded" and not postgres("embedded").is_running():
            why = f"the embedded Postgres is not running (it starts with the store's server: `slopymem start {st.name}`)"
        return fail(f"{st.name}: cannot read database {st.database}: {why} — see SETUP.md#postgres")
    hits = 0
    for memory_id, created, text in rows:
        s = find_secret(text)
        if s is None:
            continue
        hits += 1
        when = created.strftime("%Y-%m-%d %H:%M") if created is not None else "?"
        print(f"{memory_id}  {when}  {s.kind}")
    print(f"{hits} of {len(rows)} memories look like they carry a secret — review and remove by hand: "
          f"slopymem shows, never deletes — see SETUP.md#secrets")
    return 0


def cmd_harness_memory(a) -> int:
    if a.action == "status":
        try:
            st = hm.status()
        except hm.HarnessMemoryError as e:
            return fail(str(e))
        except OSError as e:
            return fail(f"{e}{hm.ANCHOR}")
        for k, v in st.items():
            print(f"{k}: {v}")
        return 0
    targets = [a.harness] if a.harness else [h.id for h in harnesses.detected() if h.id in ("claude-code", "codex")]
    if not targets:
        print("no harness with a switch detected (Claude Code, Codex)")
        return 0
    if a.action == "off":
        print("This switches the harness's OWN file memory off in EVERY project on this machine (user settings), "
              "not only where slopymemory has a store. A project without a store then has no memory at all in that "
              "harness until you set one up, and when slopymemory is unavailable there is no fallback memory. "
              "The memory files are kept; `slopymem harness-memory on` brings it back.")
        if not confirm(f"switch off: {', '.join(targets)}?", a.yes):
            return fail("nothing changed")
    rc = 0
    for t in targets:
        try:
            if a.action == "off":
                print(hm.turn_off(t))
            else:
                print(hm.turn_on(t, choose=_ask_restore(t)))
        except hm.HarnessMemoryError as e:
            # A bare (no --harness) `on` covers every detected harness with a switch; one of them
            # never having been switched off by slopymemory is information, not a failure. An
            # explicit --harness still refuses: the user asked about exactly that one.
            if a.action == "on" and a.harness is None and "no record" in str(e):
                print(f"{t}: nothing to restore")
            else:
                rc = fail(str(e))
        except OSError as e:
            rc = fail(f"{t}: {e}{hm.ANCHOR}")
    return rc


def _ask_restore(harness_id: str):
    """The conflict handler passed to `turn_on`, bound to the harness it is asking about — during
    uninstall or a bare `on` covering several harnesses, "it changed" alone would not say which one.
    Asked when the value changed after slopymemory set it; --yes never answers this."""
    def ask(prior: str, current: str) -> str:
        print(f"{harness_id}: it changed after slopymemory set it: before slopymemory it was {prior}, now it is {current}")
        print("restore the earlier value (r) or keep the current one (k)? ", end="", flush=True)
        return "restore" if sys.stdin.readline().strip().lower() == "r" else "keep"
    return ask


def cmd_register(a) -> int:
    if a.detected:
        if a.harness is not None:
            return fail("give a harness name OR --detected, not both — see SETUP.md#harnesses")
        return register_detected(a.yes, print_summary=not a.no_summary)
    return register(a.harness, a.yes, print_summary=not a.no_summary)


def cmd_summary(a) -> int:
    print("\n".join(harnesses.summary()))
    return 0


# --- the installer's verbs: each prints, asks unless --yes, is idempotent, names SETUP.md#<id> on failure ---------

def cmd_install_postgres(a) -> int:
    """Which Postgres new stores go on, verified: a scratch database is created, pgvector checked IN it, and it is
    dropped. `--postgres` names one; otherwise the system Postgres is probed and offered, else the embedded one
    (whose data dir this creates — from then on `init` defaults to it). A cluster that was down is left down."""
    system = postgres("system")
    embedded_ok = embedded_pg.available()
    requested = a.postgres
    if requested is None:
        pgdir = paths.embedded_pg_dir()
        if pgdir.exists() and embedded_ok:
            print(f"the embedded Postgres is already set up under {pgdir}; new stores go on it")
            requested = "embedded"
        else:
            try:
                can, why = system.can_provision(), None
            except InitRefused as e:
                can, why = False, str(e)
            if can:
                print(f"found: {system.describe()}")
                requested = "system" if confirm("use the system Postgres for new stores? (no → the embedded one)", a.yes) else "embedded"
            else:
                print(f"the system Postgres cannot provision stores here: {why or TEMPLATE_HINT}")
                print("new stores go on the embedded Postgres (PostgreSQL 18 + pgvector from the embedded-postgres wheel, "
                      f"under {pgdir}, unix socket only)")
                requested = "embedded"
    try:
        choice = choose_backend(system, embedded_ok, requested)
    except InitRefused as e:
        return fail(str(e))
    pg = postgres(choice.backend)
    scratch = f"slopymem_install_check_{os.getpid()}"
    print(f"verify the {choice.backend} Postgres with a scratch database {scratch}: create it, check pgvector installs in it, drop it")
    if not confirm("go on?", a.yes):
        return fail("nothing verified")
    was_running = pg.is_running() if choice.backend == "embedded" else True
    vector, failure = None, None
    try:
        pg.create_database(scratch)
        vector = pg.vector_installed(scratch)
    except InitRefused as e:
        failure = str(e)
    except Exception as e:
        failure = f"{choice.backend} Postgres: the scratch database check failed: {e} — see SETUP.md#postgres"
    finally:                                        # on every path: no scratch database left behind, the cluster as it was
        try:
            if pg.database_exists(scratch):
                pg.drop_database(scratch)
        except Exception as e:
            failure = (failure + "; " if failure else "") + f"the scratch database {scratch} was not dropped: {e} — see SETUP.md#postgres"
        if choice.backend == "embedded" and not was_running:
            try:
                pg.stop()                           # the check started it; a store's server starts it again when needed
            except Exception as e:                  # said beside the check's own failure, never a traceback over it
                failure = (failure + "; " if failure else "") + f"the embedded Postgres was not stopped after the check: {e}"
    if failure:
        return fail(failure)
    if not vector:
        return fail(f"{choice.backend} Postgres: the scratch database was created but pgvector did not install in it — see SETUP.md#postgres")
    print(f"{choice.backend} Postgres verified: {pg.describe()}; new stores go on it")
    return 0


def cmd_install_model(a) -> int:
    """The embedder at its pinned revision into the shared Hugging Face cache — once. Cached: nothing asked."""
    from agent_memory.config import settings          # the substrate's own model name and pins: one source of truth
    model, pin, code_pin = settings.embed_model, settings.embed_revision, settings.embed_code_revision
    if install_steps.weights_complete(model, pin) is not None:
        print(f"{model} at revision {pin[:12]} is already in the Hugging Face cache; checking its code")
    # download_model asks once, with the real size in the question, right before the weights are fetched; the code the
    # config names is checked (and fetched when a file is missing) whether or not the weights were cached
    def ask(note: str) -> bool:
        print(f"download {model} at revision {pin[:12]} — {note}")
        return confirm("go on?", a.yes)
    try:
        local = install_steps.download_model(pin, model, code_revision=code_pin, ask=ask)
    except Exception as e:
        return fail(f"the model download failed: {e} — see SETUP.md#model")
    if local is None:
        return fail("nothing downloaded; no server can start without it — see SETUP.md#model")
    print(f"model ready: {local}")
    return 0


def cmd_uninstall(a) -> int:
    """Everything the install put on the machine, listed first, then asked about twice: the venv, the launcher's
    registrations (each harness's own remove command, only where it IS registered), the offer line (only where
    present), the logs and the registry. The stores and the embedded Postgres are KEPT unless --data, which drops
    every store's database through its own backend and deletes stores/ and pg/ — after a third question that lists
    the store names and is never answered by --yes. --yes without --data is refused: the data left behind must be
    seen."""
    if a.yes and not a.data:
        return fail("uninstall refuses --yes without --data: the stores stay behind and you must see that; run it "
                    "without --yes, or add --data to remove the stores too (asked a third time, by name)", code=2)
    home, venv, logs, reg = paths.home(), paths.home() / "venv", paths.logs_dir(), paths.registry_file()
    stores_dir, pgdir = paths.stores_dir(), paths.embedded_pg_dir()
    stores = all_stores()
    lp = harnesses.launcher_path()
    to_unregister = [h for h in harnesses.detected() if h.registered(lp)]
    offer_files = [(h, h.instructions_file()) for h in harnesses.KNOWN if h.instructions_file is not None
                   and harnesses.offer_line_state(h.instructions_file(), h.offer_line) != "absent"]
    # harness-memory state can be unreadable for reasons that have nothing to do with slopymemory (a
    # malformed ~/.claude/settings.json the user broke by hand, a permissions problem) — that must never
    # block uninstall for a user who may never have touched harness-memory. It is said and left alone.
    hm_unreadable = None
    try:
        hm_status = hm.status()
    except (hm.HarnessMemoryError, OSError) as e:
        hm_status = {}
        hm_unreadable = _hm_reason(e)
    to_restore = [t for t, v in hm_status.items()
                  if v in ("off (slopymemory)", "on (changed since slopymemory switched it off)")]
    # a record can exist while the harness's CURRENT state can't be read (codex broken right now) — hm.status()
    # then reports "unknown (...)" for it, which is neither restored (not in to_restore) nor said anywhere else;
    # a record must never outlive the install silently, so it earns its own preview line.
    codex_kept = (not hm_unreadable and hm_status.get("codex", "").startswith("unknown (")
                  and hm.has_record("codex"))
    names = ", ".join(st.name for st in stores) or "none"
    if not home.exists() and not to_unregister and not offer_files and not to_restore and not hm_unreadable and not codex_kept:
        print(f"nothing to uninstall: {home} does not exist and no harness has the launcher registered"); return 0
    print("uninstall will remove:")
    if hm_unreadable:
        print(f"  harness memory state unreadable: {hm_unreadable} — see SETUP.md#harness-memory; left as it is")
    elif to_restore:
        print("  harness memory switched off by slopymemory: restored first")
    if codex_kept:
        print(f"  Codex: harness memory record kept — state {hm_status['codex']}; "
              f"restore by hand: codex features enable memories")
    if pgdir.exists():                      # its binaries live in the venv: a cluster left running would outlive them
        print("  (first: stop the embedded Postgres if it is running — its data is kept" + ("" if a.data else " unless --data") + ")")
    print(f"  the venv {venv}" + ("" if venv.exists() else " (not there)"))
    print("  harness registrations: " + (", ".join(h.name for h in to_unregister) or "none"))
    print("  the offer line in: " + (", ".join(str(f) for _, f in offer_files) or "none present"))
    print(f"  {logs} and {reg}")
    if a.data:
        print(f"  --data: the databases of {len(stores)} store(s) ({names}), then {stores_dir} and {pgdir}")
    else:
        print(f"  KEPT: {stores_dir} ({len(stores)} store(s): {names}) and {pgdir} — add --data to remove them too")
    if not confirm("remove these?", a.yes):
        return fail("nothing removed")
    if not confirm("really? this cannot be undone", a.yes):
        return fail("nothing removed")
    rc = 0
    restored = []
    for t in to_restore:                    # a record must never outlive the install silently: restored FIRST
        try:
            print(hm.turn_on(t, choose=_ask_restore(t)))
            restored.append(t)
        except hm.HarnessMemoryError as e:
            rc = fail(str(e))
    if a.data:
        print(f"the stores whose databases will be DROPPED: {names}")
        print("type y to drop them (--yes does not answer this one): ", end="", flush=True)
        if sys.stdin.readline().strip().lower() != "y":
            if restored:
                return fail(f"harness memory ({', '.join(restored)}) was already restored above; nothing else removed")
            return fail("nothing removed")
    for st in stores:                       # the servers first, in both modes: the venv they run from is going
        try:
            srv.stop(st)
        except RuntimeError as e:
            rc = fail(str(e))
    if a.data:
        for st in stores:
            pg = postgres(st.postgres)
            try:
                if pg.database_exists(st.database):
                    pg.drop_database(st.database)
                    print(f"dropped database {st.database} ({st.postgres} Postgres)")
            except Exception as e:              # InitRefused carries its anchor; anything else is said as it is
                rc = fail(f"{st.name}: database {st.database} not dropped: {e}")
    if pgdir.exists():                      # in both modes, before the venv: pg_ctl and the postmaster binary live there
        try:
            print("embedded Postgres stopped" if postgres("embedded").stop() else "embedded Postgres was not running")
        except Exception as e:
            # The uninstall ENDS here: pg_ctl and the postmaster binary live in the venv, and removing it under a
            # postmaster that did not stop leaves a cluster nobody can stop cleanly. Nothing after this point is
            # removed (the registrations, the logs, the registry, the venv); what was already done above is printed.
            pg_ctl = (embedded_pg.BIN / "pg_ctl") if embedded_pg.BIN else venv / "lib" / "python3.13" / "site-packages" / "embedded_postgres" / "pginstall" / "bin" / "pg_ctl"
            return fail(f"the embedded Postgres did not stop: {e}\n"
                        f"nothing more was removed — the venv is KEPT, pg_ctl lives in it. Stop the cluster by hand:\n"
                        f"  {pg_ctl} -D {pgdir} -m fast stop\n"
                        f"(or, if pg_ctl cannot: kill -TERM $(head -1 {pgdir / 'postmaster.pid'})), then run `slopymem uninstall` again")
    if a.data:
        rc |= _rmtree(stores_dir) | _rmtree(pgdir) | _rmtree(pgdir.with_name(pgdir.name + ".lock"))
    for h in to_unregister:
        rc |= harnesses.unregister(h, lp)
    for h, f in offer_files:
        try:
            if harnesses.remove_offer_line(f):
                print(f"offer line removed from {f}")
        except OSError as e:
            rc = fail(f"{h.name}: the offer line could not be removed from {f}: {e} — see SETUP.md#harnesses")
    rc |= _rmtree(logs)
    if reg.exists():
        try:
            reg.unlink()
        except OSError as e:
            rc = fail(f"{reg}: {e}")
    rc |= _rmtree(venv)                     # last: this very process runs from it
    try:
        if home.exists() and not any(home.iterdir()):
            home.rmdir(); print(f"removed {home}")
        elif home.exists():
            print(f"left {home} with: {', '.join(sorted(p.name for p in home.iterdir()))}")
    except OSError as e:
        rc = fail(f"{home}: {e}")
    print("uninstalled" + (" with errors above" if rc else ""))
    return rc


def _rmtree(path: Path) -> int:
    """Remove a tree, 0 or 1 with every reason said; a path that is not there is nothing to do."""
    if not path.exists() and not path.is_symlink():
        return 0
    errors = []
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except OSError as e:
            errors.append(f"{path}: {e}")
    else:
        shutil.rmtree(path, onexc=lambda fn, p, e: errors.append(f"{p}: {e}"))
    if errors:
        return fail("not fully removed: " + "; ".join(errors))
    print(f"removed {path}")
    return 0


def build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slopymem")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("name", nargs="?"); s.add_argument("--dialect", default="coding", choices=["coding", "design"])
    s.add_argument("--postgres", choices=["system", "embedded"], help="default: embedded if ~/.slopymemory/pg exists, else system if it can provision, else embedded")
    s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("link"); s.add_argument("store"); s.add_argument("--path"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_link)
    s = sub.add_parser("unlink"); s.add_argument("--path"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_unlink)
    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("start"); s.add_argument("store"); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("stop"); s.add_argument("store", nargs="?"); s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("remove"); s.add_argument("store"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("scan-store", help="report memories that look like secrets (never deletes)"); s.add_argument("store"); s.set_defaults(fn=cmd_scan_store)
    s = sub.add_parser("register"); s.add_argument("harness", nargs="?"); s.add_argument("--detected", action="store_true", help="every harness found on this machine")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--no-summary", action="store_true", help="suppress the first-run summary this prints when something changed")
    s.set_defaults(fn=cmd_register)
    s = sub.add_parser("summary", help="what slopymemory changed on this machine (read-only)"); s.set_defaults(fn=cmd_summary)
    s = sub.add_parser("harness-memory", help="switch a harness's own file memory off or back on (asked; reversible)")
    s.add_argument("action", choices=["off", "on", "status"])
    s.add_argument("--harness", choices=["claude-code", "codex"], help="default: every detected harness with a switch")
    s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_harness_memory)
    s = sub.add_parser("install-postgres", help="choose and verify the Postgres new stores go on (the installer's step 3)")
    s.add_argument("--postgres", choices=["system", "embedded"]); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_install_postgres)
    s = sub.add_parser("install-model", help="the embedder at its pinned revision into the Hugging Face cache, once (step 4)")
    s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_install_model)
    s = sub.add_parser("uninstall", help="remove the install; the stores are kept unless --data")
    s.add_argument("--yes", action="store_true"); s.add_argument("--data", action="store_true", help="also drop every store's database and delete stores/ and pg/")
    s.set_defaults(fn=cmd_uninstall)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build().parse_args(argv)
    try:
        return a.fn(a)
    except ConfigError as e:            # a registry/store file that does not read: the message, not a traceback
        return fail(str(e))
    except hm.HarnessMemoryError as e:  # a harness-memory read/write failure a command did not already catch itself
        return fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
