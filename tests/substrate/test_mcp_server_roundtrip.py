# tests/test_mcp_server_roundtrip.py
import json
import os
import socket
import subprocess
import sys
import time

import anyio
import pytest

from agent_memory.config import settings


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(host: str, port: int, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"MCP server never bound {host}:{port}")


def _payload(call_result) -> dict:
    # FastMCP always includes a text content block with the JSON-serialized return.
    return json.loads(call_result.content[0].text)


async def _roundtrip(url: str) -> tuple[dict, dict]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            saved = await session.call_tool(
                "memory_save",
                {
                    "tenant": "default",
                    "text": "The build server has 64 GB of memory.",
                    "session_key": "roundtrip",
                },
            )
            got = await session.call_tool(
                "memory_retrieve",
                {"tenant": "default", "query": "how much memory does the build server have?", "k": 5},
            )
            return _payload(saved), _payload(got)


@pytest.mark.embed
def test_mcp_server_real_roundtrip(conn, tmp_path):
    # `conn` truncates the TEST db first; point the server at the same TEST db.
    # AM_M3_PROJECTOR_MODE=fixed + a fresh AM_M3_PROJECTOR_PATH bypasses the
    # 50-save bootstrap so the store is warm immediately and the single
    # save->retrieve round-trip works in-process without a stale pickle.
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": settings.test_database_url,
        "AM_MCP_PORT": str(port),
        "AM_MCP_INVOCATION_LOG": "/tmp/mcp_inv_roundtrip_test.jsonl",
        "AM_M3_PROJECTOR_MODE": "fixed",
        "AM_M3_PROJECTOR_PATH": str(tmp_path / "roundtrip_projector.pkl"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_memory.mcp.server"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        _wait_port("127.0.0.1", port)
        save, retr = anyio.run(_roundtrip, f"http://127.0.0.1:{port}/mcp")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # m3 save: status is "saved" (fixed-mode projector is warm immediately) + memory_id present
    assert save.get("status") == "saved", f"expected status=saved: {save}"
    assert save.get("memory_id"), f"expected a memory_id in save response: {save}"

    # m3 retrieve: status ok + the saved memory surfaces in the results
    assert retr.get("status") == "ok", f"expected status=ok in retrieve: {retr}"
    results = retr.get("results", [])
    assert results, f"retrieve returned no results: {retr}"
    saved_id = save["memory_id"]
    returned_ids = [r.get("memory_id") for r in results]
    assert saved_id in returned_ids, (
        f"saved memory_id {saved_id!r} not in retrieve results {returned_ids}: {retr}"
    )
    # MCP twin: the retrieved payload's result for the saved memory carries text equal to saved text (VERBATIM)
    saved_result = next(r for r in results if r["memory_id"] == saved_id)
    saved_text = "The build server has 64 GB of memory."
    assert saved_result.get("text") == saved_text, (
        f"saved memory's retrieved text {saved_result.get('text')!r} != {saved_text!r}: {saved_result}"
    )
