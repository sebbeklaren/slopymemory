"""The held-records queue: a save written to disk before anything is claimed, one file per record, drained in
order; failed records kept with their error; the error text cleaned of database URLs and passwords."""
import dataclasses
import json
import os
from datetime import datetime, timezone

import pytest

import agent_memory.durability as du


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(du, "settings", dataclasses.replace(du.settings, m3_buffer_path=str(tmp_path / "m3_buffer.jsonl")))
    return tmp_path


def _rec(mid, text="a decision"):
    return du.record(mid, text, "s1", None, datetime(2026, 1, 2, tzinfo=timezone.utc), "t", None, [["function", "x"]], None)


def test_hold_writes_the_whole_record_before_returning(state):
    p = du.hold(_rec("m1"))
    assert p.parent == state / "held" and p.suffix == ".json"
    assert json.loads(p.read_text())["text"] == "a decision"
    assert not list((state / "held").glob("*.tmp"))


def test_held_paths_come_back_in_the_order_they_were_held(state):
    for m in ("c", "a", "b"):
        du.hold(_rec(m))
    assert [du.load(p)["memory_id"] for p in du.held_paths()] == ["c", "a", "b"]
    assert du.held_ids() == {"a", "b", "c"}


def test_release_deletes_one_record_and_fail_keeps_it_with_its_error(state):
    a, b = du.hold(_rec("a")), du.hold(_rec("b"))
    du.release(a)
    du.fail(b, "ValueError: bad facet")
    assert du.held_paths() == [] and du.counts() == (0, 1)
    kept = json.loads(next((state / "held-failed").glob("*.json")).read_text())
    assert kept["record"]["memory_id"] == "b" and kept["error"] == "ValueError: bad facet" and kept["failed_at"]
    assert du.failed_errors() == ["ValueError: bad facet"]


def test_hold_fails_loudly_when_the_directory_cannot_be_written(state):
    (state / "held").mkdir()
    os.chmod(state / "held", 0o500)
    try:
        with pytest.raises(OSError):
            du.hold(_rec("x"))
    finally:
        os.chmod(state / "held", 0o700)


def test_status_file_round_trips(state):
    du.write_status({"state": "failing", "error": "OperationalError: refused"})
    assert du.read_status(state)["state"] == "failing"


@pytest.mark.parametrize("msg", [
    "connection to postgresql://u:" + "pw@host/db failed\nDETAIL: more",
    "could not connect: host=/tmp dbname=x password=" + "s3cret port=5",
    "could not connect: password='" + "a b c' host=x",
])
def test_clean_error_keeps_the_class_and_first_line_and_hides_urls_and_passwords(msg):
    out = du.clean_error(RuntimeError(msg))
    assert out.startswith("RuntimeError: ") and "\n" not in out
    assert "pw@" not in out and "s3cret" not in out and "a b c" not in out


def test_substrate_tests_never_write_state_into_the_checkout():
    """Every substrate test runs with the save path's files in a temporary directory: a held record or a buffer
    left in the working tree would be read by the next run's drain, and could be committed."""
    from pathlib import Path
    assert not du.state_dir().resolve().is_relative_to(Path(__file__).resolve().parents[2])


def test_a_clock_stepping_backwards_never_puts_a_later_save_before_an_earlier_one(state, monkeypatch):
    times = iter([2_000_000_000_000_000_000, 1_000_000_000_000_000_000])      # the second save's clock went back
    monkeypatch.setattr(du.time, "time_ns", lambda: next(times))
    du.hold(_rec("first")); du.hold(_rec("second"))
    assert [du.load(p)["memory_id"] for p in du.held_paths()] == ["first", "second"]
