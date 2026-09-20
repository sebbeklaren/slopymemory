"""The embedded Postgres: the `embedded-postgres` wheel ships PostgreSQL 18 with pgvector as binaries; this
module runs ONE cluster from them under `paths.embedded_pg_dir()` for a machine without a system Postgres.

- Started on demand (`ensure_running`, before a store server whose `postgres = "embedded"`) with `pg_ctl`, which
  daemonizes the postmaster: it outlives the launcher and the session that started it, exactly as the store
  servers do. Stopped only on request (`stop`).
- No TCP port, ever: the server listens on a unix socket IN the data directory (which initdb makes 0700, so only
  the owner reaches it) — nothing to collide with a system Postgres. The socket file name still carries a port
  number, so it is pinned (a PGPORT in the environment would otherwise rename the socket under us).
- The cluster's superuser is `postgres` with trust authentication on that socket; the DSN a store's server gets
  says so (`uri`).
- The wheel's Python layer (`get_server`) is NOT used: it picks the socket directory itself (the runtime dir when
  the data path is long) and its command wrappers drop the tools' stderr. The binaries are driven directly, and
  every failure is an `InitRefused` carrying the tool's own reason and the anchor, as with `SystemPostgres`.
"""
from __future__ import annotations
import fcntl
import shlex
import subprocess
from pathlib import Path
import psycopg
from .provision import InitRefused, ident
from .store import measure

try:
    from embedded_postgres._commands import POSTGRES_BIN_PATH as BIN      # the wheel's own record of where its binaries are
    IMPORT_ERROR: ImportError | None = None
except ImportError as e:                # a venv built without the wheel: everything else keeps working; `available()` says why
    BIN = None
    IMPORT_ERROR = e

ROLE = "postgres"
PORT = 5432                             # names the socket file (.s.PGSQL.5432); no TCP listener is opened
SOCKET_PATH_MAX = 107                   # sun_path on Linux (macOS allows 104; postgres refuses beyond its own limit anyway)
START_TIMEOUT_S = 60                    # pg_ctl -w waits this long for "ready"; the subprocess gets a margin on top
_TOOLS = ("initdb", "pg_ctl", "postgres")


def uri_prefix(pgdata: Path) -> str:
    """Everything but the database name — a store's DATABASE_URL on this cluster starts with it."""
    return f"host={pgdata} port={PORT} user={ROLE} dbname="


def uri(pgdata: Path, database: str) -> str:
    """The libpq conninfo (keyword form, which psycopg takes as is) for one database on the embedded cluster."""
    return uri_prefix(pgdata) + database


def unavailable_reason() -> str | None:
    """None when the wheel can serve: its binaries and its pgvector are there. Otherwise, what is missing."""
    if BIN is None:
        return f"the embedded-postgres wheel is not importable in this venv ({IMPORT_ERROR}) — re-run the install"
    missing = [t for t in _TOOLS if not (BIN / t).exists()]
    if missing:
        return f"the embedded-postgres wheel lacks {', '.join(missing)} under {BIN} — re-run the install"
    if not _vector_control().exists():
        return f"the embedded-postgres wheel ships no pgvector ({_vector_control()} missing) — re-run the install"
    return None


def available() -> bool:
    return unavailable_reason() is None


def _vector_control() -> Path:
    return BIN.parent / "share" / "extension" / "vector.control"


class EmbeddedPostgres:
    """The `provision.Postgres` protocol over the embedded cluster at `pgdata`, plus its lifecycle."""

    def __init__(self, pgdata: Path) -> None:
        self.pgdata = Path(pgdata)

    def socket_file(self) -> Path:
        return self.pgdata / f".s.PGSQL.{PORT}"

    def log_file(self) -> Path:
        return self.pgdata / "log"

    def uri(self, database: str = "postgres") -> str:
        return uri(self.pgdata, database)

    # -- lifecycle ------------------------------------------------------------------------------------------

    def _run(self, tool: str, args: list[str], timeout: float) -> str:
        """Run one of the wheel's binaries; a failure carries the tool's OWN reason (its stderr) and the anchor,
        never `returned non-zero exit status N`."""
        if (reason := unavailable_reason()) is not None:
            raise InitRefused(f"{reason} — see SETUP.md#postgres")
        try:
            return subprocess.run([str(BIN / tool), *args], capture_output=True, text=True, check=True,
                                  stdin=subprocess.DEVNULL, timeout=timeout).stdout
        except subprocess.CalledProcessError as e:
            reason = (e.stderr or "").strip() or (e.stdout or "").strip() or str(e)
            raise InitRefused(f"{tool}: {reason} — log: {self.log_file()} — see SETUP.md#postgres") from e
        except subprocess.TimeoutExpired as e:
            raise InitRefused(f"{tool} did not finish within {timeout:g} s — log: {self.log_file()} — see SETUP.md#postgres") from e

    def is_running(self) -> bool:
        """Does the cluster answer on its socket? The product test, as `server.probe` is for a store server: a
        stale postmaster.pid after a crash or a reboot reads as down, which it is."""
        if not self.socket_file().exists():
            return False
        try:
            with psycopg.connect(self.uri(), connect_timeout=5):
                return True
        except psycopg.OperationalError:
            return False

    def _refuse_long_socket_path(self) -> None:
        n = len(str(self.socket_file()).encode())
        if n > SOCKET_PATH_MAX:
            raise InitRefused(f"the embedded Postgres socket path {self.socket_file()} is {n} bytes; unix sockets allow "
                              f"{SOCKET_PATH_MAX} — set SLOPYMEM_HOME to a shorter path — see SETUP.md#postgres")

    def _initdb(self) -> None:
        # trust auth: only this socket, in a 0700 dir. UTF-8 with the builtin C.UTF-8 locale: the same on every
        # machine, independent of the environment's LANG. English server messages: the log is read by agents.
        self._run("initdb", ["-D", str(self.pgdata), "-U", ROLE, "--auth=trust", "--encoding=UTF8",
                             "--locale-provider=builtin", "--builtin-locale=C.UTF-8", "--lc-messages=C",
                             "--no-instructions"], timeout=120)

    def _start(self) -> None:
        # -h "": no TCP listener; -k: the socket in the data dir; -p: pins the socket's name; -l: the server's
        # own output goes to the log, so pg_ctl's stdout closes when it exits (without -l it would hang us).
        options = f'-h "" -k {shlex.quote(str(self.pgdata))} -p {PORT}'
        self._run("pg_ctl", ["-D", str(self.pgdata), "-w", "-t", str(START_TIMEOUT_S), "-o", options,
                             "-l", str(self.log_file()), "start"], timeout=START_TIMEOUT_S + 30)

    def ensure_running(self) -> None:
        """Start the cluster if it is not answering — initdb first when the data dir is not a cluster yet. Two
        callers (two launchers starting two embedded stores) serialize on a lock beside the data dir; the second
        finds the cluster up and returns."""
        if self.is_running():
            return
        if (reason := unavailable_reason()) is not None:                   # before anything is created on disk
            raise InitRefused(f"{reason} — see SETUP.md#postgres")
        self._refuse_long_socket_path()
        lock = self.pgdata.with_name(self.pgdata.name + ".lock")          # beside, not inside: initdb wants an empty dir
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                if self.is_running():
                    return
                if not (self.pgdata / "PG_VERSION").exists():
                    self._initdb()
                self._start()
                if not self.is_running():
                    raise InitRefused(f"the embedded Postgres started but does not answer on {self.socket_file()} — "
                                      f"log: {self.log_file()} — see SETUP.md#postgres")
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def stop(self, wait_s: float = 30.0) -> bool:
        """Clean shutdown (fast mode: open connections are closed). False when nothing was running."""
        if not self.is_running():
            return False
        self._run("pg_ctl", ["-D", str(self.pgdata), "-w", "-t", str(int(wait_s)), "-m", "fast", "stop"], timeout=wait_s + 30)
        return True

    # -- the Postgres protocol --------------------------------------------------------------------------------

    def _connect(self, database: str = "postgres") -> psycopg.Connection:
        """A connection to the cluster, starting it when it is down: every protocol method is about to act."""
        self.ensure_running()
        try:
            return psycopg.connect(self.uri(database), autocommit=True, connect_timeout=10)
        except psycopg.OperationalError as e:
            raise InitRefused(f"embedded Postgres: {str(e).strip()} — log: {self.log_file()} — see SETUP.md#postgres") from e

    def _exec(self, sql: str, database: str = "postgres", fetch: bool = False) -> tuple | None:
        try:
            with self._connect(database) as c:
                cur = c.execute(sql)
                return cur.fetchone() if fetch else None
        except psycopg.Error as e:
            raise InitRefused(f"embedded Postgres: {str(e).strip()} — log: {self.log_file()} — see SETUP.md#postgres") from e

    def _one(self, sql: str, database: str = "postgres") -> tuple | None:
        return self._exec(sql, database, fetch=True)

    def database_exists(self, name: str) -> bool:
        name = ident(name)
        return self._one(f"select 1 from pg_database where datname = '{name}'") == (1,)

    def create_database(self, name: str) -> None:
        name = ident(name)
        self._exec(f"create database {name}")
        self._exec("create extension if not exists vector", database=name)

    def drop_database(self, name: str) -> None:
        name = ident(name)
        self._exec(f"drop database {name}")

    def database_size(self, name: str) -> int | None:
        """pg_database_size in bytes; None when the cluster is down (the caller says so — a size is never worth a
        start) or cannot answer."""
        name = ident(name)
        if not self.is_running():
            return None
        try:
            row = self._one(f"select pg_database_size('{name}')")
            return int(row[0]) if row else None
        except (InitRefused, ValueError, TypeError):
            return None

    def has_pgvector(self) -> bool:
        """Asked of the server when it is up; when it is down, the wheel's own extension files are the answer
        (`pg_available_extensions` reads exactly those) — the doctor never starts the cluster to find out."""
        if self.is_running():
            return self._one("select 1 from pg_available_extensions where name = 'vector'") == (1,)
        return unavailable_reason() is None

    def can_provision(self) -> bool:
        """Static: the wheel's binaries and its pgvector. Starting the cluster is `create_database`'s job, where a
        failure is named at the point of doing."""
        return available()

    def data_size(self) -> int:
        return measure([self.pgdata])

    def describe(self) -> str:
        if (reason := unavailable_reason()) is not None:
            return f"embedded Postgres: unavailable — {reason}"
        version = subprocess.run([str(BIN / "postgres"), "--version"], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL).stdout.strip() or "PostgreSQL (version unknown)"
        version = version.replace("postgres (PostgreSQL)", "PostgreSQL")
        if self.is_running():
            row = self._one("select default_version from pg_available_extensions where name = 'vector'")
            state = f"running; pgvector {row[0] if row else 'MISSING'}"
        else:
            state = "not running (starts with the first embedded store's server); pgvector available"
        return f"embedded {version} under {self.pgdata} — {state}; data {self.data_size() / 2**20:.0f} MB"
