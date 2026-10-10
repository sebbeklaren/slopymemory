"""A save's anchors travel every route a save takes, and are written in the save's one transaction."""
import json

import psycopg
import pytest

import agent_memory.spaces.live_store_state as ls
from agent_memory.spaces import file_refs
from agent_memory.spaces.live_store_state import LiveStore
from tests_support_fakes import Emb, stub_fit, warm_store

H = "c" * 64
REFS = [{"path": "src/a.py", "sha256": H, "line_start": 7, "line_end": 7}]


def test_a_warm_save_writes_its_anchors_and_says_how_many(conn):
    out = warm_store().save(conn, "keep the retry", session_key="s", memory_id="m1", files=REFS)
    assert out["files_anchored"] == 1
    assert file_refs.refs_of(conn, ["m1"]) == {"m1": [{"path": "src/a.py", "sha256": H, "line": 7}]}


def test_a_save_without_files_has_no_new_key(conn):
    out = warm_store().save(conn, "plain", session_key="s", memory_id="m1")
    assert "files_anchored" not in out and "files_deferred" not in out


def test_a_failure_writing_anchors_leaves_no_memory(conn, monkeypatch):
    def boom(*a, **k):
        raise psycopg.errors.DiskFull("simulated")
    monkeypatch.setattr(ls.file_refs, "write", boom)
    with pytest.raises(psycopg.errors.DiskFull):
        warm_store().save(conn, "x", session_key="s", memory_id="m1", files=REFS)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_memory WHERE memory_id = 'm1'")
        assert cur.fetchone()[0] == 0


def test_a_buffered_save_defers_its_anchors_and_the_fit_writes_them(conn):
    s = LiveStore(Emb(), mode="bootstrap", bootstrap_n=2, fit_fn=stub_fit)
    first = s.save(conn, "first", session_key="s", memory_id="a", files=REFS)
    assert first["status"] == "buffered" and first["files_anchored"] == 0 and first["files_deferred"] == 1
    second = s.save(conn, "second", session_key="s", memory_id="b", files=REFS)
    assert second["status"] == "saved" and second["files_anchored"] == 1
    assert set(file_refs.refs_of(conn, ["a", "b"])) == {"a", "b"}


def test_the_wal_carries_anchors_and_a_restart_recovers_them(conn):
    s = LiveStore(Emb(), mode="bootstrap", bootstrap_n=5, fit_fn=stub_fit)
    s.save(conn, "first", session_key="s", memory_id="a", files=REFS)
    with open(ls.settings.m3_buffer_path) as f:
        line = json.loads(f.read().splitlines()[0])
    assert line["files"] == REFS
    again = LiveStore(Emb(), mode="bootstrap", bootstrap_n=5, fit_fn=stub_fit)
    again.warmup()
    assert again._buffer[0][9] == REFS


def test_a_wal_line_from_before_anchors_recovers_without_them(conn):
    with open(ls.settings.m3_buffer_path, "w") as f:
        f.write(json.dumps({"memory_id": "a", "text": "t", "session_key": "s", "scope": None,
                            "now": "2026-01-01T00:00:00+00:00"}) + "\n")
    s = LiveStore(Emb(), mode="bootstrap", bootstrap_n=5, fit_fn=stub_fit)
    s.warmup()
    assert s._buffer[0][9] is None
