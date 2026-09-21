# src/agent_memory/mcp/server.py
"""Host HTTP MCP server exposing memory_save / memory_retrieve over streamable-http.

Long-running so the nomic embedder loads once and stays warm. The DB connection is
PER-REQUEST (concurrency-safe — a single shared psycopg connection is not). The
nomic embedder + the m3 LiveStore (projector warm-loaded via `_store.warmup()`) load
once at startup and stay warm.
"""
import json
import time
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from mcp.server.fastmcp import Context, FastMCP

from agent_memory.config import settings
from agent_memory.embed.nomic import NomicEmbedder
from agent_memory.guard import find_secret, refusal_message
from agent_memory.mcp import handlers
from agent_memory.spaces import store as m3
from agent_memory.spaces.live_store_state import LiveStore
from agent_memory.store import db

mcp = FastMCP("memory", host=settings.mcp_host, port=settings.mcp_port)

_embedder: NomicEmbedder | None = None
_store: "LiveStore | None" = None


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    register_vector(conn)
    return conn


def _log(record: dict) -> None:
    record["t"] = time.time()
    path = Path(settings.mcp_invocation_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _session_key_from_context(ctx: "Context | None") -> str:
    """Co-occurrence session key = the MCP session, derived SERVER-SIDE (session identity is
    infrastructure, never agent-supplied). OpenHarness opens ONE MCP session per `oh` run
    (connect_all() at startup, reused), so the Mcp-Session-Id is stable per conversation/run and
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
    secret = find_secret(text)
    if secret is not None:
        # REFUSED, never redacted, before any connection is opened: a memory is repeated on every retrieval,
        # so a secret stored once is leaked forever. The log carries the KIND only — no text field, no
        # snippet — because the invocation log is itself a plain-text file that outlives the session.
        _log({"tool": "memory_save", "tenant": tenant, "session_key": session_key,
              "refused": "secret", "kind": secret.kind, "status": "refused"})
        return {"status": "refused", "reason": "secret", "kind": secret.kind,
                "message": refusal_message(secret)}
    conn = _connect()
    try:
        out = handlers.memory_save(conn, _store, tenant, session_key, text, scope, thread, facets,
                                   save_concepts, supersedes)
    finally:
        conn.close()
    _log({"tool": "memory_save", "tenant": tenant, "session_key": session_key,
          # FULL text, not an 80-char snippet: a warm save's text is recoverable via memory_id, but
          # a BUFFERED save returns memory_id None, so its (text -> concepts) pair would survive only
          # in the WAL. Under a real-use harvest there is no re-run to recover it from.
          "memory_id": out.get("memory_id", ""), "text": text or "",
          # Log the agent's extraction VERBATIM, not a count/keys summary: the (text -> concepts)
          # pairs are training data that cannot be reconstructed after the fact (guarded by
          # tests/test_invocation_log_trainability.py). Save-side concepts/facets are also persisted
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
        "— surface both candidates to the user to resolve; NEVER silently pick one."
    )
)
def memory_retrieve(tenant: str, query: str, k: int = 5,
                    query_facets: dict[str, str] | None = None,
                    query_concepts: list | None = None) -> dict:
    conn = _connect()
    try:
        out = handlers.memory_retrieve(conn, _store, tenant, query, k, query_facets=query_facets,
                                       query_concepts=query_concepts)
    finally:
        conn.close()
    # THE IRRECOVERABLE HALF of the training record: query_concepts / query_facets seed retrieval
    # and are then discarded — unlike the save side they are persisted NOWHERE. Logging len()/sorted()
    # instead of the values would destroy the (question -> extracted concepts) pair on every call —
    # the exact pair a learned replacement for query-concept extraction would train on, and
    # query-concept extraction is the retrieval ceiling. Guarded by tests/test_invocation_log_trainability.py.
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
    global _embedder, _store
    boot = _connect()
    try:
        _ensure_schema(boot)
    finally:
        boot.close()
    _embedder = NomicEmbedder()  # load once, stay warm
    _store = LiveStore(_embedder)
    _store.warmup()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
