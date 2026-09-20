"""`init`: plan first (pure — what would be created, or why it is refused), then apply. The database
is created through a small Postgres protocol so tests never touch a real one; `SystemPostgres` uses
the local peer-authenticated tools (`createdb`, `psql`). No server is started here."""
from __future__ import annotations
import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from .registry import Registry
from .store import Store, all_stores, allocate_port, collisions

TEMPLATE_DB = "slopymem_template"
TEMPLATE_HINT = "one-time, as a superuser: sudo -u postgres psql -c 'CREATE DATABASE slopymem_template' -c 'GRANT ALL ON DATABASE slopymem_template TO <your role>' && sudo -u postgres psql -d slopymem_template -c 'CREATE EXTENSION vector' — see SETUP.md#postgres"


class InitRefused(RuntimeError):
    pass


class Postgres(Protocol):
    def database_exists(self, name: str) -> bool: ...
    def create_database(self, name: str) -> None: ...
    def has_pgvector(self) -> bool: ...
    def can_provision(self) -> bool: ...
    def describe(self) -> str: ...


class SystemPostgres:
    def _ident(self, name: str) -> str:
        """Guard against unsafe database names in SQL interpolation."""
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"unsafe database name {name!r}")
        return name

    def _psql(self, sql: str, db: str = "postgres") -> str:
        return subprocess.run(["psql", "-d", db, "-Atc", sql], capture_output=True, text=True, check=True).stdout.strip()

    def database_exists(self, name: str) -> bool:
        name = self._ident(name)
        return self._psql(f"select 1 from pg_database where datname = '{name}'") == "1"

    def template_exists(self) -> bool:
        self._ident(TEMPLATE_DB)
        return self._psql(f"select 1 from pg_database where datname = '{TEMPLATE_DB}'") == "1"

    def is_superuser(self) -> bool:
        return self._psql("select rolsuper from pg_roles where rolname = current_user") == "t"

    def create_database(self, name: str) -> None:
        name = self._ident(name)
        if self.template_exists():
            subprocess.run(["createdb", "-T", TEMPLATE_DB, name], check=True)
        elif self.is_superuser():
            subprocess.run(["createdb", name], check=True)
            self._psql("create extension if not exists vector", db=name)
        else:
            raise InitRefused(f"cannot create {name}: pgvector needs a superuser or the template database — {TEMPLATE_HINT}")

    def has_pgvector(self) -> bool:
        return self._psql("select 1 from pg_available_extensions where name = 'vector'") == "1"

    def can_provision(self) -> bool:
        return self.has_pgvector() and (self.template_exists() or self.is_superuser())

    def describe(self) -> str:
        version = self._psql("show server_version")
        template = "yes" if self.template_exists() else "no"
        superuser = "yes" if self.is_superuser() else "no"
        return f"system Postgres (peer auth) — {version} (template: {template}; superuser: {superuser})"


@dataclass
class Plan:
    store: Store
    path: Path
    creates_database: bool

    def describe(self) -> str:
        db = "create database" if self.creates_database else "use existing database"
        return (f"store {self.store.name!r} ({self.store.dialect}) for {self.path}\n"
                f"  {db} {self.store.database}; port {self.store.port}; state {self.store.path()}\n"
                f"  registry link {self.path} → {self.store.name}")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not s:
        raise InitRefused("a store name needs at least one letter or digit — see SETUP.md#registry")
    return s


def plan_init(cwd: Path, name: str | None, dialect: str, pg: Postgres) -> Plan:
    cwd = cwd.resolve()
    if cwd in (Path.home().resolve(), Path("/")):
        raise InitRefused(f"{cwd} is not a project directory — choose a project directory (a store linked here would become every project's store) — see SETUP.md#registry")
    reg = Registry.load()
    for l in reg.links:
        if cwd in l.path.resolve().parents:
            raise InitRefused(f"{cwd} is an ancestor of {l.path} (store {l.store}) — choose a project directory — see SETUP.md#registry")
    if (hit := reg.resolve(cwd)) is not None:
        raise InitRefused(f"{cwd} already resolves to {hit}; use `slopymem link`/`unlink` to change it — see SETUP.md#registry")
    if dialect not in ("coding", "design"):
        raise InitRefused(f"unknown dialect {dialect!r}: coding | design — see SETUP.md#registry")
    slug = _slug(name or cwd.name)
    others = all_stores()
    database = f"{slug}_memory"
    store = Store(name=slug, dialect=dialect, port=allocate_port({o.port for o in others}),
                  database=database, postgres="system", created=dt.date.today())
    if (msgs := collisions(store, others)):
        raise InitRefused("; ".join(msgs) + " — see SETUP.md#registry")
    exists = pg.database_exists(database)
    if exists:
        raise InitRefused(f"database {database} exists but no store owns it — adopt it with `slopymem link` or pick another name — see SETUP.md#postgres")
    if not pg.can_provision():
        raise InitRefused(f"cannot provision stores: {TEMPLATE_HINT}")
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
