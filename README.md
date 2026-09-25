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

## Working with your harness's own memory

**The default: both stay on, and slopymemory goes first.** Your harness's own memory — a file index like
Claude Code's project memory, or Codex's equivalent — is never touched by installing or registering
slopymemory. The instructions sent with every session tell the agent to check the store before planning or
building, weigh what it gets back, and treat a file memory index, where one exists, as a fallback after the
store. Nothing about the harness's own memory changes unless you ask it to (see "Turning the harness's own
index off" below).

Those instructions ship because a build test found they matter: given both the store and a planted decision
it was never told about, a session with no instructions acted on it 2 times out of 6; with instructions
telling it to retrieve, weigh, and act, 6 out of 6. That is why the launcher sends them as its own MCP
instructions on every session, rather than leaving retrieval to chance. That result is about having rules
at all; a build test of the exact wording above, arriving from the server with nothing else telling the
agent to use memory, is still pending, and no claim is made here about that wording's own effect.

**What using it costs, on top of your harness's own baseline.** Every one of these is additional context
sent to the model; none of it replaces the other:

- The instructions above: measured at **≈530 tokens** per session (the difference in total input tokens —
  fresh plus cached — between an otherwise identical session with slopymemory registered and one without).
- The two tool schemas (`memory_retrieve`, `memory_save`): **≈1–2k tokens**, sent once, the first time a
  session actually calls a memory tool (harnesses that support deferred tools don't pay this until then).
- Each retrieval: **≈2k tokens**, and — like the rest of the conversation — it is re-sent on every later
  request in the same session, not only once.
- Each save: **under 1k tokens**.
- Your harness's own file memory index, where one exists, is re-sent on every request too, at whatever size
  your index has grown to; slopymemory does not change that cost either way.

Roughly: if a session does a handful of retrievals spread across the session, or two or three all at the
start, the tokens saved by not re-deriving what's in them from scratch break even with what the rules,
schemas and retrievals cost to carry. Below that it's a net cost for the judgment it buys; above it, memory
gets cheaper than re-deriving the same ground each time.

**The ordering is for this job, not a general ranking.** The instructions put the store before the file
index because this is about recall of decisions made in earlier sessions: a fact that lives only in the
store cannot be reached from a file index at all, while a fact the index holds is also something the store
can answer from. That is not a claim that slopymemory is better than your harness's own memory in general —
only that, for recalling what was decided before, checking the store first is the ordering the evidence
supports.

**How a memory gets corrected.** When you confirm that a decision has changed, save the new one with
`supersedes` pointing at the memory it replaces — that's this implementation's explicit correction link,
used on your confirmation, not something the agent decides on its own. It is not the only way an older
memory stops being acted on: at retrieval time a memory that converges more strongly with the question at
hand can simply outweigh one that converges less, with nothing superseded. Retrieval is never capped to
save tokens — there's no recommended number of results to ask for, and a smaller `k` isn't a way to cut
cost; the tokens above are what each retrieval costs regardless of how many results it returns.

**Turning the harness's own index off.** If you'd rather stop paying for the harness's own index once a
project has a store, `slopymem harness-memory off` switches it off — for Claude Code, `autoMemoryEnabled`
in your user settings; for Codex, its own feature flag. This is a **user-scope, machine-wide** switch, not a
per-project one: it turns the index off in every project on the machine, including ones with no store yet,
and a project with no store then has no memory at all in that harness until it gets one. The command says
this before it asks. `slopymem harness-memory on` puts back exactly what was there before — the key it
added is removed, a value it changed is restored, never just forced to `true`; if the setting was changed
by something else since, it tells you both values and asks rather than guessing. `slopymem harness-memory
status` reports the current state without changing anything. `slopymem uninstall` runs the `on` side of
this first, then removes the registration — your harness's own memory ends up exactly as it was before you
installed slopymemory. Nothing here deletes a memory file, in either direction. `slopymem summary` reports
what slopymemory has changed on this machine at any time, read-only.

**Pointers to files are allowed, not required.** A saved memory can carry a path to a longer document
("Full detail: <path>") for material that belongs in a file rather than a memory's text. That's a pattern
you can use for long-form material, not a step slopymemory needs — retrieval has answered from the memory
text alone in every case measured so far.

**What isn't proven yet.** Recall after an in-session compaction, and accuracy as a store grows well past a
few hundred memories, are both unmeasured; whether your harness re-sends its own file index after a
compaction is undocumented behaviour on the harness's side, not something slopymemory controls.

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
