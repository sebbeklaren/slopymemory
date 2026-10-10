"""A real store server: a save anchored to a file, the file changes, a retrieve says so."""
import os
import subprocess
import sys

import anyio
import pytest

from agent_memory.config import settings
from test_mcp_server_roundtrip import _free_port, _payload, _wait_port

OLD, NEW = "1" * 64, "2" * 64


async def _run(url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            saved = _payload(await s.call_tool("memory_save", {
                "tenant": "t", "text": "The retry loop in sync.py stays.",
                "files": [{"path": "sync.py", "sha256": OLD, "line": 40}]}))
            got = _payload(await s.call_tool("memory_retrieve", {
                "tenant": "t", "query": "anything about sync?", "files": [{"path": "sync.py", "sha256": NEW}]}))
            return saved, got


@pytest.mark.embed
def test_a_changed_file_is_marked_stale_over_the_wire(conn, tmp_path):
    port = _free_port()
    env = {**os.environ, "DATABASE_URL": settings.test_database_url, "AM_MCP_PORT": str(port),
           "AM_MCP_INVOCATION_LOG": str(tmp_path / "inv.jsonl"), "AM_M3_PROJECTOR_MODE": "fixed",
           "AM_M3_PROJECTOR_PATH": str(tmp_path / "p.pkl")}
    proc = subprocess.Popen([sys.executable, "-m", "agent_memory.mcp.server"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_port("127.0.0.1", port)
        saved, got = anyio.run(_run, f"http://127.0.0.1:{port}/mcp")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert saved["status"] == "saved" and saved["files_anchored"] == 1
    hit = next(r for r in got["results"] if r["memory_id"] == saved["memory_id"])
    assert hit["anchored"] is True
    assert hit["files"] == [{"path": "sync.py", "sha256": OLD, "line": 40, "stale": True}]
