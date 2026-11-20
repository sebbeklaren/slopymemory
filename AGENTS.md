# Rules for agents (slopymemory)

1. The repo is the product: commit only code, its tests and its docs. Notes, logs, experiments stay outside.
2. Live data is not a test bench: tests use a throwaway home or test database, never a real store.
3. Ask the owner first: push, tag, release, merge to `main`, restarting stores in use, deleting data, touching
   another folder. One approval covers one action.
4. A behaviour change needs a test you saw fail; the full suite passes before a commit.
5. A user-visible change gets a CHANGELOG line.
6. Version, CHANGELOG top entry and tag always agree.
7. A rule broken twice becomes a check. Never bypass a hook; fix what it found.
8. Save each decision with its reason in the memory store when it is made.
9. Report what happened, failures and skipped steps included.

Before a release, a merge, or a dependency or tool-surface change: read that section of `CONTRIBUTING.md` first.
