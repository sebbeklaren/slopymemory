# src/agent_memory/mcp/server.py
"""Host HTTP MCP server exposing memory_save / memory_retrieve over streamable-http.

Long-running so the nomic embedder loads once and stays warm. The DB connection is
PER-REQUEST (concurrency-safe — a single shared psycopg connection is not). The
nomic embedder + the m3 LiveStore (projector warm-loaded via `_store.warmup()`) load
once at startup and stay warm.
"""
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from mcp.server.fastmcp import Context, FastMCP

from agent_memory import durability as du
from agent_memory.config import settings
from agent_memory.embed.nomic import NomicEmbedder
from agent_memory.guard import find_secret_in_save, kind_in_field, refusal_message
from agent_memory.mcp import handlers
from agent_memory.spaces import store as m3
from agent_memory.spaces.live_store_state import LiveStore
from agent_memory.store import db

mcp = FastMCP("memory", host=settings.mcp_host, port=settings.mcp_port)

_embedder: NomicEmbedder | None = None
_store: "LiveStore | None" = None


HELD_MESSAGE = ("Saved to disk, not yet to the database; it will be written when the database is back. Tell the "
                "user. Fix: SETUP.md#postgres. To report it: slopymem doctor --report")
# One lock over hold -> save -> release, the drain and the session map: the design does not rely on FastMCP running
# sync tools one at a time on its event loop, which is true today and not a promise.
_lock = threading.Lock()
# schema_ready: the schema-ensure ran on a successful connection (once per process — the server may start while
# the database is down). failing: the last database call failed; db-status.json follows every change.
_state = {"schema_ready": False, "failing": False}


def _connect() -> psycopg.Connection:
    """A short connect_timeout, so a database that hangs rather than refuses answers `held` promptly."""
    conn = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=3)
    register_vector(conn)
    if not _state["schema_ready"]:
        _ensure_schema(conn)
        _state["schema_ready"] = True
    return conn


def _mark(ok: bool, err: str | None = None) -> None:
    """Record the database state in db-status.json for `slopymem doctor`, which reads files only."""
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        if _state["failing"] or du.read_status(du.state_dir()) not in (None, {"state": "ok"}):
            du.write_status({"state": "ok"})
    else:
        prev = du.read_status(du.state_dir()) or {}
        held, failed = du.counts()
        du.write_status({"state": "failing", "error": err,
                         "since": prev.get("since") if prev.get("state") == "failing" else now,
                         "last_seen": now, "held": held, "failed": failed})
    _state["failing"] = not ok


_recovered: dict | None = None              # the last drain's outcome, attached once to the next reply

UNAVAILABLE_MESSAGE = ("The memory database is not reachable; nothing can be retrieved. Tell the user. "
                       "Fix: SETUP.md#postgres. To report it: slopymem doctor --report")


def _drain(conn, skip: Path | None = None) -> None:
    """Held records in order, each through the same save path with its original id, time, session and fields. An
    already-committed id is released without being placed again; a connection error stops the drain (the rest stay,
    in order); any other failure moves that record to held-failed/ with its error and the drain goes on."""
    global _recovered
    written, failed, errors = 0, 0, []

    def keep(path, err):
        nonlocal failed
        du.fail(path, err); failed += 1; errors.append(err)

    for path in du.held_paths():
        if path == skip:
            continue
        try:
            rec = du.load(path)
        except (OSError, ValueError) as e:
            keep(path, du.clean_error(e)); continue
        if LiveStore._has_row(conn, rec["memory_id"]):
            du.release(path); continue
        try:
            out = handlers.memory_save(conn, _store, "default", rec["session_key"], rec["text"], rec["scope"],
                                       rec["thread"], rec["facets"], rec["save_concepts"], rec["supersedes"],
                                       memory_id=rec["memory_id"], now=datetime.fromisoformat(rec["now"]),
                                       known_ids=du.held_ids() - {rec["memory_id"]})
        except psycopg.OperationalError:
            raise
        except Exception as e:                      # noqa: BLE001 — every other failure is kept, never lost
            keep(path, du.clean_error(e)); continue
        if out.get("status") in ("error", "noop"):
            keep(path, out.get("note", out.get("status"))); continue
        du.release(path); written += 1
    if written or failed:
        _recovered = {"written": written, "failed": failed, "failed_errors": errors}


def _prepare(conn, skip: Path | None = None) -> None:
    """At the start of every tool call: drain what is held, then run a pre-warm fit a failure deferred."""
    if du.held_paths():
        _drain(conn, skip)
    _store.retry_fit(conn)


def _with_recovered(out: dict) -> dict:
    global _recovered
    if _recovered is not None:
        out, _recovered = {**out, "recovered": _recovered}, None
    return out


def _held_reply(memory_id: str, err: str) -> dict:
    return {"status": "held", "memory_id": memory_id, "held_count": du.counts()[0], "error": err,
            "message": HELD_MESSAGE}


def _log(record: dict) -> None:
    record["t"] = time.time()
    path = Path(settings.mcp_invocation_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _session_key_from_context(ctx: "Context | None") -> str:
    """Co-occurrence session key = the MCP session, derived SERVER-SIDE (session identity is
    infrastructure, never agent-supplied). A coding harness typically opens one MCP session per
    run (connected once at startup, reused), so the Mcp-Session-Id is stable per conversation/run and
    fresh per run. Primary: the `mcp-session-id` request header (the client sends it on every
    post-initialize call). Fallback: the per-connection ServerSession identity (stable within one
    MCP session). NOTE (production follow-up): the LiveStore's in-memory {session_key: [nodes]} map
    grows one entry per run with no cleanup — fine at this scale; a long-lived deployment should evict on
    MCP-session-close. The co-occurrence EDGE lives in the DB (spawned at save-time), so retrieval
    needs no session key and cross-session reach is unaffected by eviction."""
    sid = None
    try:
        sid = ctx.request_context.request.headers.get("mcp-session-id")
    except Exception:
        sid = None
    if sid:
        return sid
    sess = getattr(ctx, "session", None)
    return f"sess-{id(sess)}" if sess is not None else "default"


@mcp.tool(
    description=(
        "Save salient facts/decisions to long-term memory. Call this when the user states "
        "something worth remembering across sessions. `tenant` identifies the user (inert in v1 "
        "single-tenant); `scope` optionally tags the memory (e.g. 'ops/auth'). Co-occurrence "
        "(facts saved together in one session) is tracked automatically — you do NOT supply a "
        "session id. Optionally tag the save with a short `thread` label for the design/rationale "
        "topic it belongs to (e.g. 'combat-weight'); saves sharing a thread are associated durably "
        "across sessions — keep threads narrow (one rationale unit per thread). "
        "For design/why memories, ALSO extract the memory's facets (function/feeling/fiction/"
        "player_facing — same aspect definitions as memory_retrieve's query_facets) and pass them "
        "as `facets`: the memory is then born multi-space (retrievable via any of its aspects), not "
        "semantic-only. Be NONE-honest — omit any aspect the memory is silent on. "
        "ALSO extract the memory's typed CONCEPTS and pass `save_concepts=[[space,label],…]` — same "
        "dialect as retrieve's query_concepts — so the memory is immediately concept-retrievable. "
        "When the user explicitly confirms that this save CORRECTS a specific stored memory (one you "
        "quoted to them from retrieval), pass supersedes=<that memory_id>: the correction is durably bound "
        "and retrieval will thereafter attach the standing version to the old memory. Only on their "
        "explicit confirmation — never infer supersession yourself. "
        "Never save passwords, keys or tokens: a save that contains one is refused; save where the "
        "secret lives instead. "
        "Returns {status: saved|noop|buffered|refused}."
    )
)
def memory_save(tenant: str, text: str, scope: str | None = None, thread: str | None = None,
                facets: dict[str, str] | None = None, save_concepts: list | None = None,
                supersedes: str | None = None, ctx: Context | None = None) -> dict:
    session_key = _session_key_from_context(ctx)   # server-derived from the MCP session — never agent-supplied
    hit = find_secret_in_save(text, thread, scope, facets, save_concepts)
    if hit is not None:
        # REFUSED, never redacted, before any connection is opened: a memory is repeated on every retrieval,
        # so a secret stored once is leaked forever. Every agent-supplied string is guarded (text, thread,
        # scope, facet values, concept labels). The log carries the KIND and the FIELD NAME only — no text,
        # no snippet, none of the other values — because the invocation log is itself a plain-text file that
        # outlives the session.
        secret, field = hit
        kind = kind_in_field(secret, field)
        _log({"tool": "memory_save", "tenant": tenant, "session_key": session_key,
              "refused": "secret", "kind": kind, "field": field, "status": "refused"})
        return {"status": "refused", "reason": "secret", "kind": kind, "field": field,
                "message": refusal_message(secret, field)}
    noop = handlers.check_save(text, facets, supersedes)
    if noop is not None:
        return noop
    mid, now = uuid.uuid4().hex, datetime.now(timezone.utc)
    with _lock:
        # Write first: from here on the save survives whatever the database does. An unwritable held/ raises
        # (the save fails loudly with that error, nothing is claimed).
        path = du.hold(du.record(mid, text, session_key, scope, now, thread, facets, save_concepts, supersedes))
        try:
            conn = _connect()
            try:
                _prepare(conn, skip=path)          # earlier held saves first: the order they were made in
                out = handlers.memory_save(conn, _store, tenant, session_key, text, scope, thread, facets,
                                           save_concepts, supersedes, memory_id=mid, now=now,
                                           known_ids=du.held_ids() - {mid})
            finally:
                conn.close()
            du.release(path)
            _mark(True)
            out = _with_recovered(out)
        except psycopg.Error as e:
            err = du.clean_error(e)
            _mark(False, err)
            out = _held_reply(mid, err)
    _log({"tool": "memory_save", "tenant": tenant, "session_key": session_key,
          # FULL text, not an 80-char snippet: a warm save's text is recoverable via memory_id, but
          # a BUFFERED save returns memory_id None, so its (text -> concepts) pair would survive only
          # in the WAL. Under a real-use harvest there is no re-run to recover it from.
          "memory_id": out.get("memory_id") or mid, "text": text or "",
          # Log the agent's extraction VERBATIM, not a count/keys summary: the (text -> concepts)
          # pairs cannot be reconstructed after the fact, so they are kept verbatim here so they can
          # be replayed (guarded by a regression test that asserts this field is logged verbatim).
          # Save-side concepts/facets are also persisted
          # (m3_concept_link, m3_facet), so this half is belt-and-braces; the query side (below) is
          # the irrecoverable one.
          "thread": thread or "", "facets": facets or None,
          "save_concepts": save_concepts or None,
          "supersedes": supersedes or "",
          "status": out.get("status", ""), "note": out.get("note", "")})
    return out


@mcp.tool(
    description=(
        "Retrieve memories relevant to a query. Call this BEFORE answering anything that might "
        "have been remembered. Query as a NATURAL QUESTION (like asking a person), not keywords. "
        "Returns a RANKED GRADED SET (each hit: text + relevance score) to REASON OVER — not "
        "ground truth, and not a single answer: read it as core hits (top 2-3) plus connected "
        "reasons pulled in alongside — the tangential-looking results often carry the WHY. Weigh "
        "the set; never take just the top hit. Use k~8 for design/why questions (default 5 suits "
        "quick lookups). Empty results means nothing is known. `tenant` identifies the user. "
        "For design/why questions, extract the query's facets (function/feeling/fiction/player_facing "
        "— same aspect definitions as saving) and pass them as query_facets: in-distribution seeds "
        "massively outperform raw-text routing; omit facets the query is silent on. "
        "CONCEPT-PRIMARY mode: also extract the query's sparse typed CONCEPTS — the handful of things "
        "the query is ABOUT, each a short noun phrase typed by space (function/feeling/fiction/"
        "player_facing/semantic), NONE-honest (omit a space with nothing, never invent) — and pass as "
        "query_concepts, a list of [space, label] pairs. Extract MULTIPLE facets of the question, not "
        "just its headline, so the field lights every relevant concept. "
        "A result may carry superseded_by: the user has confirmed that memory CORRECTED — treat the "
        "attached standing version (full text included) as current and the result's own text as "
        "historical. superseded_by.conflict=true means the corrections disagree (reciprocal/divergent) "
        "— surface both candidates to the user to resolve; NEVER silently pick one. "
        "A result may also carry related: memories strongly tied to it (the same thread, or several shared "
        "concepts) that the query did not reach on its own — conventions and decisions that belong with it. "
        "Read them as part of the result; they are extra, never a replacement for one."
    )
)
def memory_retrieve(tenant: str, query: str, k: int = 5,
                    query_facets: dict[str, str] | None = None,
                    query_concepts: list | None = None) -> dict:
    with _lock:
        try:
            conn = _connect()
            try:
                _prepare(conn)
                out = handlers.memory_retrieve(conn, _store, tenant, query, k, query_facets=query_facets,
                                               query_concepts=query_concepts)
            finally:
                conn.close()
            _mark(True)
        except psycopg.Error as e:
            err = du.clean_error(e)
            _mark(False, err)
            out = {"status": "unavailable", "results": [], "error": err, "held_count": du.counts()[0],
                   "message": UNAVAILABLE_MESSAGE}
        out = _with_recovered(out)
    # THE IRRECOVERABLE HALF of the logged record: query_concepts / query_facets seed retrieval
    # and are then discarded — unlike the save side they are persisted NOWHERE. Logging len()/sorted()
    # instead of the values would destroy the (question -> extracted concepts) pair on every call —
    # the exact pair query-concept extraction depends on, and query-concept extraction is the
    # retrieval ceiling. Guarded by a regression test that asserts this field is logged verbatim.
    results = out.get("results", [])
    _log({"tool": "memory_retrieve", "tenant": tenant, "query": query, "k": k,
          "query_facets": query_facets or None,
          "query_concepts": query_concepts or None,
          # `returned` stays a flat id list — existing readers depend on it. `results` adds the
          # OUTCOME half: without the score, "was the right memory surfaced, and WHERE?" is
          # unanswerable after the fact, and a real-use harvest has no re-run. superseded_by is
          # recorded as a presence flag so the supersession behaviour is provable from the record
          # rather than from a transcript read.
          "returned": [r.get("memory_id") for r in results],
          "results": [{"memory_id": r.get("memory_id"), "score": r.get("score"),
                       "superseded_by": bool(r.get("superseded_by"))} for r in results]})
    return out


def _ensure_schema(conn: psycopg.Connection) -> None:
    """Idempotent boot-time schema-ensure. The m3 server writes EVERY memory to the m3_* tables (via
    the LiveStore), so it MUST ensure the m3 schema + seeded spaces — not only the legacy 768 schema.
    (The end-to-end run once hit `relation "m3_node" does not exist`: main() applied only db.apply_schema;
    the test conftest applied BOTH, masking the gap.) apply_schema is CREATE TABLE IF NOT EXISTS; seed_spaces is
    INSERT ... ON CONFLICT DO NOTHING — both safe to re-run on every boot."""
    db.apply_schema(conn)        # legacy 768 (kept — idempotent, harmless; mirrors conftest)
    m3.apply_schema(conn)        # m3 tables: m3_node, m3_synapse, m3_positive_link, m3_negative_link, m3_memory
    m3.seed_spaces(conn)         # the m3_space rows that m3_node.space_id references


def main() -> None:
    """The server starts with the database down: saves are held and retrievals answer `unavailable` until it
    answers; the schema-ensure runs on the first successful connection."""
    global _embedder, _store
    _embedder = NomicEmbedder()  # load once, stay warm
    _store = LiveStore(_embedder)
    _store.warmup()
    try:
        conn = _connect()
        try:
            with _lock:
                _prepare(conn)
        finally:
            conn.close()
    except psycopg.Error as e:
        err = du.clean_error(e)
        _mark(False, err)
        print(f"memory server: the database is not reachable at start ({err}); saves are held until it answers "
              "— see SETUP.md#postgres", flush=True)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
