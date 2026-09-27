"""A save the database cannot take is held on disk and answered `held` with the real, cleaned error."""
import json

import pytest

import agent_memory.durability as du
from agent_memory.mcp import server
from tests_support_fakes import srv, down  # noqa: F401  (fixture)


def test_a_save_while_the_database_is_down_is_held_on_disk_and_says_so(srv, monkeypatch):
    down(monkeypatch)
    out = server.memory_save(tenant="t", text="keep the port at 8781")
    assert out["status"] == "held" and out["held_count"] == 1 and out["memory_id"]
    assert out["error"].startswith("OperationalError: ") and "pw@" not in out["error"]
    assert "SETUP.md#postgres" in out["message"] and "doctor --report" in out["message"]
    [p] = du.held_paths()
    assert du.load(p)["text"] == "keep the port at 8781" and du.load(p)["memory_id"] == out["memory_id"]
    log = [json.loads(line) for line in (srv / "inv.jsonl").read_text().splitlines()]
    assert log[-1]["status"] == "held" and log[-1]["memory_id"] == out["memory_id"]
    assert log[-1]["text"] == "keep the port at 8781"
    assert du.read_status(srv)["state"] == "failing"


def test_input_errors_are_answered_as_today_and_hold_nothing(srv, monkeypatch):
    down(monkeypatch)
    assert server.memory_save(tenant="t", text="   ")["status"] == "noop"
    with pytest.raises(ValueError):
        server.memory_save(tenant="t", text="x", facets={"semantic": "y"})
    assert du.held_paths() == []


def test_a_save_with_the_database_up_leaves_nothing_held_and_status_ok(srv, monkeypatch):
    down(monkeypatch)
    server.memory_save(tenant="t", text="while down")
    from tests_support_fakes import up
    up(monkeypatch)
    out = server.memory_save(tenant="t", text="a decision")
    assert out["status"] == "saved"
    assert du.read_status(srv)["state"] == "ok"
