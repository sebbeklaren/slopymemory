# tests/test_memory_supersession.py
"""Memory-level supersession primitives. Teeth:
  1. note is idempotent-with-bump (repeat adjudication = strength bump, one row).
  2. resolve_standing walks to the HEAD (chain), flags cycles, flags divergent heads.
  3. attach_supersessions is ADD-ONLY: same order, same scores, same ids — always."""
import pytest
from agent_memory.spaces import memory_supersession as ms


def _mk_memory(conn, mid, text):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text) VALUES (%s, %s) "
                    "ON CONFLICT (memory_id) DO NOTHING", (mid, text))


def _row(conn, stale, by):
    with conn.cursor() as cur:
        cur.execute("SELECT strength FROM m3_memory_supersession "
                    "WHERE stale_memory_id=%s AND by_memory_id=%s", (stale, by))
        return cur.fetchone()


def test_note_idempotent_bump(conn):
    _mk_memory(conn, "a", "old"); _mk_memory(conn, "b", "new")
    ms.note_memory_supersession(conn, "b", "a")
    ms.note_memory_supersession(conn, "b", "a")
    assert _row(conn, "a", "b")[0] == pytest.approx(2.0), "repeat adjudication bumps, one row"


def test_resolve_simple_and_chain_head(conn):
    for m, t in [("a", "v1"), ("b", "v2"), ("c", "v3")]:
        _mk_memory(conn, m, t)
    ms.note_memory_supersession(conn, "b", "a")          # a superseded by b
    r = ms.resolve_standing(conn, ["a", "zz-not-superseded"])
    assert r == {"a": {"heads": ["b"], "cycle": False, "others": ["b"]}}
    ms.note_memory_supersession(conn, "c", "b")          # chain: a -> b -> c
    r = ms.resolve_standing(conn, ["a", "b"])
    assert r["a"]["heads"] == ["c"], "resolves to the HEAD, not one hop"
    assert r["b"]["heads"] == ["c"]


def test_resolve_cycle_is_conflict(conn):
    _mk_memory(conn, "a", "v1"); _mk_memory(conn, "b", "v2")
    ms.note_memory_supersession(conn, "b", "a")
    ms.note_memory_supersession(conn, "a", "b")          # reciprocal: a <-> b
    r = ms.resolve_standing(conn, ["a"])
    assert r["a"]["heads"] == [] and r["a"]["cycle"] is True
    assert r["a"]["others"] == ["b"], "conflict payload carries the other adjudication"


def test_resolve_divergent_heads_is_conflict(conn):
    for m in ("a", "b", "d"):
        _mk_memory(conn, m, m)
    ms.note_memory_supersession(conn, "b", "a")
    ms.note_memory_supersession(conn, "d", "a")          # a superseded by BOTH b and d
    r = ms.resolve_standing(conn, ["a"])
    assert sorted(r["a"]["heads"]) == ["b", "d"] and r["a"]["cycle"] is False


def test_attach_is_add_only(conn):
    for m, t in [("a", "old fact"), ("b", "new fact"), ("x", "unrelated")]:
        _mk_memory(conn, m, t)
    ms.note_memory_supersession(conn, "b", "a")
    results = [{"memory_id": "x", "score": 0.9, "text": "unrelated"},
               {"memory_id": "a", "score": 0.5, "text": "old fact"}]
    out = ms.attach_supersessions(conn, results)
    # GUARD 1: identical order, ids, scores — enrichment never touches ranking.
    assert [(r["memory_id"], r["score"]) for r in out] == [("x", 0.9), ("a", 0.5)]
    assert "superseded_by" not in out[0]
    # The standing version rides with FULL TEXT (never id-only).
    assert out[1]["superseded_by"] == {"memory_id": "b", "text": "new fact"}


def test_attach_conflict_shape(conn):
    _mk_memory(conn, "a", "v1"); _mk_memory(conn, "b", "v2")
    ms.note_memory_supersession(conn, "b", "a")
    ms.note_memory_supersession(conn, "a", "b")
    out = ms.attach_supersessions(conn, [{"memory_id": "a", "score": 1.0, "text": "v1"}])
    sb = out[0]["superseded_by"]
    assert sb["conflict"] is True
    assert sb["candidates"] == [{"memory_id": "b", "text": "v2"}]


def test_resolve_diamond_reconvergence_is_clean(conn):
    """DAG re-convergence is NOT a cycle: a->b->d AND a->cc->d reaches the single head d by two
    paths. Path-based cycle detection must return the clean standing version, not a bogus conflict."""
    for m in ("a", "b", "cc", "d"):
        _mk_memory(conn, m, m)
    ms.note_memory_supersession(conn, "b", "a")          # a -> b
    ms.note_memory_supersession(conn, "d", "b")          # b -> d
    ms.note_memory_supersession(conn, "cc", "a")         # a -> cc
    ms.note_memory_supersession(conn, "d", "cc")         # cc -> d  (diamond re-converges on d)
    r = ms.resolve_standing(conn, ["a"])
    assert r == {"a": {"heads": ["d"], "cycle": False, "others": ["d"]}}
    out = ms.attach_supersessions(conn, [{"memory_id": "a", "score": 1.0, "text": "a"}])
    assert out[0]["superseded_by"] == {"memory_id": "d", "text": "d"}, "clean standing, NOT conflict"


def test_resolve_shortcut_edge_is_clean(conn):
    """A shortcut edge (a->cc while also a->b->cc) is DAG re-convergence, not a loop: single head
    cc, no cycle."""
    for m in ("a", "b", "cc"):
        _mk_memory(conn, m, m)
    ms.note_memory_supersession(conn, "b", "a")          # a -> b
    ms.note_memory_supersession(conn, "cc", "a")         # a -> cc  (shortcut)
    ms.note_memory_supersession(conn, "cc", "b")         # b -> cc
    r = ms.resolve_standing(conn, ["a"])
    assert r["a"]["heads"] == ["cc"] and r["a"]["cycle"] is False
