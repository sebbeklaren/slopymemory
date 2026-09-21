"""`init`: plan first (pure — what would be created, or why it is refused), then apply. The database
is created through a small Postgres protocol so tests never touch a real one; `SystemPostgres` uses
the local peer-authenticated tools (`createdb`, `psql`); `embedded_pg.EmbeddedPostgres` is the other
implementation. Which one a new store gets is `choose_backend`. No store server is started here."""
from __future__ import annotations
import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol
from . import paths
from .registry import Registry
from .store import BACKENDS, Store, all_stores, allocate_port, collisions

TEMPLATE_DB = "slopymem_template"
TEMPLATE_HINT = ("one-time, as a superuser: sudo -u postgres psql -c 'CREATE DATABASE slopymem_template' -c 'ALTER DATABASE slopymem_template IS_TEMPLATE true' && sudo -u postgres psql -d slopymem_template -c 'CREATE EXTENSION vector'. "
                 "The role that runs slopymem must be allowed to create databases: sudo -u postgres psql -c 'ALTER ROLE <user> CREATEDB;' — see SETUP.md#postgres")
EMBEDDED_HINT = ("the embedded Postgres needs the embedded-postgres wheel (PostgreSQL + pgvector) in the slopymemory venv — "
                 "re-run the install; `slopymem doctor` names what is missing — see SETUP.md#postgres")


class InitRefused(RuntimeError):
    pass


class Postgres(Protocol):
    def database_exists(self, name: str) -> bool: ...
    def create_database(self, name: str) -> None: ...
    def drop_database(self, name: str) -> None: ...
    def has_pgvector(self) -> bool: ...
    def can_provision(self) -> bool: ...
    def describe(self) -> str: ...


def ident(name: str) -> str:
    """Guard against unsafe database names in SQL interpolation — shared by both backends."""
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"unsafe database name {name!r}")
    return name


class SystemPostgres:
    def _ident(self, name: str) -> str:
        return ident(name)

    def _run(self, tool: str, args: list[str]) -> str:
        """Run one client tool; a failure carries the tool's OWN reason (its stderr) and the anchor,
        never `returned non-zero exit status N`."""
        try:
            return subprocess.run([tool, *args], capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError as e:
            raise InitRefused(f"{tool}: {(e.stderr or '').strip() or e} — see SETUP.md#postgres") from e
        except FileNotFoundError as e:
            raise InitRefused(f"{tool} not found — install the PostgreSQL client tools — see SETUP.md#postgres") from e

    def _psql(self, sql: str, db: str = "postgres") -> str:
        return self._run("psql", ["-d", db, "-Atc", sql]).strip()

    def database_exists(self, name: str) -> bool:
        name = self._ident(name)
        return self._psql(f"select 1 from pg_database where datname = '{name}'") == "1"

    def template_exists(self) -> bool:
        """Check if slopymem_template exists and is usable (either is_template=true or owned by current user)."""
        tpl = self._ident(TEMPLATE_DB)
        return self._psql(f"select 1 from pg_database d where d.datname = '{tpl}' and (d.datistemplate or d.datdba = (select oid from pg_roles where rolname = current_user))") == "1"

    def is_superuser(self) -> bool:
        return self._psql("select rolsuper from pg_roles where rolname = current_user") == "t"

    def can_create_databases(self) -> bool:
        """SUPERUSER or CREATEDB — what `createdb` itself needs, template or not."""
        return self._psql("select rolsuper or rolcreatedb from pg_roles where rolname = current_user") == "t"

    def create_database(self, name: str) -> None:
        name = self._ident(name)
        if self.template_exists():
            self._run("createdb", ["-T", TEMPLATE_DB, name])
        elif self.is_superuser():
            self._run("createdb", [name])
            self._psql("create extension if not exists vector", db=name)
        else:
            raise InitRefused(f"cannot create {name}: pgvector needs a superuser or the template database — {TEMPLATE_HINT}")

    def drop_database(self, name: str) -> None:
        name = self._ident(name)
        self._run("dropdb", [name])

    def database_size(self, name: str) -> int | None:
        """pg_database_size in bytes, or None when psql cannot answer — the caller says "unknown", never crashes."""
        name = self._ident(name)
        try:
            return int(self._psql(f"select pg_database_size('{name}')"))
        except (InitRefused, ValueError):
            return None

    def has_pgvector(self) -> bool:
        return self._psql("select 1 from pg_available_extensions where name = 'vector'") == "1"

    def can_provision(self) -> bool:
        return (self.has_pgvector() and (self.template_exists() or self.is_superuser())
                and self.can_create_databases())

    def describe(self) -> str:
        version = self._psql("show server_version")
        template = "yes" if self.template_exists() else "no"
        superuser = "yes" if self.is_superuser() else "no"
        createdb = "yes" if self.can_create_databases() else "no"
        return (f"system Postgres (peer auth) — {version} (template: {template}; superuser: {superuser}; "
                f"role can create databases: {createdb})")


class Choice(NamedTuple):
    backend: str            # "system" | "embedded"
    note: str | None        # why the system one was passed over when nothing was requested — said, never swallowed


def choose_backend(system: Postgres, embedded_available: bool, requested: str | None) -> Choice:
    """Which Postgres a new store goes on. Pure: it decides and explains, creates nothing. `requested` wins
    ("system" needs `system.can_provision()`, "embedded" needs the wheel), else: the embedded one when its data
    dir already exists (the user chose it once — a machine's stores stay on one backend), else the system one when
    it can provision, else the embedded one — with the system one's reason as the note, because a store landing
    on the other backend than the user expects must never happen without a word. If the data dir exists but the
    embedded backend is itself unavailable (a venv rebuilt without the wheel), landing on the system one carries
    THAT as the note instead — the same rule, the other direction. Neither → InitRefused naming both."""
    embedded_why = "" if embedded_available else f" ({EMBEDDED_HINT})"
    if requested == "system":
        if not system.can_provision():           # its own InitRefused (psql missing, server down) propagates as is
            raise InitRefused(f"the system Postgres cannot provision stores: {TEMPLATE_HINT}")
        return Choice("system", None)
    if requested == "embedded":
        if not embedded_available:
            raise InitRefused(f"the embedded Postgres is not available: {EMBEDDED_HINT}")
        return Choice("embedded", None)
    if requested is not None:
        raise InitRefused(f"unknown postgres backend {requested!r}: system | embedded — see SETUP.md#postgres")
    pgdir = paths.embedded_pg_dir()
    if embedded_available and pgdir.exists():
        return Choice("embedded", None)
    stranded_note = (f"the embedded data dir {pgdir} exists but the embedded Postgres is unavailable: {EMBEDDED_HINT}"
                     if pgdir.exists() and not embedded_available else None)
    try:
        if system.can_provision():
            return Choice("system", stranded_note)
        system_why = stranded_note or f"the system Postgres is not set up for slopymem — {TEMPLATE_HINT}"
    except InitRefused as e:
        system_why = stranded_note or f"the system Postgres is not usable here: {e}"
    if embedded_available:
        return Choice("embedded", system_why)
    raise InitRefused(f"no Postgres can provision stores: {system_why}; the embedded Postgres is not available{embedded_why}")


@dataclass
class Plan:
    store: Store
    path: Path
    creates_database: bool

    def describe(self) -> str:
        db = "create database" if self.creates_database else "use existing database"
        return (f"store {self.store.name!r} ({self.store.dialect}) for {self.path}\n"
                f"  {db} {self.store.database} on {self.store.postgres} Postgres; port {self.store.port}; state {self.store.path()}\n"
                f"  registry link {self.path} → {self.store.name}")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not s:
        raise InitRefused("a store name needs at least one letter or digit — see SETUP.md#registry")
    return s


def refuse_unsuitable_dir(path: Path, reg: Registry) -> None:
    """The directories no store may ever be linked to — by `init` or by `link`: the home directory and `/`
    (a link there is every unlinked directory's store, by longest prefix) and any ancestor of a linked path."""
    path = path.resolve()
    if path in (Path.home().resolve(), Path("/")):
        raise InitRefused(f"{path} is not a project directory — choose a project directory (a store linked here would become every project's store) — see SETUP.md#registry")
    for l in reg.links:
        if path in l.path.resolve().parents:
            raise InitRefused(f"{path} is an ancestor of {l.path} (store {l.store}) — choose a project directory — see SETUP.md#registry")


def plan_init(cwd: Path, name: str | None, dialect: str, pg: Postgres, backend: str = "system") -> Plan:
    """`pg` is the backend named by `backend` (see `choose_backend`); the store records which one it is on."""
    if backend not in BACKENDS:
        raise InitRefused(f"unknown postgres backend {backend!r}: system | embedded — see SETUP.md#postgres")
    cwd = cwd.resolve()
    reg = Registry.load()
    refuse_unsuitable_dir(cwd, reg)
    if (hit := reg.resolve(cwd)) is not None:
        raise InitRefused(f"{cwd} already resolves to {hit}; use `slopymem link`/`unlink` to change it — see SETUP.md#registry")
    if dialect not in ("coding", "design"):
        raise InitRefused(f"unknown dialect {dialect!r}: coding | design — see SETUP.md#registry")
    slug = _slug(name or cwd.name)
    others = all_stores()
    database = f"{slug}_memory"
    store = Store(name=slug, dialect=dialect, port=allocate_port({o.port for o in others}),
                  database=database, postgres=backend, created=dt.date.today())
    if (msgs := collisions(store, others)):
        raise InitRefused("; ".join(msgs) + " — see SETUP.md#registry")
    exists = pg.database_exists(database)
    if exists:
        raise InitRefused(f"database {database} exists but no store owns it — adopt it with `slopymem link` or pick another name — see SETUP.md#postgres")
    if not pg.can_provision():
        raise InitRefused(f"cannot provision stores on the {backend} Postgres: {TEMPLATE_HINT if backend == 'system' else EMBEDDED_HINT}")
    return Plan(store=store, path=cwd, creates_database=not exists)


def apply_init(plan: Plan, pg: Postgres) -> Store:
    if plan.creates_database:
        pg.create_database(plan.store.database)
    plan.store.save()
    (plan.store.path() / "multispace_projectors").mkdir(exist_ok=True)
    reg = Registry.load()
    reg.link(plan.path, plan.store.name)
    reg.save()
    return plan.store
