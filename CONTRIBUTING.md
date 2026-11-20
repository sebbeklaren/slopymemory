# Contributing to slopymemory

[AGENTS.md](AGENTS.md) holds the short rules every agent session loads. This page has the reasons and the detailed
steps, read when the work comes up. A rule lives in one place: the short form there, the detail here.

## Setting up

```
uv sync --frozen --extra dev
git config core.hooksPath .githooks      # the scan runs before every commit and push
.venv/bin/python -m pytest -q             # needs Postgres with pgvector; see .github/workflows/ci.yml
```

## The rules, and why

1. **The repo is the product.** Anything committed ships to every user. A repo that collects leftovers slowly stops
   matching what it says it is.
2. **Live data is not a test bench.** A test that writes into a real memory store leaves answers there that the
   next real session will find and trust.
3. **The owner decides what leaves the working tree.** Pushes, tags, releases, merges and restarts are visible to
   others or hard to undo, so each is asked for and approved on its own.
4. **A test that failed first.** A test written after the code is shaped by the code; watching it fail proves it
   can catch the bug.
5. **A CHANGELOG line per user-visible change.** Users read the changelog, not the commit log, to learn what an
   update does and whether to restart anything.
6. **One version everywhere.** If package, changelog and tag disagree, nobody can tell what is installed.
   `tests/test_version.py` refuses a mismatch.
7. **Rules that matter become checks.** Written rules drift across many agent sessions; a failing check does not.
8. **Decisions are written down with their reason** when made, so nobody undoes one without knowing why.
9. **Honest reports.** A skipped step or an unverified claim reported as done is worse than the failure itself.

## Releasing

Only on the owner's word. Every step, in order:

1. Bump the version in `pyproject.toml` and the `slopymemory` entry in `uv.lock`.
2. Add `## X.Y.Z` at the top of `CHANGELOG.md`, with no date in the heading. Its first line says whether running
   stores need a restart (`slopymem stop <name>` then `slopymem start <name>`): yes when server code changed.
3. Run the full suite: `.venv/bin/python -m pytest -q` (it includes the embedder tests CI skips).
   `tests/test_version.py` checks steps 1 and 2.
4. Commit `X.Y.Z`, tag `vX.Y.Z` (annotated), push the branch and the tag.
5. Publish the GitHub release:
   `gh release create vX.Y.Z --title X.Y.Z --verify-tag --latest --notes "<that CHANGELOG section>"`.
   Every tag gets one. CI cannot check this step.

## Branches and merging

Feature work happens on a branch; a worktree beside the repo is fine. `main` only receives finished, tested work,
merged on the owner's word.

On a developer machine the live install may be an editable install of this repo, so merging to `main` changes what
new sessions run. Server code reaches running stores only after a stop and start. Do not run `install.sh` over an
editable live install: it replaces it with a copy.

## Checks that run without you

`.githooks/pre-commit` and `pre-push` run `scripts/public_scan_ci.py`. It refuses secrets, home paths, private
mailboxes and private project names in the public tree. CI runs the same scan, the suite without the embedder, and
a from-scratch install. Never bypass a hook with `--no-verify`.

## Dependencies

The pins in `pyproject.toml` are deliberate. Widening one (MCP SDK, the embedding stack, pgvector) is a planned
change with its own proof, never a side effect. The embedder's vectors must stay bit-identical.

## Tool surface

Tool descriptions stay under 2,048 characters, Claude Code's limit; a test checks this. A tool never asks the
agent where a memory goes, only what to remember.

## Using this pattern elsewhere

The numbered rules name no part of this project's code. Copy them into another repo's `AGENTS.md`, then write that
repo's own `CONTRIBUTING.md` sections: its release steps, its checks, its live install.
