"""A store with fewer memories than its full search needs (bootstrap_n) still answers a search: it compares the
question directly with each saved memory. A bare "warming up" made agents think memory was off, so they stopped
saving and the store never reached the count."""
import pytest
from agent_memory.spaces.live_store_state import LiveStore
from tests_support_fakes import Emb, stub_fit

H, G = "a" * 64, "b" * 64
RETRIEVE_MSG = ("This store has {n} memories. Until it has {need}, search compares your question directly with each "
                "saved memory; from the {need}th on, the store's full search starts, which also uses concepts, threads "
                "and related memories. Every save is kept. Keep saving decisions as they are made.")
SAVE_MSG = "Saved and already searchable. The store has {n} of {need} memories; at {need} its full search starts."


def _cold(n=5) -> LiveStore:
    return LiveStore(Emb(), mode="bootstrap", bootstrap_n=n, fit_fn=stub_fit)


def _ref(path, sha=H):
    return {"path": path, "sha256": sha, "line_start": None, "line_end": None}


def test_a_cold_store_finds_a_saved_memory_by_its_text(conn):
    s = _cold()
    s.save(conn, "alpha", session_key="s", memory_id="a")
    s.save(conn, "beta", session_key="s", memory_id="b")
    out = s.retrieve(conn, "alpha", 5)
    assert out["status"] == "ok"
    assert out["results"][0]["memory_id"] == "a" and out["results"][0]["text"] == "alpha"
    assert {r["memory_id"] for r in out["results"]} == {"a", "b"}


def test_the_reply_explains_early_search(conn):
    s = _cold()
    s.save(conn, "alpha", session_key="s")
    s.save(conn, "beta", session_key="s")
    out = s.retrieve(conn, "alpha", 5)
    assert out["early"] == {"memories": 2, "full_search_at": 5}
    assert out["message"] == RETRIEVE_MSG.format(n=2, need=5)


def test_an_empty_store_answers_ok_with_nothing(conn):
    out = _cold().retrieve(conn, "anything", 5)
    assert out["status"] == "ok" and out["results"] == [] and out["early"]["memories"] == 0
    assert out["message"] == RETRIEVE_MSG.format(n=0, need=5)


def test_k_limits_and_never_overruns_the_buffer(conn):
    s = _cold()
    for t in ("one", "two", "three"):
        s.save(conn, t, session_key="s")
    assert len(s.retrieve(conn, "one", 2)["results"]) == 2
    assert len(s.retrieve(conn, "one", 50)["results"]) == 3


def test_anchored_buffered_memories_come_first_and_show_stale(conn):
    s = _cold()
    s.save(conn, "alpha", session_key="s", memory_id="a")
    s.save(conn, "about the parser", session_key="s", memory_id="p", files=[_ref("src/p.py")])
    out = s.retrieve(conn, "alpha", 5, files=[{"path": "src/p.py", "sha256": G}])
    first = out["results"][0]
    assert first["memory_id"] == "p" and first["anchored"] is True
    assert first["files"] == [{"path": "src/p.py", "sha256": H, "stale": True}]
    assert "anchored" not in out["results"][1]


def test_a_buffered_correction_marks_the_old_memory(conn):
    s = _cold(10)
    s.save(conn, "port 8780", session_key="s", memory_id="old")
    s.save(conn, "port 8781", session_key="s", memory_id="mid", supersedes="old")
    s.save(conn, "port 8782", session_key="s", memory_id="new", supersedes="mid")
    by_id = {r["memory_id"]: r for r in s.retrieve(conn, "port", 10)["results"]}
    assert by_id["old"]["superseded_by"] == {"memory_id": "new", "text": "port 8782"}
    assert by_id["mid"]["superseded_by"] == {"memory_id": "new", "text": "port 8782"}
    assert "superseded_by" not in by_id["new"]


def test_two_corrections_of_one_memory_are_a_conflict(conn):
    s = _cold(10)
    s.save(conn, "colour red", session_key="s", memory_id="old")
    s.save(conn, "colour blue", session_key="s", memory_id="b", supersedes="old")
    s.save(conn, "colour green", session_key="s", memory_id="g", supersedes="old")
    old = next(r for r in s.retrieve(conn, "colour", 10)["results"] if r["memory_id"] == "old")
    assert old["superseded_by"]["conflict"] is True
    assert {c["memory_id"] for c in old["superseded_by"]["candidates"]} == {"b", "g"}


def test_search_works_after_a_restart_from_the_wal(conn):
    s = _cold()
    s.save(conn, "alpha", session_key="s", memory_id="a")
    again = _cold()
    again.warmup()                                       # recovers the buffer from the WAL, as at server start
    out = again.retrieve(conn, "alpha", 5)
    assert out["results"][0]["memory_id"] == "a" and out["early"]["memories"] == 1


def test_the_buffered_save_reply_explains(conn):
    s = _cold()
    s.save(conn, "one", session_key="s")
    out = s.save(conn, "two", session_key="s")
    assert out["status"] == "buffered" and out["message"] == SAVE_MSG.format(n=2, need=5)


def test_the_full_search_takes_over_at_the_count(conn):
    s = _cold(3)
    for t in ("one", "two", "three"):
        s.save(conn, t, session_key="s")
    out = s.retrieve(conn, "one", 5)
    assert out["status"] == "ok" and "early" not in out and "message" not in out


@pytest.mark.embed
def test_early_search_finds_a_memory_by_meaning_with_the_real_embedder(conn):
    from agent_memory.embed.nomic import NomicEmbedder
    s = LiveStore(NomicEmbedder(), mode="bootstrap", bootstrap_n=50, fit_fn=stub_fit)
    s.save(conn, "The staging database listens on port 6543.", session_key="s", memory_id="db")
    s.save(conn, "Buttons use the green #15803D for success messages.", session_key="s", memory_id="ui")
    s.save(conn, "Nightly backups run at 02:00 and keep 14 days.", session_key="s", memory_id="bk")
    out = s.retrieve(conn, "which port is staging postgres on?", 3)
    assert out["results"][0]["memory_id"] == "db", out["results"]


def test_a_conflict_offers_only_the_standing_versions_like_the_full_search(conn):
    """old <- mid <- new and old <- other: `mid` is itself corrected, so it is not a candidate (resolve_standing)."""
    s = _cold(10)
    s.save(conn, "v1", session_key="s", memory_id="old")
    s.save(conn, "v2", session_key="s", memory_id="mid", supersedes="old")
    s.save(conn, "v2b", session_key="s", memory_id="other", supersedes="old")
    s.save(conn, "v3", session_key="s", memory_id="new", supersedes="mid")
    old = next(r for r in s.retrieve(conn, "v1", 10)["results"] if r["memory_id"] == "old")
    assert [c["memory_id"] for c in old["superseded_by"]["candidates"]] == ["new", "other"]
