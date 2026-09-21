"""The installer's Python half. `install.sh` announces six steps; what each step needs to know or check is a
function here: the real download size read from the lock (never a made-up number), the free-space gate, the
Python check, the model download by its pinned revision, and what the doctor reads back.

Imports are standard-library only at module level: `preflight` runs BEFORE `uv sync`, in an interpreter that
has nothing installed yet. `huggingface_hub` is imported at the point of use, inside the venv."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MODEL = "nomic-ai/nomic-embed-text-v1.5"
MODEL_SIZE_NOTE = "this happens once (about 0.5 GB)"
MIN_FREE_BYTES = 4 * 2**30
MB = 2**20
HEAD_TIMEOUT_S = 8
ROOT_PACKAGE = "slopymemory"


def checkout_lock() -> Path:
    """`uv.lock` of the checkout `install.sh` runs from: beside this file's `src/` when run from the checkout, else
    the working directory's (the installer cd's to the checkout). An installed copy has no lock of its own."""
    beside = Path(__file__).resolve().parents[2] / "uv.lock"
    return beside if beside.exists() else Path.cwd() / "uv.lock"


# --- the lock: which wheels this platform will download, and how big they are -----------------------------------

@dataclass(frozen=True)
class Wheel:
    name: str           # the package's normalized name, as the lock spells it
    url: str
    size: int | None    # None when the index publishes none (the PyTorch index does not)

    @property
    def filename(self) -> str:
        return urllib.parse.unquote(self.url.rsplit("/", 1)[-1])


def _glibc_minor() -> int:
    """This machine's glibc minor version (2.x); the manylinux tags up to it fit here."""
    try:
        ver = os.confstr("CS_GNU_LIBC_VERSION") or ""          # "glibc 2.39"
        return int(ver.split()[-1].split(".")[1])
    except (ValueError, IndexError, OSError, AttributeError):
        return 28


def platform_tags() -> tuple[str, ...]:
    """Substrings a wheel filename carries when it fits this interpreter and machine, MOST specific first (the
    order uv prefers wheels in): this CPython's own binary wheels for the newest fitting platform tag, then
    abi3, then pure-Python. `locked_wheels` picks each package's wheel by the earliest tag it matches."""
    v = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    machine = os.uname().machine if hasattr(os, "uname") else "x86_64"
    if sys.platform.startswith("linux"):
        plats = [f"manylinux_2_{minor}_{machine}" for minor in range(_glibc_minor(), 4, -1)]
        plats += [f"manylinux2014_{machine}", f"manylinux2010_{machine}", f"manylinux1_{machine}", f"linux_{machine}"]
    elif sys.platform == "darwin":
        plats = [f"macosx_{major}_0_{machine}" for major in (15, 14, 13, 12, 11)] + [f"macosx_10_{minor}_{machine}" for minor in (15, 13, 9)]
    else:
        plats = ["win_amd64" if machine.lower() in ("amd64", "x86_64") else f"win_{machine.lower()}"]
    # abi3 wheels carry the OLDEST CPython they were built for (cp39-abi3 runs on every later 3.x)
    abis = [f"{v}-{v}", f"{v}-none"] + [f"cp3{minor}-abi3" for minor in range(sys.version_info[1], 1, -1)] + ["py3-none"]
    tags = [f"{abi}-{p}" for p in plats for abi in abis]
    return tuple(tags + ["py3-none-any"])


def _marker_may_apply(marker: str | None) -> bool:
    """A conservative read of a dependency marker: False only when it names ANOTHER platform outright
    (`sys_platform == 'win32'` on Linux, `sys_platform != 'linux'` on Linux). Anything else may apply — the
    number over-counts a little rather than under-counting."""
    if not marker or " or " in marker:
        return True
    for op, other in re.findall(r"sys_platform\s*(==|!=)\s*'([^']+)'", marker):
        if (op == "==") != (other == sys.platform):
            return False
    return True


def locked_wheels(lock: Path, platform_tags: tuple[str, ...], root: str | None = None) -> list[Wheel]:
    """One wheel per package the sync will install: the first wheel whose filename carries one of the platform
    tags. With `root` (the project's own package in the lock), only the packages reachable through its
    `dependencies` count — the dev group is not installed by `uv sync --no-dev`. Without `root`, every package.
    A package with no fitting wheel is skipped (nothing to download for it here; uv would say so itself)."""
    data = tomllib.loads(lock.read_text())
    packages: dict[str, list[dict]] = {}
    for p in data.get("package", []):
        packages.setdefault(p["name"], []).append(p)
    if root is not None and root in packages:
        wanted: dict[str, dict] = {}
        seen: set[tuple[str, str]] = set()
        todo: list[tuple[str, str | None, tuple[str, ...]]] = [(root, None, ())]
        while todo:
            name, version, extras = todo.pop()
            entries = packages.get(name, [])
            entry = next((e for e in entries if version is None or e.get("version") == version), None)
            if entry is None:
                continue
            wanted.setdefault(name, entry)
            groups = [entry.get("dependencies", [])]
            for extra in extras:                            # `{ name = "psycopg", extra = ["binary"] }`: that group too
                if (name, extra) not in seen:
                    seen.add((name, extra))
                    groups.append(entry.get("optional-dependencies", {}).get(extra, []))
            if (name, "") in seen and len(groups) == 1:
                continue
            seen.add((name, ""))
            for dep in (d for g in groups for d in g):
                if _marker_may_apply(dep.get("marker")):
                    todo.append((dep["name"], dep.get("version"), tuple(dep.get("extra", []))))
        wanted.pop(root, None)
        chosen = list(wanted.values())
    else:
        chosen = [e for es in packages.values() for e in es]
    out = []
    for p in chosen:
        best: tuple[int, dict] | None = None
        for w in p.get("wheels", []):
            fname = urllib.parse.unquote(w["url"].rsplit("/", 1)[-1])
            rank = next((i for i, t in enumerate(platform_tags) if t in fname), None)
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, w)
        if best is not None:
            out.append(Wheel(p["name"], best[1]["url"], best[1].get("size")))
    return out


def download_size(lock: Path, platform_tags: tuple[str, ...], cached: set[str], sizes: dict[str, int] | None = None,
                  root: str | None = None) -> tuple[int, dict[str, int | None]]:
    """(total bytes to download, {package: bytes}) for the wheels not already in `cached`. A wheel whose size the
    lock does not carry takes it from `sizes` (found out by the caller); still unknown → None in the map and
    absent from the total, so the total is never inflated by a guess."""
    per: dict[str, int | None] = {}
    for w in locked_wheels(lock, platform_tags, root):
        if w.name in cached:
            continue
        per[w.name] = w.size if w.size is not None else (sizes or {}).get(w.name)
    return sum(v for v in per.values() if v is not None), per


def remote_size(url: str) -> int | None:
    """The wheel's Content-Length from its server (one HEAD request), for an index that publishes no sizes.
    None when the server does not say — the caller reports 'unknown size', never a number."""
    try:
        # the CDN in front of the PyTorch index refuses urllib's default User-Agent outright
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "slopymemory-installer"})
        with urllib.request.urlopen(req, timeout=HEAD_TIMEOUT_S) as r:
            length = r.headers.get("Content-Length")
        return int(length) if length else None
    except (OSError, ValueError):
        return None


# --- uv's cache: what will not be downloaded ----------------------------------------------------------------------

def uv_cache_dir() -> Path | None:
    try:
        out = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out) if out else None


def cached_wheels(cache: Path | None, wheels: list[Wheel]) -> set[str] | None:
    """The names of `wheels` already unpacked in uv's cache: an entry `<cache>/wheels-v*/<index>/<name>/<version>-<tags>`
    (a link into `archive-v*`) whose target exists. An empty set when the cache exists but holds no wheels yet (the
    first run on a machine). None only when uv did not say where its cache is — then the caller prints the full
    size and says 'less if cached' rather than asserting that nothing is."""
    if cache is None:
        return None
    if not any(cache.glob("wheels-v*")):
        return set()
    hit = set()
    for w in wheels:
        name = w.name.lower().replace("_", "-")
        # two depths: `wheels-v5/pypi/<name>/…` for PyPI, `wheels-v5/index/<hash>/<name>/…` for another index.
        # The entry is `<version>-<tags>`, or `<version>-<hash>` when the tag string is long, so the entry is
        # found through the `.http` record written beside every FETCHED wheel, which carries the wheel's filename.
        records = [*cache.glob(f"wheels-v*/*/{name}/*.http"), *cache.glob(f"wheels-v*/*/*/{name}/*.http")]
        for rec in records:
            try:
                fetched = w.filename.encode() in rec.read_bytes()
            except OSError:
                continue
            if fetched and rec.with_suffix("").exists():   # the unpacked wheel itself; a dangling link is not cached
                hit.add(w.name); break
    return hit


# --- the gates -----------------------------------------------------------------------------------------------------

def free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


def check_free_space(path: Path) -> tuple[bool, str]:
    free = free_bytes(path)
    if free < MIN_FREE_BYTES:
        return False, (f"only {free / 2**30:.1f} GB free on the filesystem of {path}; the install wants 4 GB there "
                       f"(the venv is about 1.3 GB, the embedded Postgres and the stores grow beside it; the model's 0.5 GB "
                       f"goes to the Hugging Face cache, which may be another filesystem) — see SETUP.md#space")
    return True, f"{free / 2**30:.0f} GB free on the filesystem of {path}"


def find_python() -> str | None:
    """A Python 3.13+: what `uv python find --system 3.13` names, else one on PATH that reports 3.13+.
    `install.sh` step 1 does the same in bash (it has no Python yet at that point); this is the tested reference
    for the message and the order, kept in step with the script by hand."""
    try:
        out = subprocess.run(["uv", "python", "find", "--system", "3.13"], capture_output=True, text=True, check=True, timeout=60).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    for exe in ("python3.13", "python3.14", "python3", "python"):
        if (found := shutil.which(exe)) is None:
            continue
        try:
            v = subprocess.run([found, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                               capture_output=True, text=True, check=True, timeout=30).stdout.split()
            if tuple(int(x) for x in v) >= (3, 13):
                return found
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def check_python() -> tuple[bool, str]:
    py = find_python()
    if py is None:
        return False, "no Python 3.13+ found — `uv python install 3.13` downloads one (about 30 MB, no root) — see SETUP.md#python"
    return True, f"Python 3.13+: {py}"


def run_step(step_id: str, text: str, fn: Callable[[], object], already: Callable[[], bool]) -> bool:
    """Announce one step by its id and text; skip it when `already()` says it is done; else do it. Returns
    whether `fn` ran. Idempotence is the step's own (`already`), not a marker file. The reference shape of a step;
    `install.sh` announces its six in bash the same way (`say`), each idempotent on its own state."""
    print(f"{step_id}: {text}")
    if already():
        print("  already done")
        return False
    fn()
    return True


# --- step 2's preflight: the size, the space, the question — before anything is downloaded -------------------------

def preflight(venv: Path, yes: bool, lock: Path | None = None, cache: Path | None = None) -> int:
    """Print what `uv sync` will download (from the lock, minus uv's cache, sizes the lock lacks asked of their
    server), refuse below 4 GB free, and ask. Exit code: 0 to go on, 1 to stop."""
    venv = Path(venv)
    lock = Path(lock) if lock is not None else checkout_lock()
    ok, msg = check_free_space(venv)
    print(msg)
    if not ok:
        return 1
    wheels = locked_wheels(lock, platform_tags(), root=ROOT_PACKAGE)
    cached = cached_wheels(cache if cache is not None else uv_cache_dir(), wheels)
    sizes = {w.name: size for w in wheels if w.size is None if (size := remote_size(w.url)) is not None}
    full, per_all = download_size(lock, platform_tags(), set(), sizes, root=ROOT_PACKAGE)
    unknown = sorted(n for n, v in per_all.items() if v is None)
    if cached is None:
        print(f"download: about {full / MB:.0f} MB for {len(per_all)} package(s) (less if cached — `uv cache dir` did not answer)")
    elif not cached:
        print(f"download: about {full / MB:.0f} MB for {len(per_all)} package(s) (nothing in uv's cache yet)")
    else:
        # Both numbers: what uv's cache appears to hold is subtracted, but uv refetches a wheel it holds and cannot
        # verify against the lock's hash, so the full size is the ceiling and the subtracted one the floor.
        total, per = download_size(lock, platform_tags(), cached, sizes, root=ROOT_PACKAGE)
        print(f"download: about {total / MB:.0f} MB for {len(per)} package(s) not in uv's cache; {len(cached)} found there "
              f"(would be {full / MB:.0f} MB without the cache; uv refetches a cached wheel it cannot verify)")
    if unknown:
        print(f"  plus {len(unknown)} package(s) of unknown size (their index publishes none and did not answer): {', '.join(unknown)}")
    if not yes:
        answer = input("continue? [y/N] ").strip().lower()
        if answer != "y":
            print("stopped; nothing was installed")
            return 1
    return 0


# --- step 4: the model, once, by its pinned revision --------------------------------------------------------------

def snapshot_download():
    """`huggingface_hub.snapshot_download`, imported when first needed (inside the venv)."""
    from huggingface_hub import snapshot_download as sd
    return sd


def hub_cache() -> Path:
    """Where huggingface_hub keeps models: its own constant when importable, else the same environment rules."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return Path(HF_HUB_CACHE)
    except ImportError:
        if (c := os.environ.get("HF_HUB_CACHE")):
            return Path(c)
        return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def model_cache_dir(model: str = MODEL) -> Path:
    return hub_cache() / ("models--" + model.replace("/", "--"))


# What the embedder reads from the weights snapshot: the pinned snapshot's modules.json is Transformer + Pooling
WEIGHT_FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "modules.json",
                "sentence_bert_config.json", "1_Pooling/config.json")


def local_snapshot(repo: str, revision: str) -> Path | None:
    """The snapshot DIRECTORY `<hub>/models--<repo>/snapshots/<revision>` when it exists, else None. This is the one
    thing the embedder reads (`snapshot_download(local_files_only=True)` on a commit hash resolves to exactly it);
    no `refs/` is consulted — a fresh machine after `slopymem install-model` has none."""
    d = model_cache_dir(repo) / "snapshots" / revision
    return d if d.is_dir() else None


def missing_files(snapshot_dir: Path, names) -> list[str]:
    return [n for n in names if not (Path(snapshot_dir) / n).exists()]


def code_files(weights_snapshot: Path) -> list[str]:
    """The module files the model's config names for its classes (`<repo>--<module>.<Class>` → `<module>.py`)."""
    cfg = Path(weights_snapshot) / "config.json"
    try:
        auto_map = json.loads(cfg.read_text()).get("auto_map", {})
    except (ValueError, OSError):
        return []
    mods = {v.split("--", 1)[1].rsplit(".", 1)[0] for v in auto_map.values() if isinstance(v, str) and "--" in v}
    return sorted(f"{m}.py" for m in mods)


def cached_revisions(model: str = MODEL) -> list[str]:
    """The snapshot revisions of `model` present in the hub cache — what the doctor compares to the pin."""
    snaps = model_cache_dir(model) / "snapshots"
    if not snaps.is_dir():
        return []
    return sorted(p.name for p in snaps.iterdir() if p.is_dir())


def remote_code_repos(snapshot_dir: Path) -> list[str]:
    """The repositories a model's `trust_remote_code` classes live in, from its config's `auto_map`
    (`<repo>--<module>.<Class>`). Fetched with the model so the first load works offline too."""
    cfg = Path(snapshot_dir) / "config.json"
    if not cfg.exists():
        return []
    try:
        auto_map = json.loads(cfg.read_text()).get("auto_map", {})
    except (ValueError, OSError):
        return []
    repos = {v.split("--", 1)[0] for v in auto_map.values() if isinstance(v, str) and "--" in v}
    return sorted(repos)


def model_cached(revision: str, model: str = MODEL, allow_patterns: list[str] | None = None) -> str | None:
    """The snapshot's local path when huggingface_hub resolves it offline (for a commit hash: the directory exists),
    else None. Completeness is `missing_files` against `WEIGHT_FILES` — the hub does not check that offline."""
    try:
        return snapshot_download()(model, revision=revision, local_files_only=True, allow_patterns=allow_patterns)
    except Exception:           # huggingface_hub raises its own family of errors for "not in the cache"
        return None


def download_model(revision: str, model: str = MODEL, code_revision: str | None = None) -> str:
    """The pinned snapshot into the shared Hugging Face cache, once; then the remote code its config names, at
    `code_revision` (the substrate's `embed_code_revision`; None = the code repository's head). The code step
    runs even when the weights are cached, so an interrupted first run is repaired by the next call. Returns the
    snapshot's local path. Errors from the hub propagate: the caller names the anchor."""
    sd = snapshot_download()
    local = model_cached(revision, model)
    if local is None or missing_files(local, WEIGHT_FILES):     # absent, or a snapshot cut short: fetch what is missing
        print(f"downloading {model} at revision {revision[:12]} — {MODEL_SIZE_NOTE}")
        local = sd(model, revision=revision)
    for repo in remote_code_repos(Path(local)):                # quiet when everything is there: the caller reports
        try:
            sd(repo, revision=code_revision, allow_patterns=["*.py"], local_files_only=True)
        except Exception:
            at = f" at revision {code_revision[:12]}" if code_revision else ""
            print(f"fetching the model's code from {repo}{at} (a few small files)")
            sd(repo, revision=code_revision, allow_patterns=["*.py"])
    return local
