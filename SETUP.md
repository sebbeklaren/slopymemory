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
- **slopymem** — the command: `init`, `link`, `unlink`, `list`, `start`, `stop`, `doctor`, `register`, `remove`.

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

## Checks (generated from `slopymemory.checks` — do not edit below this line)

## python

**Symptom:** the command or the launcher fails to start

**Verifies:** Python 3.13+ and the venv interpreter exist

**See for yourself:**

```bash
ls ~/.slopymemory/venv/bin/python; python3 --version
```

**Fix:** re-run the installer or the dev install script

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

**Symptom:** the first server start is slow or fails offline

**Verifies:** the embedder is in the Hugging Face cache

**See for yourself:**

```bash
ls ~/.cache/huggingface/hub | grep nomic
```

**Fix:** start any store once while online

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

## local-paths

**Symptom:** a leak of the author's machine into the package

**Verifies:** no installed file contains a machine-local path

**See for yourself:**

```bash
grep -r '/home/' ~/.slopymemory/venv/lib/python3.13/site-packages/slopymemory
```

**Fix:** report it — the export scanner missed it
