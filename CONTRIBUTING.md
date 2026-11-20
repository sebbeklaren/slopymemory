# Contributing to slopymemory

The rules agents follow are in [AGENTS.md](AGENTS.md). This page has the same rules for people, with the reason for
each, so you can tell when one applies.

## Setting up

```
uv sync --frozen --extra dev
git config core.hooksPath .githooks      # the scan runs before every commit and push
.venv/bin/python -m pytest -q             # needs Postgres with pgvector; see .github/workflows/ci.yml
```

## The pattern, and why

- **The repo is the product.** Anything committed ships to every user, so experiment results, logs, scratch
  scripts and working notes stay outside it. A repo that collects leftovers slowly stops matching what it says it is.
- **Live data is not a test bench.** A test that writes into someone's real memory store leaves answers there that
  the next real session will find and trust. Tests use a throwaway home or a test database.
- **The owner decides what leaves the working tree.** Pushes, tags, releases, merges to `main` and restarts of
  services in use are visible to others or hard to undo, so each one is asked for and approved on its own.
- **A test that failed first.** A test written after the code usually passes because it was shaped by the code;
  watching it fail proves it can catch the bug.
- **A CHANGELOG line for every user-visible change.** Users read the changelog, not the commit log, to learn what
  an update does and whether they must restart anything.
- **One version, everywhere.** If the package, the changelog and the tag disagree, nobody can tell what is
  installed. `tests/test_version.py` refuses a mismatch.
- **Rules that matter become checks.** Written rules drift, especially across many agent sessions; a failing check
  does not. When a rule keeps being broken, the fix is a test or hook, not a louder sentence.
- **Decisions are written down with their reason,** at the moment they are made, so a later contributor does not
  undo one without knowing why it was made.

## Releasing

Follow the numbered steps in [AGENTS.md](AGENTS.md#in-this-repo). In short: bump `pyproject.toml` and `uv.lock`,
add the CHANGELOG section (saying whether stores need a restart), run the full suite, commit `X.Y.Z`, tag
`vX.Y.Z`, push both, and publish a GitHub release with that CHANGELOG section as its text.

## Using this pattern elsewhere

The "pattern" half of AGENTS.md names no part of this project on purpose. Copy it into another repo, then write
that repo's own "in this repo" half: its release steps, its checks, its live install.
