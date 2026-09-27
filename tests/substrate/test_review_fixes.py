"""What the whole-branch review found: the documented recovery step, a crash while warming, the report's home path,
a database that hangs, and a disk error after the save is already safe."""
import dataclasses
import json
from pathlib import Path

import psycopg

import agent_memory.durability as du
from agent_memory.mcp import server
from agent_memory.spaces.live_store_state import LiveStore
from tests_support_fakes import Emb, srv, down, up, stub_fit  # noqa: F401  (fixture)


def _rows(conn, text):
    with conn.cursor() as cur:
        cur.execute("SELECT memory_id FROM m3_memory WHERE text = %s", (text,))
        return [r[0] for r in cur.fetchall()]


def test_a_failed_record_moved_back_into_held_drains(srv, conn, monkeypatch):
    down(monkeypatch)
    server.memory_save(tenant="t", text="retry me")
    [p] = du.held_paths()
    du.fail(p, "CheckViolation: x")                                  # what a failed drain leaves
    failed = next((du.state_dir() / "held-failed").glob("*.json"))
    failed.rename(du.state_dir() / "held" / failed.name)            # the documented fix: move it back
    up(monkeypatch)
    out = server.memory_retrieve(tenant="t", query="q")
    assert out["status"] == "ok" and out["recovered"]["written"] == 1 and _rows(conn, "retry me")


def test_a_held_file_of_the_wrong_shape_goes_to_failed_and_blocks_nothing(srv, monkeypatch):
    held = du.state_dir() / "held"
    held.mkdir(parents=True, exist_ok=True)
    (held / "00000000000000000001-a.json").write_text("[1, 2]")
    (held / "00000000000000000002-b.json").write_text(json.dumps({"text": "no id"}))
    out = server.memory_retrieve(tenant="t", query="q")
    assert out["status"] == "ok" and du.counts() == (0, 2)
    assert server.memory_save(tenant="t", text="still works")["status"] == "saved"


def test_a_record_already_in_the_warm_up_buffer_is_not_buffered_twice(conn, tmp_path, monkeypatch):
    s = LiveStore(Emb(), mode="bootstrap", bootstrap_n=5, fit_fn=stub_fit)
    s.save(conn, "one", session_key="s", memory_id="a")
    out = s.save(conn, "one", session_key="s", memory_id="a")          # the same record again, e.g. from held/
    assert out["status"] == "buffered" and [b[0] for b in s._buffer] == ["a"]


def test_the_drain_releases_a_record_already_in_the_warm_up_buffer(srv, monkeypatch):
    cold = LiveStore(Emb(), mode="bootstrap", bootstrap_n=5, fit_fn=stub_fit)
    monkeypatch.setattr(server, "_store", cold)
    down(monkeypatch)
    mid = server.memory_save(tenant="t", text="buffered then crashed")["memory_id"]
    up(monkeypatch)
    cold._buffer.append((mid, "buffered then crashed", "s", None, None, None, None, None, None))   # recovered from WAL
    server.memory_retrieve(tenant="t", query="q")
    assert du.held_paths() == [] and [b[0] for b in cold._buffer].count(mid) == 1


def test_the_report_shows_the_home_directory_as_a_tilde(monkeypatch, tmp_path):
    from slopymemory import checks
    monkeypatch.setattr(checks, "_report_lines", lambda: [f'error on socket "{Path.home()}/.slopymemory/pg/.s.PGSQL.5432"'])
    out = checks.report()
    assert str(Path.home()) not in out and '"~/.slopymemory/pg/.s.PGSQL.5432"' in out


def test_connections_carry_a_statement_timeout_so_a_hanging_database_answers(srv, monkeypatch):
    seen = {}

    def spy(*a, **k):
        seen.update(k)
        raise psycopg.OperationalError("refused")
    monkeypatch.setattr(server.psycopg, "connect", spy)
    assert server.memory_save(tenant="t", text="x")["status"] == "held"
    assert seen["connect_timeout"] == 3 and "statement_timeout" in seen["options"]


def test_a_status_file_that_cannot_be_written_still_answers_held(srv, monkeypatch):
    down(monkeypatch)

    def full(state):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(du, "write_status", full)
    out = server.memory_save(tenant="t", text="keep me")
    assert out["status"] == "held" and du.held_paths()


def test_a_status_file_that_cannot_be_written_after_a_commit_still_answers_saved(srv, monkeypatch):
    def full(state):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(server, "_state", {"schema_ready": True, "failing": True})
    monkeypatch.setattr(du, "write_status", full)
    out = server.memory_save(tenant="t", text="committed")
    assert out["status"] == "saved" and du.held_paths() == []
