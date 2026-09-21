"""`slopymem`: one command for humans and agents. Every subcommand says what it will change and asks
(unless --yes); every failure ends with a SETUP.md anchor. The `memory_init` tool is `init` — one
implementation, the CLI is the reference."""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path
import psycopg
from . import embedded_pg, paths, server as srv
from .checks import run_all
from .embedded_pg import EmbeddedPostgres
from .harnesses import register
from .provision import InitRefused, SystemPostgres, apply_init, choose_backend, plan_init, refuse_unsuitable_dir
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


def cmd_register(a) -> int:
    return register(a.harness, a.yes)


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
    s = sub.add_parser("register"); s.add_argument("harness", nargs="?"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_register)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build().parse_args(argv)
    try:
        return a.fn(a)
    except ConfigError as e:            # a registry/store file that does not read: the message, not a traceback
        return fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
