#!/usr/bin/env python3
"""The compatibility check: are the harnesses and tools slopymemory relies on (docs/COMPATIBILITY.md) still behaving
the way it expects? Run by hand, or weekly on a timer you set up yourself.

It runs the contract tests (tests/contract, real harness CLIs in a throwaway home), records each harness's version
and each contract's result in ~/.slopymemory/compat/<date>.json, and prints what changed since the last record —
plus, when `gh` is installed and logged in, new releases of the harnesses and the MCP SDK whose notes mention what
slopymemory relies on. Nothing is sent anywhere but those read-only `gh api` calls.

Usage: compat_check.py            exit 1 when a contract test failed."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "tests" / "contract" / "test_harness_contracts.py"
CLIS = {"claude-code": ["claude", "--version"], "codex": ["codex", "--version"]}
REPOS = {"claude-code": "anthropics/claude-code", "codex": "openai/codex", "mcp-python-sdk": "modelcontextprotocol/python-sdk"}
KEYWORDS = re.compile(r"\b(mcp|config|hooks?|instructions|memory|settings)\b", re.I)
_RESULT = re.compile(r"^(PASSED|FAILED|ERROR) \S+::\S+\[([\w-]+)\]")


def record_dir() -> Path:
    sys.path.insert(0, str(ROOT / "src"))
    from slopymemory import paths
    return paths.home() / "compat"


def harness_versions(clis: dict = CLIS, run=subprocess.run) -> dict:
    out = {}
    for name, cmd in clis.items():
        try:
            p = run(cmd, capture_output=True, text=True, timeout=30)
            out[name] = (p.stdout or p.stderr).strip().splitlines()[0] if p.returncode == 0 and (p.stdout or p.stderr).strip() else None
        except (OSError, subprocess.TimeoutExpired):
            out[name] = None
    return out


def parse_contracts(pytest_output: str) -> dict:
    res = {}
    for line in pytest_output.splitlines():
        m = _RESULT.match(line)
        if m:
            res[m.group(2)] = "passed" if m.group(1) == "PASSED" else "failed"
    return res


def run_contracts(run=subprocess.run) -> dict:
    p = run([sys.executable, "-m", "pytest", "-q", "-rA", str(CONTRACTS)], capture_output=True, text=True,
            cwd=ROOT, env={**os.environ, "SLOPYMEM_CONTRACT": "1", "PYTHONPATH": str(ROOT / "src")}, timeout=900)
    return parse_contracts(p.stdout)


def latest_record(d: Path) -> dict | None:
    files = sorted(d.glob("*.json")) if d.exists() else []
    for f in reversed(files):
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            continue
    return None


def diff(prev: dict | None, cur: dict) -> list:
    if prev is None:
        return ["first record: nothing to compare yet"]
    out = []
    for name in sorted(set(prev.get("versions", {})) | set(cur.get("versions", {}))):
        a, b = prev.get("versions", {}).get(name), cur.get("versions", {}).get(name)
        if a == b:
            continue
        if b is None:
            out.append(f"{name}: not installed now (was {a})")
        elif a is None:
            out.append(f"{name}: {b} (new)")
        else:
            out.append(f"{name}: {a} -> {b}")
    for name in sorted(set(prev.get("contracts", {})) | set(cur.get("contracts", {}))):
        a, b = prev.get("contracts", {}).get(name), cur.get("contracts", {}).get(name)
        if a != b:
            out.append(f"contract {name}: {a or 'not run'} -> {b or 'not run'}")
    return out


def release_notes(since_iso: str, repos: dict = REPOS, run=subprocess.run) -> list:
    """New releases since `since_iso` whose notes mention what slopymemory relies on — one line each, the first
    matching line of the notes. Over-reporting is the safe side: a keyword in a URL still counts."""
    since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    out = []
    for name, repo in repos.items():
        try:
            p = run(["gh", "api", f"repos/{repo}/releases?per_page=30"], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            return ["release notes skipped: gh is not installed"]
        except (OSError, subprocess.TimeoutExpired) as e:
            out.append(f"{name}: release notes not read ({e})")
            continue
        if p.returncode != 0:
            out.append(f"{name}: release notes not read ({(p.stderr or '').strip().splitlines()[:1]})")
            continue
        for r in json.loads(p.stdout or "[]"):
            published = r.get("published_at")
            if not published or datetime.fromisoformat(published.replace("Z", "+00:00")) <= since:
                continue
            hit = next((line.strip() for line in (r.get("body") or "").splitlines() if KEYWORDS.search(line)), None)
            if hit:
                out.append(f"{name} {r.get('tag_name')}: {hit}")
    return out


def main() -> int:
    now = datetime.now(timezone.utc)
    d = record_dir()
    prev = latest_record(d)
    cur = {"when": now.isoformat(), "versions": harness_versions(), "contracts": run_contracts()}
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{now:%Y-%m-%dT%H%M%S}.json").write_text(json.dumps(cur, indent=1))
    print(f"compatibility check, {now:%Y-%m-%d %H:%M} UTC")
    for name, v in cur["versions"].items():
        print(f"  {name}: {v or 'not installed'} — contract {cur['contracts'].get(name, 'not run')}")
    print("changed since the last record:")
    for line in diff(prev, cur) or ["nothing"]:
        print(f"  {line}")
    print("upstream releases that mention what slopymemory relies on:")
    first_window = (now - timedelta(days=30)).isoformat()          # a first run shows the last month, not all history
    for line in release_notes(prev["when"] if prev else first_window) or ["none"]:
        print(f"  {line}")
    failed = [n for n, r in cur["contracts"].items() if r != "passed"]
    if failed:
        print(f"FAILED contracts: {', '.join(failed)} — see docs/COMPATIBILITY.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
