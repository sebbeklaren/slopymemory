import os, sys
from pathlib import Path
import pytest
import mcp.types as types
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
    errlog = tmp_path / "launcher.stderr"
    try:
        with open(errlog, "w") as err:
            async with stdio_client(params, errlog=err) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()                       # answered before the server is up
                    names = [t.name for t in (await s.list_tools()).tools]
                    assert names == ["memory_ping"]
                    res = await s.call_tool("memory_ping", {"text": "hi"})
                    assert res.content[0].text == "pong:hi"
        assert server.probe(free_port), "the server outlives the session"
        # The env-driven fakes this test relies on are production code paths; a launcher running with
        # any of them set must say so on stderr, once, so a stray variable in a user's shell is never silent.
        hook_lines = [l for l in errlog.read_text().splitlines() if "TEST HOOK active" in l]
        assert len(hook_lines) == 1 and "SLOPYMEM_CWD" in hook_lines[0] and "SLOPYMEM_SERVER_CMD" in hook_lines[0]
        assert "SLOPYMEM_FAKE_PG" not in hook_lines[0]
    finally:
        server.stop(Store.load("repo"))


async def test_init_mode_offers_memory_init_then_switches_to_the_store(tmp_home, tmp_path):
    repo = tmp_path / "fresh"; repo.mkdir()
    params = launcher_params(repo, tmp_home, SLOPYMEM_FAKE_PG="1")  # provisioning without a real database
    notifications = []

    async def on_message(message):
        if isinstance(message, types.ServerNotification):
            notifications.append(message.root)

    try:
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr, message_handler=on_message) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                assert [t.name for t in tools] == ["memory_init"]
                assert "Ask the user before calling it" in tools[0].description
                res = await s.call_tool("memory_init", {"name": "fresh", "dialect": "coding"})
                assert "created store fresh" in res.content[0].text
                names = [t.name for t in (await s.list_tools()).tools]
                assert names == ["memory_ping"]
        assert Registry.load().resolve(repo) == "fresh"
        # fix round 1, item 1: the launcher must advertise tools.listChanged=True (the capability
        # negotiated at initialize) since memory_init sends exactly this notification.
        assert any(isinstance(n, types.ToolListChangedNotification) for n in notifications)
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


async def test_a_failing_provisioning_check_still_lists_memory_init_with_the_reason(tmp_home, tmp_path):
    """Fix round 1, item 2: can_provision() can raise (psql down, missing binary, ...) instead of
    returning False. That must not drop memory_init off the list — it's the only way out."""
    repo = tmp_path / "broken"; repo.mkdir()
    params = launcher_params(repo, tmp_home, SLOPYMEM_FAKE_PG="broken")
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            assert [t.name for t in tools] == ["memory_init"]
            assert "slopymem_template" in tools[0].description
            assert "connection refused" in tools[0].description


async def test_memory_init_reports_psqls_own_reason(tmp_home, tmp_path, broken_pg_tools):
    """With the real SystemPostgres and a psql that fails, both the NOTE on memory_init's description and
    the memory_init error must carry psql's stderr and the anchor — not 'exit status 1'."""
    repo = tmp_path / "psqldown"; repo.mkdir()
    params = launcher_params(repo, tmp_home)
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            assert [t.name for t in tools] == ["memory_init"]
            assert "FATAL: boom" in tools[0].description and "SETUP.md#postgres" in tools[0].description
            res = await s.call_tool("memory_init", {"name": "psqldown", "dialect": "coding"})
            assert res.isError
            assert "FATAL: boom" in res.content[0].text and "SETUP.md#postgres" in res.content[0].text


async def test_a_malformed_store_or_registry_file_still_answers_the_handshake_and_names_the_anchor(tmp_home, tmp_path):
    """SETUP.md tells users to hand-edit these files; a typo must not turn every launcher on the machine into a
    traceback before the handshake — the harness connects, list_tools raises the message with the anchor."""
    from mcp.shared.exceptions import McpError
    repo = tmp_path / "repo"; repo.mkdir()
    Store(name="repo", dialect="coding", port=8781, database="x", postgres="system").save()
    r = Registry.load(); r.link(repo, "repo"); r.save()
    toml = Store.dir_of("repo") / "store.toml"
    toml.write_text(toml.read_text().replace('database = "x"\n', ""))
    async with stdio_client(launcher_params(repo, tmp_home)) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            with pytest.raises(McpError) as e:
                await s.list_tools()
            assert "SETUP.md#registry" in str(e.value) and "database" in str(e.value)
            with pytest.raises(McpError, match="SETUP.md#registry"):     # every surface raises the same message
                await s.list_prompts()
            with pytest.raises(McpError, match="SETUP.md#registry"):
                await s.list_resources()
    (tmp_home / "registry.toml").write_text("link = [ { path = ")
    async with stdio_client(launcher_params(repo, tmp_home)) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            with pytest.raises(McpError) as e:
                await s.list_tools()
            assert "SETUP.md#registry" in str(e.value) and "registry.toml" in str(e.value)


@pytest.fixture
def hanging_mcp_server():
    """Answers the HTTP probe but never answers the MCP handshake — a bound port with a wedged loop."""
    import http.server, threading
    release = threading.Event()
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(406); self.end_headers()
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            release.wait()
        def log_message(self, *a): pass
    hs = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); hs.daemon_threads = True
    t = threading.Thread(target=hs.serve_forever, daemon=True); t.start()
    yield hs.server_address[1]
    release.set(); hs.shutdown(); hs.server_close()


async def test_a_server_that_never_completes_the_handshake_is_a_failure_within_the_deadline(tmp_home, tmp_path, hanging_mcp_server, monkeypatch):
    """DEADLINE_S bounds 'answers HTTP'; it must also bound the MCP handshake, or a wedged server hangs
    every tool call with no message. In-process: the deadline is shortened and connect() is awaited."""
    import time
    from slopymemory.launcher import Launcher
    repo = tmp_path / "repo"; repo.mkdir()
    Store(name="hang", dialect="coding", port=hanging_mcp_server, database="x", postgres="system").save()
    r = Registry.load(); r.link(repo, "hang"); r.save()
    monkeypatch.setenv("SLOPYMEM_CWD", str(repo))
    monkeypatch.setattr(server, "DEADLINE_S", 0.5)
    launcher = Launcher()
    t0 = time.monotonic()
    await launcher.connect(launcher.resolve())
    assert time.monotonic() - t0 < 5
    assert launcher.ready.is_set() and launcher.upstream is None
    assert "hang" in launcher.failure and "SETUP.md#servers" in launcher.failure and "handshake" in launcher.failure
    handler = launcher.server.request_handlers[types.ListToolsRequest]
    with pytest.raises(RuntimeError, match="SETUP.md#servers"):
        await handler(types.ListToolsRequest(method="tools/list"))
