# slopymemory — setup and troubleshooting

You are probably an agent asked to find out why memory is not working. Start here.

## Installing

```bash
git clone <the repository URL> slopymemory && cd slopymemory
./install.sh                      # asks before each step; --yes answers them; --help lists the flags
```

Linux, no root. From the system it needs three tools: `git` (the clone above), `curl` (only to fetch `uv` when it
is not installed), and `ss` from `iproute2` (to find the process on a store's port — `slopymem stop`, `remove`, the
doctor); a minimal image may lack them — the installer stops before downloading anything when `ss` is missing.
Six announced steps, each idempotent — a second run repairs what is missing and leaves what is
right alone: **1** `uv` (installed into `~/.local/bin` only if absent; your shell rc files are never edited) and a
Python 3.13 (`uv python install 3.13` when there is none); **2** the package into a venv at `~/.slopymemory/venv`
from `uv.lock` exactly — the tool check, then the real download size read from the lock (minus what uv already has
cached), printed **before** anything is fetched, and the install refuses below 4 GB free; **3** Postgres
(`slopymem install-postgres`, `--postgres system|embedded` picks); **4** the embedder model, once, about 0.5 GB, at
the pinned revision, into the shared Hugging Face cache (`slopymem install-model`); **5** the launcher registered
in every harness found (`slopymem register --detected`; `--no-harness` skips it); **6** `slopymem doctor`.
`SLOPYMEM_HOME` at install time moves everything (venv, stores, embedded Postgres) elsewhere; from then on the
command and the launcher find that home from the venv they run in (its parent, when it holds the registry), so a
harness needs no variable — only a shell that runs `slopymem` from somewhere else does. The checkout can be
deleted after the install.

Step 3, in full — two paths, verified either way with a scratch database:

- **System Postgres** (probed first; offered when it can provision). A one-time, as-a-superuser setup makes a
  template database pgvector is installed into, and lets the role that runs slopymem create databases from it:
  ```
  sudo -u postgres psql -c 'CREATE DATABASE slopymem_template' -c 'ALTER DATABASE slopymem_template IS_TEMPLATE true'
  sudo -u postgres psql -d slopymem_template -c 'CREATE EXTENSION vector'
  sudo -u postgres psql -c 'ALTER ROLE <user> CREATEDB;'
  ```
  Every store after that gets its own database on this Postgres, no further superuser steps.
- **Embedded Postgres** (used when the system one can't provision, or when asked for). A PostgreSQL + pgvector
  build from the `embedded-postgres` wheel, about 15 MB, living entirely under `~/.slopymemory/pg`, started on
  demand, reachable only over its own unix socket — nothing on the network, nothing shared with any system
  Postgres.

`slopymem uninstall` lists what it will remove — the venv (stopping the embedded Postgres first, whose binaries
live there; its data stays), the registrations (each harness's own remove command, on the entry whose command is
our launcher — whatever it is named; a foreign entry called `memory` is never touched), the offer line (only where
present), the logs, the registry — and asks twice. The stores and the embedded Postgres are **kept** unless `--data`, which drops every store's
database through its own backend and deletes `stores/` and `pg/` after a third question that lists the store
names and is never answered by `--yes`. `--yes` without `--data` is refused: the data left behind must be seen.

## The pieces

- **slopymem-mcp** — the launcher your harness starts in the project directory. It looks the directory up in
  the registry, starts that store's server if it is down, waits, and forwards your MCP calls to it.
- **a store** — one project's memory: a Postgres database and a state directory `~/.slopymemory/stores/<name>/`.
  The database is on the **system** Postgres (peer auth) or on the **embedded** one — a PostgreSQL 18 + pgvector
  cluster from the `embedded-postgres` wheel under `~/.slopymemory/pg`, started on demand before the store's server,
  reachable only over its unix socket (no TCP port), log at `~/.slopymemory/pg/log`. `store.toml` says which
  (`postgres = "system" | "embedded"`); `slopymem init --postgres …` chooses for a new store.
- **the registry** — `~/.slopymemory/registry.toml`: directory prefix → store name. Longest prefix wins.
- **the server** — one process per store, on `127.0.0.1:<port>` (`store.toml`), log at `~/.slopymemory/logs/<name>.log`.
- **slopymem** — the command: `init`, `link`, `unlink`, `list`, `start`, `stop`, `doctor`, `scan-store`, `register`, `remove`;
  the installer's `install-postgres`, `install-model`; `uninstall`.
- **the model** — `nomic-embed-text-v1.5` at ONE pinned revision (`AM_EMBED_REVISION`), and the code repository its
  classes come from (`nomic-bert-2048`) at its own pin (`AM_EMBED_CODE_REVISION`), both in `~/.cache/huggingface/hub`.
  A server loads the embedder from exactly those two snapshot directories — no network, no `refs/main`; without them
  it does not start, and says which `slopymem install-model` fetches. Every stored coordinate was embedded with that
  snapshot; a different one is a migration, not a setting.

## The three questions, in order

1. **Does this directory resolve to a store?** `slopymem list` — the directory must appear under a store. If not:
   the tools you see are only `memory_init`; run `slopymem init` (asks first) or `slopymem link <store>`. `init`
   refuses the home directory, `/`, and any directory above an already-linked project — start the harness IN the
   project (a desktop app's non-project modes have no project directory; that is expected).
2. **Is the store's server up?** `slopymem list` shows `up`/`down`. Down: `slopymem start <store>`; then read
   `~/.slopymemory/logs/<store>.log` if it stays down.
3. **Is the launcher registered in this harness?** Claude Code: `claude mcp list`; Codex: `codex mcp list` — it is
   listed as `slopymemory`. Missing: `slopymem register`. An older install registered it as `memory`; a re-run of
   `slopymem register` offers the rename (the tool names change with it, so update any `mcp__memory__` allowlists).

Then run `slopymem doctor`. Every failing line ends with the anchor of the section below that explains it.

## Checks (generated from `slopymemory.checks` — do not edit below this line)

## python

**Symptom:** the command or the launcher fails to start, or finds no stores where they were installed

**Verifies:** Python 3.13+ and the venv interpreter exist; which home is in use and why — SLOPYMEM_HOME when set, else the parent of the venv this interpreter runs from when that is a slopymemory home (~/.slopymemory itself, or it holds a registry or a stores dir), else ~/.slopymemory. The launcher a harness starts has no SLOPYMEM_HOME, so an install put elsewhere is found by that layout, not by the variable

**See for yourself:**

```bash
ls ~/.slopymemory/venv/bin/python; python3 --version; echo ${SLOPYMEM_HOME:-unset}
```

**Fix:** re-run the installer or the dev install script; to use an install elsewhere from a shell, export SLOPYMEM_HOME=<that home>

## package

**Symptom:** import errors on start

**Verifies:** slopymemory and the substrate are installed in the venv

**See for yourself:**

```bash
~/.slopymemory/venv/bin/python -c 'import slopymemory, agent_memory'
```

**Fix:** re-run the install

## postgres

**Symptom:** init fails or a server dies on start

**Verifies:** each backend in use: the embedded Postgres under ~/.slopymemory/pg (running or not — the doctor never starts it; pgvector; data size) when its data dir exists or a store is on it; the system Postgres (reachable, pgvector, template/superuser/CREATEDB) when a store is on it or nothing exists yet; one database per store on its own backend. A store whose backend is unreachable fails, by name. `slopymem init --postgres system|embedded` picks; the default is embedded when ~/.slopymemory/pg exists, else system if it can provision, else embedded

**See for yourself:**

```bash
slopymem list; ls ~/.slopymemory/pg; tail ~/.slopymemory/pg/log; psql -d postgres -Atc "select 1 from pg_available_extensions where name='vector'"
```

**Fix:** embedded: read ~/.slopymemory/pg/log, re-run the install if the wheel is missing; to stop it manually (no `slopymem stop` verb for it yet): ~/.slopymemory/venv/lib/python3.13/site-packages/embedded_postgres/pginstall/bin/pg_ctl -D ~/.slopymemory/pg -m fast stop; system: install pgvector / the template; a missing database: `slopymem init` or adopt with `link`

## model

**Symptom:** no server starts (online or offline), or every retrieval comes back subtly wrong

**Verifies:** the two snapshot DIRECTORIES the embedder loads from, and nothing else, are in the Hugging Face cache with their files: nomic-embed-text-v1.5 at the pinned revision the stores' coordinates were embedded with (AM_EMBED_REVISION) and its code repository (nomic-bert-2048, whose classes the model uses) at its own pin (AM_EMBED_CODE_REVISION); then the embedder's own offline resolution of both, the way a server starts (the hub's view of a complete snapshot is its cached tree listing, not the doctor's file list). No refs/main and no network are involved in a start; `slopymem install-model` fetches exactly those two. Another snapshot of the same model fails: changing the model is a migration (re-embed every store), not a config edit

**See for yourself:**

```bash
ls ~/.cache/huggingface/hub/models--nomic-ai--nomic-embed-text-v1.5/snapshots ~/.cache/huggingface/hub/models--nomic-ai--nomic-bert-2048/snapshots
```

**Fix:** slopymem install-model (once, about 0.5 GB, needs the network)

## registry

**Symptom:** a directory resolves to the wrong store, or two stores collide

**Verifies:** registry parses; every linked store exists; no two stores share a port or database

**See for yourself:**

```bash
cat ~/.slopymemory/registry.toml; slopymem list
```

**Fix:** slopymem unlink / link; fix the port in store.toml

## servers

**Symptom:** tools hang or the launcher reports 'no server answering'

**Verifies:** each store's server answers HTTP on its port

**See for yourself:**

```bash
slopymem list; ss -ltnp | grep 87
```

**Fix:** slopymem start <store>; read ~/.slopymemory/logs/<store>.log

## harnesses

**Symptom:** the agent has no memory tools

**Verifies:** each detected harness has slopymem-mcp registered at user scope

**See for yourself:**

```bash
claude mcp list; codex mcp list
```

**Fix:** slopymem register

## space

**Symptom:** disk is filling

**Verifies:** store data (state dirs, logs, and the files each store's env names — adopted stores keep theirs elsewhere) and free disk space under ~/.slopymemory; the venv is measured separately

**See for yourself:**

```bash
du -sh ~/.slopymemory; df -h ~
```

**Fix:** slopymem list shows per-store sizes; remove a store you no longer want

## logs

**Symptom:** something failed and nobody knows what

**Verifies:** the last error line of each server log (tail of the last 64 KB); error lines are reported, not failed; an unreadable log fails

**See for yourself:**

```bash
tail -50 ~/.slopymemory/logs/<store>.log
```

**Fix:** read the line; the error names its anchor

## secrets

**Symptom:** an agent tried to save a key or password

**Verifies:** refused saves per store from the invocation logs (count, kinds, first/last time; the texts were never logged); existing stores can be scanned with `slopymem scan-store <name>`, which reports and never deletes

**See for yourself:**

```bash
slopymem scan-store <name>
```

**Fix:** remove the memory by hand; tell the agent to save where the secret lives

## local-paths

**Symptom:** a leak of the author's machine into the package

**Verifies:** no file of the two installed packages (slopymemory and the vendored agent_memory) contains a home path (/home/<user>, /Users/<user>) or a private mailbox; a hit names the file and the shape

**See for yourself:**

```bash
grep -rE '/home/[A-Za-z]|/Users/[A-Za-z]|@(gmail|outlook|hotmail|yahoo|icloud|proton)' ~/.slopymemory/venv/lib/python3.13/site-packages/slopymemory ~/.slopymemory/venv/lib/python3.13/site-packages/agent_memory
```

**Fix:** report it — the export scanner missed it
