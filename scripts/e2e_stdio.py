#!/usr/bin/env python3
"""The end-to-end a harness would run, over stdio: from a project directory with no store the launcher offers exactly
`memory_init`; after `memory_init` the memory tools are there; twelve distinct saves warm the store (the first eleven
come back `buffered`, the twelfth `saved`); a retrieve by a natural question brings the TEXT of a known memory back.
Every step is timed and printed. Any deviation prints the payload it saw and exits 1; exit 0 means the text came back.

Usage: e2e_stdio.py --project DIR [--launcher CMD]
  DIR       the project directory (no store may resolve to it yet; `memory_init` creates one for it)
  CMD       the launcher command; default: the `slopymem-mcp` beside this interpreter, else the one on PATH

Run it with the install's interpreter: ~/.slopymemory/venv/bin/python scripts/e2e_stdio.py --project DIR
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path

WARM_AT = 12          # the coding dialect's warm-up count: the store fits its projector on the twelfth save
TENANT = "e2e"
INIT_TIMEOUT_S = 300.0    # memory_init starts the store's server, which loads the embedder first
CALL_TIMEOUT_S = 120.0    # a save (the twelfth fits the projector) or a retrieve

# Twelve distinct memories with their concepts (the coding dialect: `function` and `semantic` spaces), and the one the
# retrieve must bring back.
MEMORIES: list[tuple[str, list[list[str]]]] = [
    ("The config loader reads settings from a TOML file and refuses unknown keys.", [["function", "config loader"], ["semantic", "strict settings"]]),
    ("Outbound requests retry with exponential backoff, capped at thirty seconds.", [["function", "retry policy"], ["semantic", "backoff"]]),
    ("The nightly backup runs after the last export finishes and keeps seven copies.", [["function", "nightly backup"], ["semantic", "retention"]]),
    ("Log lines carry the request id so a failure can be traced to its call.", [["function", "request logging"], ["semantic", "traceability"]]),
    ("The queue worker acknowledges a job only after its result is written.", [["function", "queue worker"], ["semantic", "at-least-once delivery"]]),
    ("Feature flags are read once at start; changing one needs a restart.", [["function", "feature flags"], ["semantic", "startup configuration"]]),
    ("The search index is rebuilt in place and swapped in atomically.", [["function", "search index"], ["semantic", "atomic swap"]]),
    ("Sessions expire after twelve idle hours and are refreshed on every call.", [["function", "session expiry"], ["semantic", "idle timeout"]]),
    ("Migrations add a column with a default before any code reads it.", [["function", "database migration"], ["semantic", "backward compatibility"]]),
    ("The health endpoint answers only when the database and the cache are reachable.", [["function", "health endpoint"], ["semantic", "readiness"]]),
    ("Uploads above ten megabytes are streamed to disk instead of held in memory.", [["function", "upload handling"], ["semantic", "streaming"]]),
    ("The rate limiter allows sixty calls a minute per key and answers 429 beyond that.", [["function", "rate limiter"], ["semantic", "throttling"]]),
]
QUESTION = "When does the nightly backup run, and how many copies does it keep?"
QUERY_CONCEPTS = [["function", "nightly backup"], ["semantic", "retention"]]
EXPECTED_PREFIX = "The nightly backup runs after the last export finishes"
assert len(MEMORIES) == WARM_AT and len({t for t, _ in MEMORIES}) == WARM_AT


class Deviation(Exception):
    """What was expected did not happen; `payload` is what came back instead."""

    def __init__(self, what: str, payload: object = None) -> None:
        super().__init__(what)
        self.payload = payload


# --- the assertions, each on one payload -------------------------------------------------------------------------

def assert_init_mode(tools: list[str]) -> None:
    """A project with no store: exactly one tool, `memory_init`."""
    if tools != ["memory_init"]:
        raise Deviation(f"expected the tool list ['memory_init'] from a project with no store, got {tools}", tools)


def assert_memory_tools(tools: list[str]) -> None:
    """After `memory_init`: the store's tools, `memory_save` and `memory_retrieve` among them, `memory_init` gone."""
    missing = [t for t in ("memory_save", "memory_retrieve") if t not in tools]
    if missing or "memory_init" in tools:
        raise Deviation(f"expected memory_save and memory_retrieve (and no memory_init) after memory_init, got {tools}", tools)


def assert_buffered(payload: dict, n: int) -> None:
    """Save number n < WARM_AT: `buffered`, counting toward the warm-up."""
    if payload.get("status") != "buffered" or payload.get("count") != n:
        raise Deviation(f"expected save {n} of {WARM_AT} to come back buffered with count {n}", payload)


def assert_warmed(payload: dict) -> None:
    """The twelfth save: `saved` with a memory_id — the store fitted and placed everything buffered."""
    if payload.get("status") != "saved" or not payload.get("memory_id"):
        raise Deviation(f"expected save {WARM_AT} of {WARM_AT} to come back saved (the store warm)", payload)


def assert_text_back(payload: dict, prefix: str) -> dict:
    """A retrieve on a warm store: status ok, and a result whose TEXT starts with `prefix` — the product, not an id or
    a rank. Returns that result."""
    if payload.get("status") != "ok":
        raise Deviation(f"expected a retrieve with status ok, got {payload.get('status')!r}", payload)
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise Deviation("expected a non-empty results list", payload)
    for r in results:
        if isinstance(r, dict) and isinstance(r.get("text"), str) and r["text"].startswith(prefix):
            return r
    raise Deviation(f"no result's text starts with {prefix!r}", payload)


def payload_of(result) -> dict:
    """The tool's return value: the structured content when the server sent one, else the first text content parsed
    as JSON. An error result is a deviation carrying its text."""
    if getattr(result, "isError", False):
        raise Deviation("the tool call came back as an error", [getattr(c, "text", str(c)) for c in result.content])
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        return sc
    for c in result.content:
        text = getattr(c, "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except ValueError:
                raise Deviation("the tool's text content is not JSON", text) from None
    raise Deviation("the tool call carried no structured content and no text", str(result))


def text_of(result) -> str:
    if getattr(result, "isError", False):
        raise Deviation("the tool call came back as an error", [getattr(c, "text", str(c)) for c in result.content])
    return "".join(getattr(c, "text", "") for c in result.content)


# --- the run -----------------------------------------------------------------------------------------------------

def default_launcher() -> str:
    beside = Path(sys.executable).parent / "slopymem-mcp"
    if beside.exists():
        return str(beside)
    found = shutil.which("slopymem-mcp")
    if found:
        return found
    raise SystemExit("no slopymem-mcp beside this interpreter or on PATH; pass --launcher, or run this with ~/.slopymemory/venv/bin/python")


def _say(step: str, t0: float, note: str) -> None:
    print(f"{step}: {note} ({time.monotonic() - t0:.1f} s)", flush=True)


async def _run(project: Path, launcher: str) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    cmd = shlex.split(launcher)
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], cwd=str(project), env=dict(os.environ))
    t_all = time.monotonic()
    async with stdio_client(params, errlog=sys.stderr) as (read, write):   # the launcher's stderr, live, on ours
        async with ClientSession(read, write) as s:
            t0 = time.monotonic()
            await asyncio.wait_for(s.initialize(), CALL_TIMEOUT_S)
            tools = [t.name for t in (await asyncio.wait_for(s.list_tools(), CALL_TIMEOUT_S)).tools]
            assert_init_mode(tools)
            _say("tools", t0, "memory_init only, as for a project with no store")

            t0 = time.monotonic()
            res = await s.call_tool("memory_init", {"name": None, "dialect": "coding"}, read_timeout_seconds=timedelta(seconds=INIT_TIMEOUT_S))
            created = text_of(res)
            if "created store" not in created:
                raise Deviation("memory_init did not report a created store", created)
            _say("memory_init", t0, created)
            tools = [t.name for t in (await asyncio.wait_for(s.list_tools(), CALL_TIMEOUT_S)).tools]
            assert_memory_tools(tools)
            print(f"tools: now {tools}", flush=True)

            for n, (text, concepts) in enumerate(MEMORIES, start=1):
                t0 = time.monotonic()
                res = await s.call_tool("memory_save", {"tenant": TENANT, "text": text, "thread": "e2e", "save_concepts": concepts},
                                        read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT_S))
                payload = payload_of(res)
                if n < WARM_AT:
                    assert_buffered(payload, n)
                    _say(f"save {n:2d}/{WARM_AT}", t0, f"buffered {payload.get('count')}/{payload.get('need')}")
                else:
                    assert_warmed(payload)
                    _say(f"save {n:2d}/{WARM_AT}", t0, f"saved — the store is warm (memory_id {payload['memory_id']})")

            t0 = time.monotonic()
            res = await s.call_tool("memory_retrieve", {"tenant": TENANT, "query": QUESTION, "k": 5, "query_concepts": QUERY_CONCEPTS},
                                    read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT_S))
            payload = payload_of(res)
            hit = assert_text_back(payload, EXPECTED_PREFIX)
            _say("retrieve", t0, f"ok, {len(payload['results'])} result(s); the text is back: {hit['text']!r} (score {hit.get('score')})")
    print(f"e2e: ok — the text came back through the launcher over stdio ({time.monotonic() - t_all:.1f} s in all)", flush=True)
    return 0


def _cause(e: BaseException) -> BaseException:
    """What went wrong, out of the task-group wrapper: an exception leaving the client's `async with` blocks arrives as
    an ExceptionGroup around the one exception that was raised."""
    while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
        e = e.exceptions[0]
    return e


def run(project: Path, launcher: str) -> int:
    """0 when the text came back; 1 on any deviation, with what came back printed; 1 on anything else that stopped the
    run, with its message."""
    project = Path(project)
    if not project.is_dir():
        print(f"e2e: FAILED — {project} is not a directory", flush=True)
        return 1
    try:
        return asyncio.run(_run(project, launcher))
    except Exception as wrapped:
        e = _cause(wrapped)
        if isinstance(e, Deviation):
            print(f"e2e: FAILED — {e}", flush=True)
            print("payload:", json.dumps(e.payload, indent=2, default=str), flush=True)
        elif isinstance(e, TimeoutError):
            print("e2e: FAILED — a call did not answer in time (the launcher's stderr above says what it waited on)", flush=True)
        else:
            print(f"e2e: FAILED — {type(e).__name__}: {e}", flush=True)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--launcher", default=None, help="the launcher command (default: slopymem-mcp beside this interpreter)")
    a = ap.parse_args()
    return run(a.project, a.launcher or default_launcher())


if __name__ == "__main__":
    sys.exit(main())
