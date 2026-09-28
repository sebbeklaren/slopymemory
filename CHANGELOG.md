# Changelog

What a user of slopymemory notices, newest first. A release that changes the code the store servers run says so:
restart each running store with `slopymem stop <name>` and `slopymem start <name>`.

## 0.5.0

Update with `slopymem update` (the MCP library changes underneath), then restart your stores:
`slopymem stop <name>` then `slopymem start <name>`.

- Built on version 2 of the MCP Python SDK. Harnesses that speak the earlier protocol versions still connect;
  Claude Code was checked end to end.
- A call in flight when its store's server goes away now fails at once, naming the store and its log, and is not
  retried (a save may already have landed); the next call reconnects.
- The store server now answers calls from worker threads; one lock keeps saves, the held-save drain and session
  wiring in order, as before.

## 0.4.1

Restart your stores: `slopymem stop <name>` then `slopymem start <name>`.

- Claude Code's and Codex's configuration is found where they keep it: `CLAUDE_CONFIG_DIR` (and Claude Code's legacy
  `.config.json`) and `CODEX_HOME`; `slopymem doctor` warns when either is not an absolute path.
- A save the database is up for but rejects is told apart from a database that is down; a partial drain is always
  reported; the order of held saves survives a clock stepping back.
- `slopymem doctor --report` names each store's Postgres version; `slopymem update` handles a damaged install record.
- The embedding stack moves to torch 2.14, transformers 5.16 and sentence-transformers 6.1 — proven to give the same
  vectors as before, so stored memories need nothing.

## 0.4.0

Restart your stores: `slopymem stop <name>` then `slopymem start <name>`.

- A save made while the database is down is kept on disk and written in, in order, once it is back; the agent is told
  `held`, with the error. One save is one transaction. `slopymem doctor` shows held saves; `doctor --report` prints a
  block to paste into an issue, without memory text.
- Retrieval finds a memory whose concepts were filed in the other space from the question's, and each result carries up
  to two strongly related memories (same thread, or two shared concepts) under `related`.
- `slopymem update`, and a once-a-day check for a newer version (asked at install; `slopymem update --check off`).
- The usage rules no longer ask agents to label their own choices; memories stay anonymous.

## 0.3

The usage rules the server sends, the reversible harness-memory switch, the registered name `slopymemory`.

## 0.2

Installable by a stranger: one installer, embedded or system Postgres, `slopymem doctor`.

## 0.1

The launcher and one store per project.
