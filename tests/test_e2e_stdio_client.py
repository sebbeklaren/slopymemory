"""The end-to-end client (scripts/e2e_stdio.py): its assertions on canned payloads, and its whole run through the real
launcher against the fake server's canned store — no database, no model. The real thing runs in CI's container job."""
import os, sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from slopymemory import server
from slopymemory.store import Store

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import e2e_stdio as e2e  # noqa: E402

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")


# --- the assertions, on canned payloads -----------------------------------------------------------------------------

def test_init_mode_is_exactly_memory_init():
    e2e.assert_init_mode(["memory_init"])
    for tools in ([], ["memory_save", "memory_retrieve"], ["memory_init", "memory_save"]):
        with pytest.raises(e2e.Deviation, match="memory_init"):
            e2e.assert_init_mode(tools)


def test_memory_tools_need_save_and_retrieve_and_no_init():
    e2e.assert_memory_tools(["memory_save", "memory_retrieve"])
    e2e.assert_memory_tools(["memory_retrieve", "memory_save", "memory_ping"])
    for tools in (["memory_save"], ["memory_ping", "memory_session"], ["memory_init", "memory_save", "memory_retrieve"]):
        with pytest.raises(e2e.Deviation):
            e2e.assert_memory_tools(tools)


def test_buffered_and_warmed_saves():
    e2e.assert_buffered({"status": "buffered", "warming": True, "count": 3, "need": 12}, 3)
    with pytest.raises(e2e.Deviation) as ei:
        e2e.assert_buffered({"status": "buffered", "count": 2, "need": 12}, 3)       # the count did not advance
    assert ei.value.payload == {"status": "buffered", "count": 2, "need": 12}
    with pytest.raises(e2e.Deviation):
        e2e.assert_buffered({"status": "saved", "memory_id": "m3"}, 3)               # warm too early
    e2e.assert_warmed({"status": "saved", "memory_id": "m12", "warmed": True})
    with pytest.raises(e2e.Deviation):
        e2e.assert_warmed({"status": "buffered", "count": 12, "need": 12})
    with pytest.raises(e2e.Deviation):
        e2e.assert_warmed({"status": "saved"})                                        # no memory_id


def test_text_back_is_the_text_not_an_id_or_a_rank():
    ok = {"status": "ok", "results": [{"memory_id": "a", "score": 0.9, "text": "Something else entirely."},
                                      {"memory_id": "b", "score": 0.5, "text": "The nightly backup runs after the last export finishes and keeps seven copies."}]}
    hit = e2e.assert_text_back(ok, "The nightly backup runs")
    assert hit["memory_id"] == "b"
    with pytest.raises(e2e.Deviation, match="warming up"):
        e2e.assert_text_back({"status": "warming up", "results": []}, "The nightly backup runs")
    with pytest.raises(e2e.Deviation, match="non-empty"):
        e2e.assert_text_back({"status": "ok", "results": []}, "The nightly backup runs")
    with pytest.raises(e2e.Deviation, match="no result's text"):
        e2e.assert_text_back({"status": "ok", "results": [{"memory_id": "b", "score": 1.0, "text": None}]}, "The nightly backup runs")
    with pytest.raises(e2e.Deviation, match="no result's text"):
        e2e.assert_text_back({"status": "ok", "results": [{"memory_id": "b", "score": 1.0}]}, "The nightly backup runs")


def test_payload_prefers_structured_content_then_json_text_and_refuses_errors():
    text = SimpleNamespace(type="text", text='{"status": "ok", "results": []}')
    assert e2e.payload_of(SimpleNamespace(isError=False, structuredContent={"status": "saved"}, content=[text])) == {"status": "saved"}
    assert e2e.payload_of(SimpleNamespace(isError=False, structuredContent=None, content=[text])) == {"status": "ok", "results": []}
    with pytest.raises(e2e.Deviation, match="not JSON"):
        e2e.payload_of(SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(type="text", text="pong:hi")]))
    with pytest.raises(e2e.Deviation, match="error") as ei:
        e2e.payload_of(SimpleNamespace(isError=True, structuredContent=None, content=[SimpleNamespace(type="text", text="no such store")]))
    assert ei.value.payload == ["no such store"]


def test_the_twelve_memories_are_distinct_and_the_expected_one_is_among_them():
    texts = [t for t, _ in e2e.MEMORIES]
    assert len(texts) == e2e.WARM_AT == len(set(texts))
    assert any(t.startswith(e2e.EXPECTED_PREFIX) for t in texts)
    assert all(space in ("function", "semantic") for _, concepts in e2e.MEMORIES for space, _ in concepts)


# --- the whole run: the real launcher, the fake server's canned store -------------------------------------------------

LAUNCHER = f"{sys.executable} -m slopymemory.launcher"


@pytest.fixture
def canned_store(tmp_home, tmp_path, monkeypatch):
    """The launcher in init mode over a fake Postgres, its upstream the fake server with the canned memory tools.
    Yields the project directory; stops the server the launcher started."""
    project = tmp_path / "proj"; project.mkdir()
    monkeypatch.setenv("SLOPYMEM_FAKE_PG", "1")
    monkeypatch.setenv("SLOPYMEM_SERVER_CMD", f"{sys.executable} {FAKE}")
    monkeypatch.setenv("FAKE_MCP_MEMORY", "1")
    yield project
    if Store.exists("proj"):
        server.stop(Store.load("proj"))


def test_run_saves_twelve_and_gets_the_text_back(canned_store, capfd):
    assert e2e.run(canned_store, LAUNCHER) == 0
    out = capfd.readouterr().out
    assert "tools: memory_init only" in out and "created store proj" in out
    assert "save  1/12: buffered 1/12" in out and "save 11/12: buffered 11/12" in out and "save 12/12: saved" in out
    assert "the text is back: 'The nightly backup runs after the last export finishes" in out
    assert out.rstrip().splitlines()[-1].startswith("e2e: ok")


def test_run_prints_the_payload_and_fails_when_the_retrieve_stays_warming(canned_store, capfd, monkeypatch):
    monkeypatch.setenv("FAKE_MCP_RETRIEVE", "warming")
    assert e2e.run(canned_store, LAUNCHER) == 1
    out = capfd.readouterr().out
    assert "save 12/12: saved" in out                                  # the saves went through; the retrieve did not
    assert "e2e: FAILED" in out and "status ok" in out and '"warming up"' in out


def test_run_fails_when_the_store_offers_the_wrong_tools(canned_store, capfd, monkeypatch):
    monkeypatch.delenv("FAKE_MCP_MEMORY")                              # the fake's plain tools: memory_ping, memory_session
    assert e2e.run(canned_store, LAUNCHER) == 1
    out = capfd.readouterr().out
    assert "e2e: FAILED" in out and "memory_ping" in out and "expected memory_save and memory_retrieve" in out


def test_run_refuses_a_project_that_is_not_a_directory(tmp_path, capsys):
    assert e2e.run(tmp_path / "absent", LAUNCHER) == 1
    assert "not a directory" in capsys.readouterr().out


def test_default_launcher_is_the_one_beside_the_interpreter(monkeypatch, tmp_path):
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "slopymem-mcp").write_text("#!/bin/sh\n")
    monkeypatch.setattr(e2e.sys, "executable", str(fake_bin / "python"))
    assert e2e.default_launcher() == str(fake_bin / "slopymem-mcp")
    monkeypatch.setattr(e2e.sys, "executable", str(tmp_path / "elsewhere" / "python"))
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit, match="no slopymem-mcp"):
        e2e.default_launcher()
