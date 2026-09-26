"""The reversible retrieval-mode switch: LiveStore.retrieve routes to the concept-primary field ONLY when
m3_retrieval_mode == 'concept_primary' AND query_concepts is supplied; otherwise the current
retrieve_memories path is byte-identical (the one-flag revert). query_concepts threads through.

The mode flag is set on a dataclasses.replace copy of live_store_state.settings."""
import dataclasses
import agent_memory.spaces.live_store_state as ls
import agent_memory.spaces.word_vote as wv


def _warm_store():
    return ls.LiveStore(embedder=object(), projector=object())   # warm = projector is not None


def _set_mode(monkeypatch, mode):
    monkeypatch.setattr(ls, "settings", dataclasses.replace(ls.settings, m3_retrieval_mode=mode))


def test_flag_on_with_concepts_routes_to_concept_primary(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(wv, "retrieve_concept_primary",
                        lambda conn, emb, qc, k: [{"memory_id": "CP", "score": 1.0, "text": "cp"}])
    monkeypatch.setattr(ls, "retrieve_memories", lambda *a, **k: calls.append("mem") or [])
    monkeypatch.setattr(ls, "_memory_texts", lambda *a, **k: {})
    _set_mode(monkeypatch, "concept_primary")
    out = _warm_store().retrieve(conn, "q", 5, query_concepts=[["function", "friendly fire"]])
    assert out == {"status": "ok", "results": [{"memory_id": "CP", "score": 1.0, "text": "cp"}]}
    assert "mem" not in calls                    # current path NOT taken


def test_default_flag_is_byte_identical_current_path(monkeypatch):
    calls = []
    monkeypatch.setattr(wv, "retrieve_concept_primary",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not route here")))
    monkeypatch.setattr(ls, "retrieve_memories", lambda *a, **k: calls.append("mem") or [])
    monkeypatch.setattr(ls, "_memory_texts", lambda *a, **k: {})
    _set_mode(monkeypatch, "memories")           # default mode
    # even WITH query_concepts supplied, mode 'memories' takes the current path
    out = _warm_store().retrieve(None, "q", 5, query_concepts=[["function", "x"]])
    assert calls == ["mem"]                       # current path taken, concept-primary skipped
    assert out["status"] == "ok"
