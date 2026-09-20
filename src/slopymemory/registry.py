"""The registry: directory prefix → store name. Longest matching prefix wins, so a worktree under a
repo resolves to the repo's store. Lives at `paths.registry_file()`; never inside a project."""
from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
import tomli_w
from . import paths
from .paths import ConfigError


@dataclass(frozen=True)
class Link:
    path: Path
    store: str


@dataclass
class Registry:
    links: list[Link] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Registry":
        f = paths.registry_file()
        if not f.exists():
            return cls()
        try:
            data = tomllib.loads(f.read_text())
            return cls([Link(Path(l["path"]), l["store"]) for l in data.get("link", [])])
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{f}: not valid TOML: {e} — see SETUP.md#registry") from e
        except KeyError as e:
            raise ConfigError(f"{f}: a link is missing the key {e} — see SETUP.md#registry") from e
        except (TypeError, AttributeError) as e:
            raise ConfigError(f"{f}: `link` must be a list of {{path, store}} tables: {e} — see SETUP.md#registry") from e
        except OSError as e:
            raise ConfigError(f"{f}: unreadable: {e} — see SETUP.md#registry") from e

    def save(self) -> None:
        paths.write_atomic(paths.registry_file(),
                           tomli_w.dumps({"link": [{"path": str(l.path), "store": l.store} for l in self.links]}))

    def resolve(self, cwd: Path) -> str | None:
        cwd = cwd.resolve()
        best: Link | None = None
        for l in self.links:
            p = l.path.resolve()
            if cwd == p or p in cwd.parents:
                if best is None or len(p.parts) > len(best.path.resolve().parts):
                    best = l
        return best.store if best else None

    def link(self, path: Path, store: str) -> None:
        path = path.resolve()
        for l in self.links:
            if l.path.resolve() == path:
                raise ValueError(f"{path} is already linked to {l.store}; unlink it first")
        self.links.append(Link(path, store))

    def unlink(self, path: Path) -> bool:
        path = path.resolve()
        before = len(self.links)
        self.links = [l for l in self.links if l.path.resolve() != path]
        return len(self.links) < before

    def paths_of(self, store: str) -> list[Path]:
        return [l.path for l in self.links if l.store == store]
