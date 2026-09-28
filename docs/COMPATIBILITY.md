# Compatibility

What slopymemory relies on in the harnesses and tools around it, and how a change there is caught before it reaches
you. When one of these changes in a way slopymemory does not handle, the fix ships as a new version: see
`slopymem update` in the README.

## What slopymemory relies on

| | what slopymemory uses | how a change is caught |
|---|---|---|
| **Claude Code** | `claude mcp add --scope user --transport stdio <name> -- <launcher>` and `claude mcp remove --scope user`; the `mcpServers` table in `~/.claude.json`; `~/.claude/CLAUDE.md`, read at session start; `autoMemoryEnabled` in `~/.claude/settings.json` — all three under `$CLAUDE_CONFIG_DIR` when it is set; its 2,048-character cap on each tool description and on server instructions (since 2.1.280) | the contract test registers, reads back with `claude mcp get` and removes, against the real CLI; a test keeps every description and the instructions under the cap |
| **Codex** | `codex mcp add <name> -- <launcher>` and `codex mcp remove`; `[mcp_servers.<name>]` in `config.toml` and `AGENTS.md`, both in `$CODEX_HOME` (default `~/.codex`) | the contract test, the same way, with `codex mcp get`; both harnesses are also checked with their config moved (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`) |
| **Harnesses configured by hand** (an MCP server entry in the harness's own config file) | the stdio launcher command, its environment, the server's instructions given to the model, tool results passed through as they are | slopymemory never writes their config; changes are tracked with each harness's maintainers |
| **MCP Python SDK** | the low-level server, the stdio server and the streamable-HTTP client, pinned in `uv.lock` | weekly Dependabot pull requests, each run through CI |
| **Postgres, pgvector, psycopg, embedded Postgres** | pinned in `uv.lock`; the system cluster's own version | Dependabot and CI's end-to-end install on embedded Postgres |
| **The embedding model** | pinned snapshot revisions | `slopymem doctor` (the model check) |
| **GitHub Actions and the runner image** | pinned versions in `.github/workflows/ci.yml` | Dependabot |

## Running the checks yourself

```bash
SLOPYMEM_CONTRACT=1 python -m pytest tests/contract      # the contract tests, against the harnesses installed here
python scripts/compat_check.py                           # the tests, harness versions, and what changed since last time
```

The contract tests run each harness's real command line in a throwaway home directory; they never touch your own
harness configuration, and refuse to run if they would. `compat_check.py` records each run under
`~/.slopymemory/compat/`, prints what changed since the previous record, and — when `gh` is installed and logged in —
lists new releases of Claude Code, Codex and the MCP Python SDK whose notes mention MCP, config, hooks, instructions,
memory or settings. It exits with status 1 when a contract fails, so it can run on a weekly timer.

## Last verified

| harness | version | contract |
|---|---|---|
| Claude Code | 2.1.283 | passed |
| Codex | 0.156.1 | passed |
