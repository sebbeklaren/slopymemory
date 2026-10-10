"""The tools take files: a malformed one is refused by name before anything is held, a held save keeps its anchors
through the drain, a path is scanned for secrets, and both log lines record the files verbatim."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

import agent_memory.durability as du
from agent_memory.mcp import server
from tests_support_fakes import srv, down, up  # noqa: F401  (fixture)

H, G = "d" * 64, "e" * 64


async def _refusal(tool, args):
    with pytest.raises(ToolError) as e:
        await server.mcp.call_tool(tool, args)
    assert not isinstance(e.value, UnexpectedToolError), "the reason was hidden from the model"
    return str(e.value)


@pytest.mark.parametrize("files, needle", [
    ([{"path": "../x", "sha256": H}], "no '..'"),
    ([{"path": "a.py"}], "sha256 is required"),
    ([{"path": "a.py", "sha256": H, "line": 0}], "line must be"),
    ("a.py", "valid list"),           # the SDK's own type check answers first, naming files
])
async def test_a_malformed_save_files_is_refused_by_name_and_nothing_is_held(srv, files, needle):
    msg = await _refusal("memory_save", {"tenant": "t", "text": "x", "files": files})
    assert needle in msg, msg
    assert du.counts() == (0, 0)


async def test_a_malformed_retrieve_files_is_refused_by_name(srv):
    msg = await _refusal("memory_retrieve", {"tenant": "t", "query": "q", "files": [{"path": "/abs"}]})
    assert "relative to the project root" in msg


async def test_a_nul_in_a_path_is_refused(srv):
    msg = await _refusal("memory_save", {"tenant": "t", "text": "x", "files": [{"path": "a\x00b", "sha256": H}]})
    assert "NUL" in msg and "files" in msg


def test_a_secret_in_a_path_is_refused_as_a_secret(srv):
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    out = server.memory_save(tenant="t", text="x", files=[{"path": f"cfg/{token}.txt", "sha256": H}])
    assert out["status"] == "refused" and out["field"] == "file path"


def test_save_then_retrieve_round_trips_with_stale(srv):
    saved = server.memory_save(tenant="t", text="keep the retry loop",
                               files=[{"path": "./src/a.py", "sha256": H, "line": 3}])
    assert saved["status"] == "saved" and saved["files_anchored"] == 1
    got = server.memory_retrieve(tenant="t", query="q", files=[{"path": "src/a.py", "sha256": G}])
    hit = got["results"][0]
    assert hit["anchored"] is True and hit["files"] == [{"path": "src/a.py", "sha256": H, "line": 3, "stale": True}]


def test_a_held_save_with_files_is_drained_with_its_anchors(srv, monkeypatch):
    down(monkeypatch)
    held = server.memory_save(tenant="t", text="decided while down", files=[{"path": "src/a.py", "sha256": H}])
    assert held["status"] == "held"
    assert du.load(du.held_paths()[0])["files"] == [{"path": "src/a.py", "sha256": H}]
    up(monkeypatch)
    got = server.memory_retrieve(tenant="t", query="q", files=[{"path": "src/a.py"}])
    assert any(r.get("anchored") for r in got["results"]) and du.counts() == (0, 0)


def test_both_log_lines_record_files_verbatim(srv):
    f = [{"path": "src/a.py", "sha256": H}]
    server.memory_save(tenant="t", text="x", files=f)
    server.memory_retrieve(tenant="t", query="q", files=[{"path": "src/a.py"}])
    lines = [json.loads(l) for l in (srv / "inv.jsonl").read_text().splitlines()]
    assert lines[0]["files"] == f and lines[1]["files"] == [{"path": "src/a.py"}]
    assert lines[1]["results"][0]["anchored"] is True


def test_calls_without_files_reply_as_before(srv):
    saved = server.memory_save(tenant="t", text="plain")
    got = server.memory_retrieve(tenant="t", query="q")
    assert "files_anchored" not in saved
    assert all("files" not in r and "anchored" not in r for r in got["results"])


def test_a_held_record_from_before_anchors_drains_without_them(srv, monkeypatch):
    from datetime import datetime, timezone
    rec = du.record("pre06", "held before anchors existed", "s", None, datetime.now(timezone.utc),
                    None, None, None, None)
    del rec["files"]                                         # the shape a 0.5.x server wrote
    du.hold(rec)
    got = server.memory_retrieve(tenant="t", query="q")
    assert du.counts() == (0, 0) and "pre06" in [r["memory_id"] for r in got["results"]]


def test_a_reply_without_files_has_exactly_the_keys_it_had_before(srv):
    saved = server.memory_save(tenant="t", text="plain")
    assert set(saved) == {"status", "memory_id"}
    got = server.memory_retrieve(tenant="t", query="q")
    assert all(set(r) <= {"memory_id", "score", "text", "related", "superseded_by"} for r in got["results"])


def test_the_retrieve_log_marks_anchored_only_when_files_were_asked_for(srv):
    server.memory_save(tenant="t", text="x", files=[{"path": "a.py", "sha256": H}])
    server.memory_retrieve(tenant="t", query="q")
    line = json.loads((srv / "inv.jsonl").read_text().splitlines()[-1])
    assert all("anchored" not in r for r in line["results"])
