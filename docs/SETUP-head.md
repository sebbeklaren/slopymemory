# slopymemory — setup and troubleshooting

You are probably an agent asked to find out why memory is not working. Start here.

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
- **slopymem** — the command: `init`, `link`, `unlink`, `list`, `start`, `stop`, `doctor`, `scan-store`, `register`, `remove`.

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
