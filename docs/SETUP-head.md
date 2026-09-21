# slopymemory — setup and troubleshooting

You are probably an agent asked to find out why memory is not working. Start here.

## Installing

```bash
git clone <this repository> && cd slopymemory
./install.sh                      # asks before each step; --yes answers them; --help lists the flags
```

Linux, no root. Six announced steps, each idempotent — a second run repairs what is missing and leaves what is
right alone: **1** `uv` (installed into `~/.local/bin` only if absent; your shell rc files are never edited) and a
Python 3.13 (`uv python install 3.13` when there is none); **2** the package into a venv at `~/.slopymemory/venv`
from `uv.lock` exactly — the real download size is read from the lock (minus what uv already has cached) and
printed **before** anything is fetched, and the install refuses below 4 GB free; **3** Postgres — the system one
is probed and offered when it can provision, else the embedded one, either verified with a scratch database
(`slopymem install-postgres`); **4** the embedder model, once, about 0.5 GB, at the pinned revision, into the
shared Hugging Face cache (`slopymem install-model`); **5** the launcher registered in every harness found
(`slopymem register --detected`; `--no-harness` skips it); **6** `slopymem doctor`. `SLOPYMEM_HOME` moves
everything (venv, stores, embedded Postgres) elsewhere; the checkout can be deleted after the install.

`slopymem uninstall` lists what it will remove — the venv, the registrations (each harness's own remove
command, only where the launcher is registered), the offer line (only where present), the logs, the registry —
and asks twice. The stores and the embedded Postgres are **kept** unless `--data`, which drops every store's
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
- **the model** — `nomic-embed-text-v1.5` at ONE pinned revision (`AM_EMBED_REVISION`), in `~/.cache/huggingface/hub`.
  Every stored coordinate was embedded with that snapshot; a different one is a migration, not a setting.

## The three questions, in order

1. **Does this directory resolve to a store?** `slopymem list` — the directory must appear under a store. If not:
   the tools you see are only `memory_init`; run `slopymem init` (asks first) or `slopymem link <store>`. `init`
   refuses the home directory, `/`, and any directory above an already-linked project — start the harness IN the
   project (a desktop app's non-project modes have no project directory; that is expected).
2. **Is the store's server up?** `slopymem list` shows `up`/`down`. Down: `slopymem start <store>`; then read
   `~/.slopymemory/logs/<store>.log` if it stays down.
3. **Is the launcher registered in this harness?** Claude Code: `claude mcp list`; Codex: `codex mcp list`. Missing:
   `slopymem register`.

Then run `slopymem doctor`. Every failing line ends with the anchor of the section below that explains it.
