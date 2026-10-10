"""Retrieving with files: the memories anchored to those files first, every result showing its anchors, and each
anchor checked against the hash the caller sent. Nothing is hidden."""
import dataclasses
from datetime import datetime, timezone

import agent_memory.spaces.live_store_state as ls
from agent_memory.spaces import file_refs
from agent_memory.spaces.live_store_state import LiveStore
from tests_support_fakes import Emb, stub_fit, warm_store

H, G = "a" * 64, "b" * 64


def _ref(path, sha=H):
    return {"path": path, "sha256": sha, "line_start": None, "line_end": None}


def _mem(conn, mid, day, path=None, sha=H):
    """`day` orders the memories by save time: a higher day is newer."""
    at = datetime(2000, 1, day, tzinfo=timezone.utc)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO m3_memory (memory_id, text, created_at) VALUES (%s, %s, %s)", (mid, f"text {mid}", at))
    if path:
        file_refs.write(conn, mid, [_ref(path, sha)])


def _r(mid, score):
    return {"memory_id": mid, "score": score, "text": f"text {mid}"}


def test_anchored_first_query_order_then_newest_then_the_rest(conn):
    _mem(conn, "q_anchored", 1, "src/a.py")
    _mem(conn, "path_old", 2, "src/a.py")
    _mem(conn, "path_new", 3, "src/a.py")
    _mem(conn, "plain", 4)
    got = file_refs.anchor(conn, [_r("plain", 0.9), _r("q_anchored", 0.5)], [{"path": "src/a.py"}], k=10)
    assert [r["memory_id"] for r in got] == ["q_anchored", "path_new", "path_old", "plain"]
    assert [r.get("anchored") for r in got] == [True, True, True, None]
    assert got[0]["score"] == 0.5 and got[1]["score"] is None


def test_k_still_bounds_the_list(conn):
    for i in range(4):
        _mem(conn, f"m{i}", i + 1, "src/a.py")
    assert len(file_refs.anchor(conn, [_r("x", 0.1)], [{"path": "src/a.py"}], k=2)) == 2


def test_a_path_nothing_is_anchored_to_changes_nothing(conn):
    _mem(conn, "plain", 1)
    before = [_r("plain", 0.9)]
    assert file_refs.anchor(conn, [dict(r) for r in before], [{"path": "nowhere.py", "sha256": H}], k=5) == before


def test_every_result_shows_its_anchors_even_without_files_in_the_call(conn):
    _mem(conn, "m1", 1, "src/a.py")
    got = file_refs.anchor(conn, [_r("m1", 0.4)], None, k=5)
    assert got[0]["files"] == [{"path": "src/a.py", "sha256": H}] and "anchored" not in got[0]


def test_stale_missing_and_unchecked(conn):
    _mem(conn, "same", 1, "same.py", H)
    _mem(conn, "changed", 2, "changed.py", H)
    _mem(conn, "gone", 3, "gone.py", H)
    _mem(conn, "unchecked", 4, "unchecked.py", H)
    got = {r["memory_id"]: r["files"][0] for r in file_refs.anchor(
        conn, [], [{"path": "same.py", "sha256": H}, {"path": "changed.py", "sha256": G},
                   {"path": "gone.py", "sha256": None}, {"path": "unchecked.py"}], k=10)}
    assert got["same"]["stale"] is False
    assert got["changed"]["stale"] is True and got["changed"]["sha256"] == H
    assert got["gone"] == {"path": "gone.py", "sha256": H, "missing": True}
    assert "stale" not in got["unchecked"] and "missing" not in got["unchecked"]


def test_the_store_retrieve_anchors_on_the_classic_path(conn):
    s = warm_store()
    s.save(conn, "retry loop stays", session_key="s", memory_id="m1", files=[_ref("src/a.py")])
    out = s.retrieve(conn, "anything", 5, files=[{"path": "src/a.py", "sha256": G}])
    hit = next(r for r in out["results"] if r["memory_id"] == "m1")
    assert hit["anchored"] is True and hit["files"][0]["stale"] is True


def test_the_store_retrieve_anchors_on_the_concept_primary_path(conn, monkeypatch):
    monkeypatch.setattr(ls, "settings", dataclasses.replace(ls.settings, m3_retrieval_mode="concept_primary"))
    s = warm_store()
    s.save(conn, "retry loop stays", session_key="s", memory_id="m1", save_concepts=[["function", "retry"]],
           files=[_ref("src/a.py")])
    out = s.retrieve(conn, "q", 5, query_concepts=[["function", "unrelated words"]], files=[{"path": "src/a.py"}])
    assert out["results"][0]["memory_id"] == "m1" and out["results"][0]["anchored"] is True


def test_a_superseded_memory_found_by_path_still_carries_superseded_by(conn):
    s = warm_store()
    s.save(conn, "old rule", session_key="s", memory_id="old", files=[_ref("src/a.py")])
    s.save(conn, "new rule", session_key="s", memory_id="new", supersedes="old")
    out = s.retrieve(conn, "q", 5, files=[{"path": "src/a.py"}])
    old = next(r for r in out["results"] if r["memory_id"] == "old")
    assert old["superseded_by"]["memory_id"] == "new"


def test_warming_answers_as_before(conn):
    cold = LiveStore(Emb(), mode="bootstrap", bootstrap_n=5, fit_fn=stub_fit)
    assert cold.retrieve(conn, "q", 5, files=[{"path": "a"}]) == {"status": "warming up", "results": []}
