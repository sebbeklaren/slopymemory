# slopymemory — setup and troubleshooting

You are probably an agent asked to find out why memory is not working. Start here.

## The pieces

- **slopymem-mcp** — the launcher your harness starts in the project directory. It looks the directory up in
  the registry, starts that store's server if it is down, waits, and forwards your MCP calls to it.
- **a store** — one project's memory: a Postgres database and a state directory `~/.slopymemory/stores/<name>/`.
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

**Fix:** re-run install.sh (Plan B) or the dev install script

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

**Verifies:** Postgres reachable, pgvector available, template/superuser/CREATEDB status, one database per store

**See for yourself:**

```bash
psql -d postgres -Atc "select 1 from pg_available_extensions where name='vector'"
```

**Fix:** install pgvector / create the missing database with `slopymem init` or adopt with `link`

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

**Verifies:** data under ~/.slopymemory (excluding venv), and free disk space on its filesystem; venv size shown separately

**See for yourself:**

```bash
du -sh ~/.slopymemory; df -h ~
```

**Fix:** slopymem list shows per-store sizes; remove a store you no longer want

## logs

**Symptom:** something failed and nobody knows what

**Verifies:** the last error line of each server log (tail of last 64 KB)

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
