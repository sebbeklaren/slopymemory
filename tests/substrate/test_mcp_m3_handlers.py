# tests/test_mcp_m3_handlers.py
import pytest

from agent_memory.mcp import handlers


class _FakeStore:
    def __init__(self): self.saved = []; self.saved_threads = []; self.saved_facets = []; self._warm = True; self.last_query_facets = "unset"
    def save(self, conn, text, *, session_key, now, scope=None, memory_id=None, thread=None, facets=None, save_concepts=None, supersedes=None):
        self.saved.append((text, session_key, scope)); self.saved_threads.append(thread); self.saved_facets.append(facets)
        return {"status": "saved", "memory_id": "m1"}
    def retrieve(self, conn, query, k, *, query_facets=None, facets=None, epoch_range=None, **kwargs):
        self.last_query_facets = query_facets
        return {"status": "ok", "results": [{"memory_id": "m1", "score": 0.9, "text": "the deploy uses the blue database"}]}


def test_memory_save_threads_session_key_and_scope():
    st = _FakeStore()
    out = handlers.memory_save(None, st, "tenantX", "sessABC", "a fact", scope="ops/auth")
    assert out["status"] == "saved"
    assert st.saved == [("a fact", "sessABC", "ops/auth")]


def test_memory_save_threads_thread_tag():
    st = _FakeStore()
    out = handlers.memory_save(None, st, "t", "sess1", "a fact", thread="combat-weight")
    assert out["status"] == "saved"
    assert st.saved_threads == ["combat-weight"]          # add saved_threads tracking to _FakeStore


def test_memory_save_empty_is_noop():
    st = _FakeStore()
    out = handlers.memory_save(None, st, "t", "s", "   ")
    assert out["status"] == "noop" and st.saved == []


def test_memory_retrieve_returns_graded_set():
    out = handlers.memory_retrieve(None, _FakeStore(), "t", "how to auth", k=3)
    assert out["status"] == "ok" and out["results"][0]["memory_id"] == "m1"


def test_memory_retrieve_handler_returns_text_field():
    """Handler-level twin: non-gated dual to the MCP server roundtrip test. Verifies the
    handler returns text from the store's retrieve verbatim, unmodified."""
    out = handlers.memory_retrieve(None, _FakeStore(), "t", "which database does the deploy use", k=3)
    assert out["status"] == "ok"
    result = out["results"][0]
    assert result["text"] == "the deploy uses the blue database"


# --- query_facets (facet-scoped retrieval): boundary validation + pass-through ---


def test_memory_retrieve_threads_valid_query_facets_to_store():
    """A valid content-space query_facets dict passes validation and is threaded to store.retrieve."""
    st = _FakeStore()
    handlers.memory_retrieve(None, st, "t", "how does the dash feel", k=3,
                             query_facets={"function": "a short burst of movement", "feeling": "snappy"})
    assert st.last_query_facets == {"function": "a short burst of movement", "feeling": "snappy"}


def test_memory_retrieve_none_query_facets_is_the_default_fallback():
    """Omitted query_facets -> None reaches the store (raw-text fallback), NOT {}."""
    st = _FakeStore()
    handlers.memory_retrieve(None, st, "t", "q", k=3)
    assert st.last_query_facets is None


def test_memory_retrieve_empty_query_facets_reaches_store_as_empty_dict():
    """query_facets={} is a live value (semantic-only), distinct from None — threaded verbatim."""
    st = _FakeStore()
    handlers.memory_retrieve(None, st, "t", "q", k=3, query_facets={})
    assert st.last_query_facets == {}


def test_memory_retrieve_rejects_unknown_facet_space():
    """Unknown space key -> ValueError. 'semantic' is NOT a facet key (it is seeded from the raw
    query), so it too is rejected — the teeth against a caller confusing the seed for a facet."""
    with pytest.raises(ValueError):
        handlers.memory_retrieve(None, _FakeStore(), "t", "q", query_facets={"bogus": "x"})
    with pytest.raises(ValueError):
        handlers.memory_retrieve(None, _FakeStore(), "t", "q", query_facets={"semantic": "x"})


def test_memory_retrieve_rejects_empty_string_facet_text():
    """A facet mapped to an empty/whitespace string -> ValueError (never a blank seed)."""
    with pytest.raises(ValueError):
        handlers.memory_retrieve(None, _FakeStore(), "t", "q", query_facets={"function": "   "})
    with pytest.raises(ValueError):
        handlers.memory_retrieve(None, _FakeStore(), "t", "q", query_facets={"function": ""})


# --- server-derived session key (boundary #1: Mcp-Session-Id, NEVER agent-supplied) ---
# The HANDLER still takes session_key (test above is unchanged — it tests the handler threading it);
# what changed: the SERVER derives session_key from the MCP session, not a tool arg. These pin that.

def test_session_key_from_context_reads_mcp_session_header():
    from agent_memory.mcp.server import _session_key_from_context

    class _Req:
        headers = {"mcp-session-id": "abc-123"}

    class _RC:
        request = _Req()

    class _Ctx:
        request_context = _RC()
        session = object()

    assert _session_key_from_context(_Ctx()) == "abc-123"   # primary: the MCP session header


def test_session_key_from_context_falls_back_to_session_identity():
    from agent_memory.mcp.server import _session_key_from_context
    sess = object()

    class _RC:
        request = None        # no usable header -> fall back to the per-connection ServerSession identity

    class _Ctx:
        request_context = _RC()
        session = sess

    assert _session_key_from_context(_Ctx()) == f"sess-{id(sess)}"


def test_session_key_from_context_none_ctx_is_default():
    from agent_memory.mcp.server import _session_key_from_context
    assert _session_key_from_context(None) == "default"


# --- boot-time schema-ensure (regression) ---
# The m3 server writes EVERY memory to the m3_* tables (via the LiveStore), so its boot must ensure
# the m3 schema + seeded spaces, not only the legacy 768 schema. The rig-on e2e hit
# `relation "m3_node" does not exist` because main() applied only db.apply_schema; the conftest
# applied BOTH (db + spaces.store), masking the gap. This pins the server boot-ensure.

def test_server_ensure_schema_creates_m3_tables_and_spaces(conn):
    from agent_memory.mcp import server
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS m3_concept_link, m3_positive_link, m3_negative_link, m3_synapse, m3_node CASCADE")
        cur.execute("DELETE FROM m3_space")
        cur.execute("SELECT to_regclass('m3_node')")
        assert cur.fetchone()[0] is None        # precondition: the fresh-production-DB state that broke the boot
    server._ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('m3_node')")
        assert cur.fetchone()[0] is not None    # the fix: boot-ensure recreated the m3 schema
        cur.execute("SELECT count(*) FROM m3_space")
        assert cur.fetchone()[0] > 0            # and re-seeded the spaces that m3_node.space_id references
