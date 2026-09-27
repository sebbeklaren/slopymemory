"""Related memories: each retrieved result carries up to N memories strongly tied to it — same thread, or at least
two shared concepts — without replacing, reordering or rescoring anything in the result list."""
import numpy as np

from agent_memory.spaces import concepts
from agent_memory.spaces.related import attach_related


class _Emb:
    """One-hot 768 vectors, one dimension per label: every label is its own concept node."""

    def __init__(self):
        self.dims = {}

    def embed_document(self, text):
        v = np.zeros(768, dtype="float32")
        v[self.dims.setdefault(text, len(self.dims))] = 1.0
        return v


def _mem(conn, emb, memory_id, *, thread=None, labels=()):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text, thread) VALUES (%s, %s, %s)",
                    (memory_id, f"text of {memory_id}", thread))
    for label in labels:
        concepts.link_concept_to_memory(conn, concepts.place_or_get_concept(conn, "semantic", label, emb), memory_id)


def _results(*ids):
    return [{"memory_id": m, "score": 1.0 - i / 10, "text": f"text of {m}"} for i, m in enumerate(ids)]


def _related_ids(result):
    return [r["memory_id"] for r in result.get("related", [])]


def test_a_thread_neighbour_rides_with_its_result_and_the_list_is_untouched(conn):
    emb = _Emb()
    _mem(conn, emb, "a", thread="frontend"); _mem(conn, emb, "naming", thread="frontend"); _mem(conn, emb, "b")
    before = _results("a", "b")
    out = attach_related(conn, [dict(r) for r in before], 2)
    assert [r["memory_id"] for r in out] == ["a", "b"]
    assert [(r["score"], r["text"]) for r in out] == [(r["score"], r["text"]) for r in before]
    assert out[0]["related"] == [{"memory_id": "naming", "text": "text of naming"}]
    assert "related" not in out[1]                      # absent, never an empty list


def test_two_shared_concepts_count_as_strong_and_one_does_not(conn):
    emb = _Emb()
    _mem(conn, emb, "a", labels=("retry policy", "backoff", "http client"))
    _mem(conn, emb, "two", labels=("retry policy", "backoff"))
    _mem(conn, emb, "one", labels=("http client",))
    out = attach_related(conn, _results("a"), 2)
    assert _related_ids(out[0]) == ["two"]


def test_session_only_ties_do_not_count(conn):
    emb = _Emb()
    _mem(conn, emb, "a"); _mem(conn, emb, "same-session")          # no thread, no shared concept
    assert "related" not in attach_related(conn, _results("a"), 2)[0]


def test_at_most_n_per_result_same_thread_first_then_more_shared_concepts(conn):
    emb = _Emb()
    _mem(conn, emb, "a", thread="t", labels=("x", "y", "z"))
    _mem(conn, emb, "c2", labels=("x", "y"))
    _mem(conn, emb, "c3", labels=("x", "y", "z"))
    _mem(conn, emb, "th", thread="t")
    assert _related_ids(attach_related(conn, _results("a"), 2)[0]) == ["th", "c3"]
    assert _related_ids(attach_related(conn, _results("a"), 3)[0]) == ["th", "c3", "c2"]


def test_a_memory_already_shown_or_already_attached_is_not_repeated(conn):
    emb = _Emb()
    for m in ("a", "b", "n1", "n2"):
        _mem(conn, emb, m, thread="t")
    out = attach_related(conn, _results("a", "b"), 2)
    assert _related_ids(out[0]) == ["n1", "n2"] or _related_ids(out[0]) == ["n2", "n1"]
    assert "b" not in _related_ids(out[0])               # b is in the list itself
    assert "related" not in out[1]                        # a, n1, n2 are all taken; the top result claims first


def test_a_superseded_neighbour_is_skipped(conn):
    emb = _Emb()
    _mem(conn, emb, "a", thread="t"); _mem(conn, emb, "old", thread="t"); _mem(conn, emb, "new", thread="t")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory_supersession (stale_memory_id, by_memory_id) VALUES ('old', 'new')")
    assert _related_ids(attach_related(conn, _results("a"), 2)[0]) == ["new"]


def test_zero_leaves_the_results_byte_identical(conn):
    emb = _Emb()
    _mem(conn, emb, "a", thread="t"); _mem(conn, emb, "n", thread="t")
    before = _results("a")
    assert attach_related(conn, [dict(r) for r in before], 0) == before
