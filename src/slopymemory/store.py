"""A store = one database + one state directory under `paths.stores_dir()/<name>/` + `store.toml`.
The server's environment is DERIVED from the store (the per-instance paths that keep two stores from
ever sharing a file), with an explicit `[env]` table for adopted stores whose files live elsewhere."""
from __future__ import annotations
import datetime as dt
import re
import socket
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal
import tomli_w
from . import paths
from .paths import ConfigError

Dialect = Literal["coding", "design"]

# coding = the substrate's function + semantic dialect (concept-primary retrieval, no facets); design = the
# substrate's defaults (the five spaces with facets come from the facet-projector dir in the state directory).
DIALECT_ENV: dict[str, dict[str, str]] = {
    "coding": {"AM_M3_RETRIEVAL_MODE": "concept_primary", "AM_M3_WORD_DEPTH_STEEPNESS": "4.0",
               "AM_M3_BOOTSTRAP_N": "12"},
    "design": {},
}
PORT_RANGE = range(8780, 8900)
NAME_RE = re.compile(r"[a-z0-9_]+")     # what `provision._slug` produces; a name is also a directory under stores/


def valid_name(name: str) -> bool:
    return NAME_RE.fullmatch(name) is not None


@dataclass
class Store:
    name: str
    dialect: str
    port: int
    database: str
    postgres: str                       # "system" | "embedded"
    env: dict[str, str] = field(default_factory=dict)
    created: dt.date = field(default_factory=dt.date.today)

    @staticmethod
    def dir_of(name: str) -> Path:
        return paths.stores_dir() / name

    def path(self) -> Path:
        return self.dir_of(self.name)

    def toml_file(self) -> Path:
        return self.path() / "store.toml"

    def lock_file(self) -> Path:
        return self.path() / "start.lock"

    def log_file(self) -> Path:
        return paths.logs_dir() / f"{self.name}.log"

    @classmethod
    def exists(cls, name: str) -> bool:
        return (cls.dir_of(name) / "store.toml").exists()

    @classmethod
    def load(cls, name: str) -> "Store":
        f = cls.dir_of(name) / "store.toml"
        try:
            d = tomllib.loads(f.read_text())
            st = cls(name=d["name"], dialect=d["dialect"], port=int(d["port"]), database=d["database"],
                     postgres=d["postgres"], env=dict(d.get("env", {})), created=d["created"])
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{f}: not valid TOML: {e} — see SETUP.md#registry") from e
        except KeyError as e:
            raise ConfigError(f"{f}: missing the key {e} — see SETUP.md#registry") from e
        except (TypeError, ValueError, AttributeError) as e:
            raise ConfigError(f"{f}: {e} — see SETUP.md#registry") from e
        except OSError as e:
            raise ConfigError(f"{f}: unreadable: {e} — see SETUP.md#registry") from e
        if st.dialect not in DIALECT_ENV:
            raise ConfigError(f"{f}: unknown dialect {st.dialect!r}: coding | design — see SETUP.md#registry")
        return st

    def save(self) -> None:
        paths.write_atomic(self.toml_file(), tomli_w.dumps(asdict(self)))

    STATE_ENV_KEYS = ("AM_M3_BUFFER_PATH", "AM_M3_PROJECTOR_PATH", "AM_M3_FACET_PROJECTOR_DIR", "AM_MCP_INVOCATION_LOG")

    def state_paths(self) -> list[Path]:
        """The state dir and every file or dir the server's env names — an adopted store's live elsewhere."""
        env = self.server_env()
        return [self.path()] + [Path(env[k]) for k in self.STATE_ENV_KEYS]

    def state_size(self) -> int:
        return measure(self.state_paths())

    def server_env(self) -> dict[str, str]:
        d = str(self.path())
        env = {
            "AM_MCP_HOST": "127.0.0.1", "AM_MCP_PORT": str(self.port),
            "DATABASE_URL": f"postgresql:///{self.database}",
            "AM_M3_BUFFER_PATH": f"{d}/m3_buffer.jsonl",
            "AM_M3_PROJECTOR_PATH": f"{d}/projector.pkl",
            "AM_M3_FACET_PROJECTOR_DIR": f"{d}/multispace_projectors",
            "AM_MCP_INVOCATION_LOG": f"{d}/mcp_invocations.jsonl",
        }
        env.update(DIALECT_ENV[self.dialect])
        env.update(self.env)            # adopted stores: the observed values win
        return env


def measure(paths: list[Path]) -> int:
    """Bytes in the files under `paths`, each file counted once (a dir and a file inside it may both be named)."""
    seen: dict[Path, int] = {}
    for p in paths:
        if p.is_file():
            seen[p.resolve()] = p.stat().st_size
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    seen[f.resolve()] = f.stat().st_size
    return sum(seen.values())


def _bound(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def allocate_port(taken: set[int]) -> int:
    for p in PORT_RANGE:
        if p not in taken and not _bound(p):
            return p
    raise RuntimeError("no free port in 8780-8899; see SETUP.md#registry")


def collisions(new: Store, others: list[Store]) -> list[str]:
    msgs = []
    for o in others:
        if o.name == new.name:
            msgs.append(f"name {new.name} is in use")
        if o.port == new.port:
            msgs.append(f"port {new.port} is used by store {o.name}")
        if o.database == new.database:
            msgs.append(f"database {new.database} is used by store {o.name}")
    return msgs


def _walk() -> list[tuple[Store | None, str | None]]:
    """Every store directory: (the store, None) when its file reads, (None, the error) when it does not."""
    root = paths.stores_dir()
    if not root.exists():
        return []
    out: list[tuple[Store | None, str | None]] = []
    for p in sorted(root.iterdir()):
        if (p / "store.toml").exists():
            try:
                out.append((Store.load(p.name), None))
            except ConfigError as e:
                out.append((None, str(e)))
    return out


def all_stores() -> list[Store]:
    """The stores whose files read. The ones that do not are in `store_problems()` — a caller that lists or
    diagnoses must show both; a caller that serves one store loads it by name and gets the ConfigError."""
    return [st for st, _ in _walk() if st is not None]


def store_problems() -> list[str]:
    return [err for _, err in _walk() if err is not None]
