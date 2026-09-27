"""The pre-warm fit is one transaction: a failure part-way leaves the store cold, the buffer file whole and nothing
placed; the next call retries and every memory is placed exactly once."""
import dataclasses

import psycopg

import agent_memory.spaces.live_store_state as ls
from agent_memory.spaces.live_store_state import LiveStore
from tests_support_fakes import Emb, stub_fit


def _cold(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "settings", dataclasses.replace(
        ls.settings, m3_buffer_path=str(tmp_path / "buf.jsonl"), m3_projector_path=str(tmp_path / "p.pkl")))
    return LiveStore(Emb(), mode="bootstrap", bootstrap_n=3, fit_fn=stub_fit)


def _count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_memory")
        return cur.fetchone()[0]


def test_a_failed_fit_leaves_the_store_cold_the_buffer_whole_and_nothing_placed(conn, tmp_path, monkeypatch):
    s = _cold(tmp_path, monkeypatch)
    s.save(conn, "one", session_key="s", memory_id="a")
    s.save(conn, "two", session_key="s", memory_id="b")
    real = ls.save_memory
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise psycopg.errors.AdminShutdown("simulated")
        return real(*a, **k)

    monkeypatch.setattr(ls, "save_memory", flaky)
    out = s.save(conn, "three", session_key="s", memory_id="c")
    assert out["status"] == "buffered" and "AdminShutdown" in out["fit_deferred"]
    assert not s.warm and _count(conn) == 0 and (tmp_path / "buf.jsonl").exists()
    assert s._session_nodes == {}
    monkeypatch.setattr(ls, "save_memory", real)
    assert s.retry_fit(conn) is True
    assert s.warm and _count(conn) == 3 and not (tmp_path / "buf.jsonl").exists()
    assert len(s._session_nodes["s"]) == 3
    assert s.retry_fit(conn) is False
