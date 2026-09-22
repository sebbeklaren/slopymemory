import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from agent_memory.spaces import store as m3
from agent_memory.spaces import live_store_state as _lst
from agent_memory.spaces.live_store_state import LiveStore


class _StubEmbedder:
    def embed_document(self, text):
        v = np.zeros(768, dtype="float32"); v[0] = 1.0
        return v
    def embed_query(self, text):
        return self.embed_document(text)


class _StubProjector:
    def project(self, vec):
        return (0.0, 0.0, 0.0)


def _stub_fit(embedder, texts):
    return _StubProjector()


T = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def _wal_paths(monkeypatch, tmp_path):
    wal = tmp_path / "wal.jsonl"
    proj = tmp_path / "projector.pkl"
    monkeypatch.setattr(_lst, "settings",
                        dataclasses.replace(_lst.settings, m3_buffer_path=str(wal), m3_projector_path=str(proj)))
    return wal, proj


def test_buffered_save_appends_full_record_to_wal(conn, monkeypatch, tmp_path):
    m3.apply_schema(conn); m3.seed_spaces(conn)
    wal, _ = _wal_paths(monkeypatch, tmp_path)
    s = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=3, fit_fn=_stub_fit)
    s.save(conn, "the combat is heavy because dread", session_key="s1", now=T, scope=None)
    s.save(conn, "economy is scarce on purpose", session_key="s1", now=T, scope="eco")
    recs = [json.loads(ln) for ln in wal.read_text().splitlines() if ln.strip()]
    assert len(recs) == 2
    assert recs[0]["text"] == "the combat is heavy because dread"
    assert recs[0]["session_key"] == "s1" and recs[0]["scope"] is None
    assert recs[1]["scope"] == "eco"
    assert datetime.fromisoformat(recs[0]["now"]) == T          # tz-aware roundtrip
    assert recs[0]["memory_id"]                                  # uuid present


def test_fit_clears_wal(conn, monkeypatch, tmp_path):
    m3.apply_schema(conn); m3.seed_spaces(conn)
    wal, _ = _wal_paths(monkeypatch, tmp_path)
    s = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=2, fit_fn=_stub_fit)
    s.save(conn, "m1", session_key="s1", now=T)
    assert wal.exists()
    warmed = s.save(conn, "m2", session_key="s1", now=T)         # hits N -> fit_and_place
    assert warmed["status"] == "saved" and warmed.get("warmed") is True
    assert not wal.exists()                                       # DB is authoritative now


def test_warm_save_never_touches_wal(conn, monkeypatch, tmp_path):
    m3.apply_schema(conn); m3.seed_spaces(conn)
    wal, _ = _wal_paths(monkeypatch, tmp_path)
    s = LiveStore(_StubEmbedder(), mode="fixed", projector=_StubProjector())
    s.save(conn, "warm fact", session_key="s1", now=T)
    assert not wal.exists()                                       # warm path byte-identical


def test_restart_recovery_survives_wal(conn, monkeypatch, tmp_path):
    """Buffered saves survive a process restart via the WAL.
    The delete-the-WAL control below proves recovery is WAL-attributable."""
    m3.apply_schema(conn); m3.seed_spaces(conn)
    wal, _ = _wal_paths(monkeypatch, tmp_path)
    a = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=3, fit_fn=_stub_fit)
    a.save(conn, "combat is heavy because dread", session_key="s1", now=T)
    a.save(conn, "economy is scarce on purpose", session_key="s1", now=T)
    del a                                                        # "restart": process state gone

    b = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=3, fit_fn=_stub_fit)
    b.warmup()
    assert len(b._buffer) == 2                                   # recovered, count continues
    assert b._buffer[0][1] == "combat is heavy because dread"    # full text intact
    assert b._buffer[0][2] == "s1"                               # session_key intact -> co-occurrence preserved at fit
    warmed = b.save(conn, "story is the spine", session_key="s1", now=T)   # third save -> fit
    assert warmed["status"] == "saved" and warmed.get("warmed") is True
    assert not wal.exists()
    got = b.retrieve(conn, "combat heavy", 5)
    assert got["status"] == "ok" and len(got["results"]) >= 1    # pre-restart memory retrievable


def test_restart_without_wal_recovers_nothing_control(conn, monkeypatch, tmp_path):
    """Teeth control: same flow, WAL deleted between 'restart' halves -> empty buffer."""
    m3.apply_schema(conn); m3.seed_spaces(conn)
    wal, _ = _wal_paths(monkeypatch, tmp_path)
    a = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=3, fit_fn=_stub_fit)
    a.save(conn, "m1", session_key="s1", now=T)
    del a
    wal.unlink()                                                 # the one variable
    b = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=3, fit_fn=_stub_fit)
    b.warmup()
    assert b._buffer == []                                       # recovery is WAL-attributable


def test_wal_alongside_warm_projector_fails_loud(monkeypatch, tmp_path):
    import pickle, pytest
    wal, proj = _wal_paths(monkeypatch, tmp_path)
    wal.write_text('{"memory_id":"x","text":"t","session_key":"s","scope":null,"now":"2026-03-01T09:00:00+00:00"}\n')
    from agent_memory.spaces import coordinate_sources as cs
    cs.save_projector(_StubProjector(), str(proj))               # persisted projector = warm-load path
    s = LiveStore(_StubEmbedder(), mode="bootstrap", bootstrap_n=3, fit_fn=_stub_fit)
    with pytest.raises(RuntimeError, match="WAL"):
        s.warmup()                                               # fit should have cleared it -> inconsistent


def test_wal_in_fixed_mode_fails_loud(monkeypatch, tmp_path):
    import pytest
    wal, proj = _wal_paths(monkeypatch, tmp_path)
    wal.write_text('{"memory_id":"x","text":"t","session_key":"s","scope":null,"now":"2026-03-01T09:00:00+00:00"}\n')
    bg_path = str(tmp_path / "bg.txt")
    (tmp_path / "bg.txt").write_text("a\nb\nc\nd\n")
    monkeypatch.setattr(
        _lst,
        "settings",
        dataclasses.replace(_lst.settings, m3_buffer_path=str(wal),
                            m3_projector_path=str(proj),
                            m3_background_corpus_path=bg_path),
    )
    s = LiveStore(_StubEmbedder(), mode="fixed", fit_fn=_stub_fit)
    with pytest.raises(RuntimeError, match="WAL"):
        s.warmup()                                               # WAL is a bootstrap artifact only
