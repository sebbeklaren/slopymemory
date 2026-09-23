# slopymemory

Per-project memory for coding agents. Each project gets its own store — a Postgres database plus a
small embedder — so an agent can save what it learned about your codebase and retrieve it later, in
that project only. One launcher, one registry on your machine, a store per project.

## Install

```bash
git clone https://github.com/sebbeklaren/slopymemory && cd slopymemory && ./install.sh
```

Linux, no root. Needs `git`, `curl` and `iproute2` (`ss`) from the system; asks before each step,
and `SETUP.md` explains every step and every failure. Six steps: a Python 3.13 (via `uv`, installed
into `~/.local/bin` if you don't have it already); the package into a venv at `~/.slopymemory/venv`;
Postgres; the embedder model; registering the launcher in the coding harnesses it finds on your
machine; a final health check. On a clean machine this downloads about 364 MB of packages and a
0.5 GB model, builds a venv of about 1.3 GB, and takes under a minute on a fast connection — less if
your machine already has CPU torch or the model cached, since `uv` and the Hugging Face cache are
shared with the rest of your system. The checkout can be deleted once it's done.

Postgres: if your machine already has one with pgvector available, the installer offers to use it
(a one-time superuser step sets up a template database); otherwise it sets up its own embedded
PostgreSQL under `~/.slopymemory/pg`, about 15 MB, reachable only from your account. Either way each
store gets its own database. `SETUP.md#postgres` covers both paths in full, including the exact
superuser commands.

`./install.sh --help` lists the flags (skip harness registration, pick which Postgres, answer every
question yes). `slopymem uninstall` removes the venv and the harness registrations and asks before
touching anything; your stores are kept unless you ask it to drop them too.

## Harnesses

- **Claude Code** and **Codex** — registered automatically, at user scope, once you say yes.
- **pi** — a table entry exists but is untested on this machine; if you use it, tell us how it goes.
- **Anything else that speaks MCP** — register a stdio server with command
  `~/.slopymemory/venv/bin/slopymem-mcp`, run from the project directory. Name it `slopymemory`, or anything
  your harness does not already use — the launcher does not depend on the name. Avoid `memory`: other memory
  servers are commonly registered under it.

## A project's first memory

Open a project in your harness and ask the agent to set up memory for it — the agent sees exactly one
tool, `memory_init`, until a store exists, and calling it creates one. Or run `slopymem init` yourself
in the project directory. Either way a fresh store, its first dozen memories, and the first retrieval
back are typically done in under 20 seconds.

From then on the directory decides which store a session talks to. The commands you'll use day to day:

- `slopymem init` — create a store for the current directory (or `--dialect design` for a non-coding one)
- `slopymem link <store>` — add another directory to an existing store
- `slopymem list` — every store, its linked directories, and whether its server is up
- `slopymem doctor` — check the whole install end to end
- `slopymem remove <store>` — delete a store (asks twice first)

## Size

A store's data — the database plus its state directory and logs — usually stays small. `slopymem
list` shows each store's size; `slopymem doctor` totals them against free disk space and warns past
1 GB of store data or under 2 GB free. Neither command deletes anything; that's always your call.

## Secrets

A save that looks like an API key, a token, a password-bearing URL or another high-entropy secret is
refused, with the reason, and never stored — only the kind is logged, never the text. Anything written
as ordinary prose ("the wifi password is …") has no shape to catch, which is why the harness
instructions also tell the agent never to save passwords, keys or tokens and to save where they live
instead. `slopymem scan-store <name>` reports look-alikes already sitting in a store, without deleting
them.

## Troubleshooting

Something not working? Ask your agent to read `SETUP.md` — every failure message names the section
that explains it (`SETUP.md#<id>`), with a command to check it yourself and a fix.

## The model

Retrieval runs on `nomic-embed-text-v1.5` (its model code comes from `nomic-bert-2048`), both under
the Apache 2.0 licence, both pinned by the installer to an exact revision, both running on CPU — no
Hugging Face account needed. Changing the model is a migration, not a setting: every store's saved
coordinates were embedded with that one pinned snapshot.

## Contributing

CI runs the repository's own scanner (`scripts/public_scan_ci.py`) over the tree and over every commit reachable
from the branch: secrets, paths into a home directory, private mailboxes and the maintainers' internal jargon are
refused, on a push and on a pull request alike. Commit with your GitHub noreply address (Settings → Emails →
"Keep my email addresses private"): a commit authored from a gmail, outlook or similar mailbox fails the history
scan, and the fix is an amend, not a discussion.

## Licence

MIT — see `LICENSE`.
