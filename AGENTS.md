# Rules for agents working on slopymemory

Read this before changing anything. `CLAUDE.md` imports this file; `CONTRIBUTING.md` has the same rules with the
reasons, for people. When a rule here and your own habit disagree, the rule wins. When a rule seems wrong for the
case in front of you, stop and ask the owner; do not work around it.

## The pattern (portable: holds for any product repo)

1. **The repo is the product.** Only what belongs in the shipped product is committed: code, its tests, its docs.
   Experiment results, run logs, scratch scripts, plans and notes live outside the repo.
2. **Live data is not a test bench.** Tests and experiments never write into a user's real data or a running
   install. They use a throwaway home, a test database, a copy.
3. **The owner's word first** for anything that leaves this working tree or is hard to undo: push, tag, release,
   merge to the main branch, restarting services that are in use, deleting data, and any change in a folder or
   project other than this one. Approval for one of these is not approval for the next.
4. **Every behaviour change carries a test that failed first,** and the full suite passes before a commit.
5. **Every user-visible change gets a CHANGELOG line** in the words a user would use, not a commit message.
6. **One version, everywhere.** The version number, the changelog's top entry and the release tag always agree; a
   check enforces it, and a release is not done until each place says the same.
7. **Rules that matter are checks, not prose.** When an agent breaks a rule twice, turn it into a test, hook or CI
   step that refuses the change. This file is for what a check cannot see.
8. **Decisions are recorded with their reason** where the next session will find them (here: the project's
   memory store), at the moment they are made.
9. **Report what happened, not what should have happened.** A failing test, a skipped step, an unverified claim is
   said plainly.

## In this repo

**Release** (only on the owner's word; every step, in this order):

1. Bump the version in `pyproject.toml` and the `slopymemory` entry in `uv.lock`.
2. Add `## X.Y.Z` at the top of `CHANGELOG.md` (no date in the heading). Its first line says whether running stores
   need a restart (`slopymem stop <name>` then `slopymem start <name>`): yes when server code changed.
3. Full suite: `.venv/bin/python -m pytest -q` (includes the embedder tests CI skips). `tests/test_version.py`
   checks steps 1–2.
4. Commit `X.Y.Z`, tag `vX.Y.Z` (annotated), push the branch and the tag.
5. GitHub release: `gh release create vX.Y.Z --title X.Y.Z --verify-tag --latest --notes "<that CHANGELOG section>"`.
   Every tag gets one; CI cannot check this step, so it is yours.

**Branches:** feature work happens on a branch (a worktree beside the repo is fine); `main` only receives finished,
tested work, merged on the owner's word.

**Checks that run without you:** `.githooks/pre-commit` and `pre-push` (enable once with
`git config core.hooksPath .githooks`) run `scripts/public_scan_ci.py`: no secrets, home paths, private mailboxes
or private project names in the public tree. CI runs the same scan, the suite without the embedder, and a
from-scratch install. Never bypass a hook (`--no-verify`); fix what it found.

**The live install** on a developer machine may be an editable install of this repo, so merging to `main` changes
what new sessions run. Server code reaches running stores only after a stop and start. Do not run `install.sh`
over an editable live install: it replaces it with a copy.

**Dependencies:** pins in `pyproject.toml` are deliberate. Widening one (MCP SDK, the embedding stack, pgvector) is
a planned change with its own proof, never a side effect; the embedder's vectors must stay bit-identical.

**Tool surface:** tool descriptions stay under 2,048 characters (Claude Code's cap; a test checks). A tool never
asks the agent where a memory goes, only what to remember.
