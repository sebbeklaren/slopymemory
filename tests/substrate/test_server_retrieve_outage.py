"""Retrieval says what it cannot see: unavailable while the database is down, incomplete while saves wait or failed."""
import agent_memory.durability as du
from agent_memory.mcp import server
from tests_support_fakes import srv, down  # noqa: F401  (fixture)


def test_retrieve_while_the_database_is_down_is_unavailable_and_names_the_error(srv, monkeypatch):
    down(monkeypatch)
    server.memory_save(tenant="t", text="held one")
    out = server.memory_retrieve(tenant="t", query="q")
    assert out["status"] == "unavailable" and out["results"] == [] and out["held_count"] == 1
    assert out["error"].startswith("OperationalError") and "pw@" not in out["error"]
    assert "SETUP.md#postgres" in out["message"]


def test_retrieve_says_incomplete_while_records_are_failed(srv, monkeypatch):
    held = du.state_dir() / "held"
    held.mkdir(parents=True, exist_ok=True)
    (held / "00000000000000000001-bad.json").write_text("{torn")
    first = server.memory_retrieve(tenant="t", query="q")
    second = server.memory_retrieve(tenant="t", query="q")
    for out in (first, second):
        assert out["status"] == "ok"
        assert out["incomplete"] == {"held": 0, "failed": 1} and "not searchable" in out["incomplete_message"]
        assert "SETUP.md#held" in out["incomplete_message"]


def test_retrieve_with_nothing_held_carries_no_incomplete_key(srv):
    assert "incomplete" not in server.memory_retrieve(tenant="t", query="q")


def test_the_descriptions_tell_the_agent_what_held_and_unavailable_mean():
    for tool in ("memory_save", "memory_retrieve"):
        d = server.mcp._tool_manager.get_tool(tool).description
        assert "`held` or `unavailable` means the memory database is down" in d
