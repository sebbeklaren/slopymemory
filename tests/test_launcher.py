import asyncio, os, sys
from pathlib import Path
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from slopymemory.registry import Registry
from slopymemory.store import Store
from slopymemory import server

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")


def launcher_params(cwd: Path, tmp_home: Path, **extra_env) -> StdioServerParameters:
    env = {**os.environ, "SLOPYMEM_HOME": str(tmp_home), "SLOPYMEM_CWD": str(cwd),
           "SLOPYMEM_SERVER_CMD": f"{sys.executable} {FAKE}", **extra_env}
    return StdioServerParameters(command=sys.executable, args=["-m", "slopymemory.launcher"], env=env)


async def test_bridges_to_the_stores_server_starting_it_on_demand(tmp_home, tmp_path, free_port):
    repo = tmp_path / "repo"; repo.mkdir()
    Store(name="repo", dialect="coding", port=free_port, database="x", postgres="system").save()
    r = Registry.load(); r.link(repo, "repo"); r.save()
    params = launcher_params(repo, tmp_home)
    try:
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()                       # answered before the server is up
                names = [t.name for t in (await s.list_tools()).tools]
                assert names == ["memory_ping"]
                res = await s.call_tool("memory_ping", {"text": "hi"})
                assert res.content[0].text == "pong:hi"
        assert server.probe(free_port), "the server outlives the session"
    finally:
        server.stop(Store.load("repo"))


async def test_init_mode_offers_memory_init_then_switches_to_the_store(tmp_home, tmp_path):
    repo = tmp_path / "fresh"; repo.mkdir()
    params = launcher_params(repo, tmp_home, SLOPYMEM_FAKE_PG="1")  # provisioning without a real database
    try:
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                assert [t.name for t in tools] == ["memory_init"]
                assert "Ask the user before calling it" in tools[0].description
                res = await s.call_tool("memory_init", {"name": "fresh", "dialect": "coding"})
                assert "created store fresh" in res.content[0].text
                names = [t.name for t in (await s.list_tools()).tools]
                assert names == ["memory_ping"]
        assert Registry.load().resolve(repo) == "fresh"
    finally:
        if Store.exists("fresh"):
            server.stop(Store.load("fresh"))


async def test_when_the_machine_cannot_provision_list_tools_notes_it_and_init_fails(tmp_home, tmp_path):
    """Controller ruling (task-5 fact 3): a machine that cannot provision must say so at list_tools
    time, before any call — and memory_init must still refuse, pointing at the same doc anchor."""
    repo = tmp_path / "cannot"; repo.mkdir()
    params = launcher_params(repo, tmp_home, SLOPYMEM_FAKE_PG="cannot")
    try:
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                assert [t.name for t in tools] == ["memory_init"]
                assert "slopymem_template" in tools[0].description
                res = await s.call_tool("memory_init", {"name": "cannot", "dialect": "coding"})
                assert res.isError
                assert "SETUP.md#postgres" in res.content[0].text
    finally:
        if Store.exists("cannot"):
            server.stop(Store.load("cannot"))
