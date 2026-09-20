import asyncio, os, sys
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
                    assert names == ["memory_ping", "memory_session"]
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
                assert names == ["memory_ping", "memory_session"]
        assert Registry.load().resolve(repo) == "fresh"
        # The launcher must advertise tools.listChanged=True at initialize: memory_init sends exactly this
        # notification, and a harness told the list is static may ignore it.
        assert any(isinstance(n, types.ToolListChangedNotification) for n in notifications)
    finally:
        if Store.exists("fresh"):
            server.stop(Store.load("fresh"))


async def test_when_the_machine_cannot_provision_list_tools_notes_it_and_init_fails(tmp_home, tmp_path):
    """A machine that cannot provision must say so at list_tools time, before any call — and memory_init
    must still refuse, pointing at the same doc anchor."""
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
    """can_provision() can raise (psql down, missing binary, ...) instead of returning False. That must not
    drop memory_init off the list — it is the only way out."""
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
    """With the real SystemPostgres and a psql that fails, both memory_init's description and the memory_init
    error must carry psql's stderr and the anchor — not 'exit status 1'. The description carries it whether
    the machine then falls back to the embedded Postgres (the reason the system one was passed over) or has
    nothing to fall back to; the call asks for the system one, so it is the refusal that answers."""
    repo = tmp_path / "psqldown"; repo.mkdir()
    params = launcher_params(repo, tmp_home)
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            assert [t.name for t in tools] == ["memory_init"]
            assert "FATAL: boom" in tools[0].description and "SETUP.md#postgres" in tools[0].description
            assert "postgres" in tools[0].inputSchema["properties"]
            res = await s.call_tool("memory_init", {"name": "psqldown", "dialect": "coding", "postgres": "system"})
            assert res.isError
            assert "FATAL: boom" in res.content[0].text and "SETUP.md#postgres" in res.content[0].text
    assert not Store.exists("psqldown") and not (tmp_home / "pg").exists()


async def test_memory_init_says_which_backend_it_will_use(tmp_home, tmp_path):
    repo = tmp_path / "fresh"; repo.mkdir()
    params = launcher_params(repo, tmp_home, SLOPYMEM_FAKE_PG="1")
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            assert "system Postgres" in tools[0].description and "NOTE" not in tools[0].description


@pytest.mark.pg
async def test_memory_init_on_the_embedded_postgres_starts_it_and_creates_the_database(tmp_home, tmp_path):
    """The whole path, live: memory_init asked for the embedded backend starts a real cluster under the test
    home, creates the store's database with pgvector in it, and the store's server (the fake here) is spawned
    after it. Both are stopped in `finally`."""
    from slopymemory.embedded_pg import EmbeddedPostgres
    repo = tmp_path / "emb"; repo.mkdir()
    params = launcher_params(repo, tmp_home)
    epg = EmbeddedPostgres(tmp_home / "pg")
    try:
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                res = await s.call_tool("memory_init", {"name": "emb", "dialect": "coding", "postgres": "embedded"})
                assert not res.isError, res.content[0].text
                assert "created store emb" in res.content[0].text and "embedded Postgres" in res.content[0].text
                names = [t.name for t in (await s.list_tools()).tools]
                assert names == ["memory_ping", "memory_session"]
        st = Store.load("emb")
        assert st.postgres == "embedded" and st.server_env()["DATABASE_URL"].startswith(f"host={tmp_home / 'pg'} ")
        assert epg.is_running() and epg.database_exists("emb_memory")
        import psycopg
        with psycopg.connect(st.server_env()["DATABASE_URL"]) as c:
            assert c.execute("select 1 from pg_extension where extname = 'vector'").fetchone() == (1,)
    finally:
        if Store.exists("emb"):
            server.stop(Store.load("emb"))
        epg.stop()
    assert not epg.is_running()


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


async def test_a_server_that_exits_at_once_is_reported_through_the_tools_at_once(tmp_home, tmp_path, free_port):
    """Through the bridge: the store's server command exits 3 immediately; list_tools must raise the exit
    code, the log tail and the anchor well before DEADLINE_S, not hang for a minute."""
    import time
    from mcp.shared.exceptions import McpError
    repo = tmp_path / "repo"; repo.mkdir()
    Store(name="repo", dialect="coding", port=free_port, database="x", postgres="system").save()
    r = Registry.load(); r.link(repo, "repo"); r.save()
    params = launcher_params(repo, tmp_home, SLOPYMEM_SERVER_CMD=f"{sys.executable} -c \"import sys; print('FATAL: no such database'); sys.exit(3)\"")
    t0 = time.monotonic()
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            with pytest.raises(McpError) as e:
                await s.list_tools()
    assert time.monotonic() - t0 < 15
    msg = str(e.value)
    assert "exited with code 3" in msg and "FATAL: no such database" in msg and "SETUP.md#servers" in msg


# --- the store's server restarted or crashed under an open session -------------------------------------
# Observed against the SDK with the fake server killed mid-session: a new server on the same port answers
# the stale session id with 404, which the transport turns into McpError('Session terminated') on every call
# (the harness shows "connected" while nothing works); with no server on the port the first call's POST fails
# inside the transport, its task group crashes, and that call HANGS (the SDK's pending-request cleanup is
# itself cancelled) while later calls raise anyio.ClosedResourceError. The launcher must serve the next
# call in every one of these states, or fail loud — never hang, never stay dead.

def _linked_store(tmp_path: Path, port: int) -> tuple[Path, Store]:
    repo = tmp_path / "repo"; repo.mkdir()
    store = Store(name="repo", dialect="coding", port=port, database="x", postgres="system"); store.save()
    r = Registry.load(); r.link(repo, "repo"); r.save()
    return repo, store


def _reconnect_lines(errlog: Path) -> list[str]:
    return [l for l in errlog.read_text().splitlines() if "server session lost — reconnecting" in l]


async def _session_seen_by_the_server(s: ClientSession) -> str:
    res = await s.call_tool("memory_session", {})
    assert not res.isError, res.content[0].text
    return res.content[0].text


async def test_a_server_restarted_under_the_session_is_reconnected_on_the_next_call(tmp_home, tmp_path, free_port, monkeypatch):
    """`slopymem stop && slopymem start` while a harness session is open: the next call goes through, and
    stderr says so exactly once."""
    repo, store = _linked_store(tmp_path, free_port)
    errlog = tmp_path / "launcher.stderr"
    monkeypatch.setattr(server, "server_command", lambda s: [sys.executable, FAKE])   # for the restart below
    try:
        with open(errlog, "w") as err:
            async with stdio_client(launcher_params(repo, tmp_home), errlog=err) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    first = await _session_seen_by_the_server(s)
                    assert server.stop(store)                       # killed under the open session...
                    server.ensure_up(store)                         # ...and a NEW server on the same port
                    res = await s.call_tool("memory_ping", {"text": "again"})
                    assert not res.isError, res.content[0].text
                    assert res.content[0].text == "pong:again"
                    assert await _session_seen_by_the_server(s) != first
                    assert [t.name for t in (await s.list_tools()).tools] == ["memory_ping", "memory_session"]
        assert len(_reconnect_lines(errlog)) == 1, errlog.read_text()
        assert "store repo" in _reconnect_lines(errlog)[0]
    finally:
        server.stop(store)


async def test_a_server_crashed_under_the_session_is_restarted_and_reconnected_on_the_next_call(tmp_home, tmp_path, free_port):
    """A crash (nothing on the port any more): the next call brings the server back through ensure_up and is
    served — this is the shape where the SDK would otherwise hang the call forever."""
    repo, store = _linked_store(tmp_path, free_port)
    errlog = tmp_path / "launcher.stderr"
    try:
        with open(errlog, "w") as err:
            async with stdio_client(launcher_params(repo, tmp_home), errlog=err) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    res = await s.call_tool("memory_ping", {"text": "one"})
                    assert res.content[0].text == "pong:one"
                    assert server.stop(store)
                    assert not server.probe(store.port)
                    res = await asyncio.wait_for(s.call_tool("memory_ping", {"text": "two"}), 30)
                    assert not res.isError, res.content[0].text
                    assert res.content[0].text == "pong:two"
        assert server.probe(store.port), "the reconnect started the server again"
        assert len(_reconnect_lines(errlog)) == 1, errlog.read_text()
    finally:
        server.stop(store)


async def test_a_failed_reconnect_raises_the_failure_shape_and_the_launcher_stays_alive(tmp_home, tmp_path, free_port):
    """The server is gone and cannot come back (its command now exits at once): the call fails at once with the
    store, the log and the anchor — no hang, no loop — and the launcher is still there for the next call,
    which gets its own single attempt."""
    import time
    from mcp.shared.exceptions import McpError
    repo, store = _linked_store(tmp_path, free_port)
    errlog = tmp_path / "launcher.stderr"
    marker = tmp_path / "started-once"
    try:
        with open(errlog, "w") as err:
            async with stdio_client(launcher_params(repo, tmp_home, FAKE_MCP_ONCE=str(marker)), errlog=err) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    assert (await s.call_tool("memory_ping", {"text": "one"})).content[0].text == "pong:one"
                    assert server.stop(store)
                    t0 = time.monotonic()
                    res = await asyncio.wait_for(s.call_tool("memory_ping", {"text": "two"}), 30)
                    assert time.monotonic() - t0 < 15
                    assert res.isError
                    msg = res.content[0].text
                    assert "store repo" in msg and "exited with code 3" in msg and "SETUP.md#servers" in msg
                    with pytest.raises(McpError) as e:                 # still alive; the next call gets one attempt too
                        await asyncio.wait_for(s.list_tools(), 30)
                    assert "store repo" in str(e.value) and "SETUP.md#servers" in str(e.value)
        # one attempt per call, no loop — the first announced as a lost session, the second (no session ever
        # came of the failed attempt) as a missing connection
        assert len(_reconnect_lines(errlog)) == 1, errlog.read_text()
        assert len([l for l in errlog.read_text().splitlines() if "no server connection — trying again" in l]) == 1
    finally:
        if server.probe(store.port):
            server.stop(store)


async def test_a_healthy_session_makes_no_reconnect_attempt(tmp_home, tmp_path, free_port):
    """Control: calls on a live server all arrive on the one MCP session the launcher opened, and nothing is announced."""
    repo, store = _linked_store(tmp_path, free_port)
    errlog = tmp_path / "launcher.stderr"
    try:
        with open(errlog, "w") as err:
            async with stdio_client(launcher_params(repo, tmp_home), errlog=err) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    first = await _session_seen_by_the_server(s)
                    assert (await s.call_tool("memory_ping", {"text": "one"})).content[0].text == "pong:one"
                    assert [t.name for t in (await s.list_tools()).tools] == ["memory_ping", "memory_session"]
                    assert await _session_seen_by_the_server(s) == first
        assert _reconnect_lines(errlog) == [], errlog.read_text()
    finally:
        server.stop(store)


async def test_concurrent_calls_that_lose_the_session_share_one_reconnect(tmp_home, tmp_path, free_port, monkeypatch):
    """Parallel tool calls both find the session gone: one reconnect, both served — the second must take
    the first's fresh connection, never tear it down over its own loss of the OLD one."""
    repo, store = _linked_store(tmp_path, free_port)
    errlog = tmp_path / "launcher.stderr"
    monkeypatch.setattr(server, "server_command", lambda s: [sys.executable, FAKE])
    try:
        with open(errlog, "w") as err:
            async with stdio_client(launcher_params(repo, tmp_home), errlog=err) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    first = await _session_seen_by_the_server(s)
                    assert server.stop(store)
                    server.ensure_up(store)
                    a, b = await asyncio.gather(s.call_tool("memory_ping", {"text": "a"}),
                                                s.call_tool("memory_ping", {"text": "b"}))
                    assert [a.content[0].text, b.content[0].text] == ["pong:a", "pong:b"], (a, b)
                    second = await _session_seen_by_the_server(s)
                    assert second != first
                    assert await _session_seen_by_the_server(s) == second      # and it stayed
        assert len(_reconnect_lines(errlog)) == 1, errlog.read_text()
    finally:
        server.stop(store)


async def test_a_failure_without_text_is_named_by_its_type_on_stderr(tmp_home, tmp_path, monkeypatch, capsys):
    """anyio's stream errors (the shapes a dying connection raises) have an empty str(); both connect-task
    failure lines must then carry the type name — a line that names a blank failure is silent in spirit."""
    import anyio
    from slopymemory.launcher import Launcher
    repo = tmp_path / "repo"; repo.mkdir()
    store = Store(name="blank", dialect="coding", port=8782, database="x", postgres="system"); store.save()
    r = Registry.load(); r.link(repo, "blank"); r.save()
    monkeypatch.setenv("SLOPYMEM_CWD", str(repo))

    def broken(store):
        raise anyio.BrokenResourceError()
    monkeypatch.setattr(server, "ensure_up", broken)
    launcher = Launcher()
    await launcher.connect(store)                                  # a connection that fails
    err = capsys.readouterr().err
    line = [l for l in err.splitlines() if "store blank" in l][-1]
    assert "BrokenResourceError" in line and "SETUP.md#servers" in line, err
    assert "BrokenResourceError" in launcher.failure

    launcher = Launcher()
    launcher.closing.set()                                         # a connection released while it was closing
    await launcher.connect(store)
    err = capsys.readouterr().err
    line = [l for l in err.splitlines() if "store blank" in l][-1]
    assert "did not close cleanly (BrokenResourceError)" in line, err
