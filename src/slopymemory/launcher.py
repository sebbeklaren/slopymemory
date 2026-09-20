"""`slopymem-mcp`: the one MCP server every harness registers. Spawned per session in the project's
directory; resolves the directory to a store; starts the store's server on demand and WAITS for it;
bridges stdio ↔ the server's streamable HTTP. Holds no session state of its own. With no store
resolved it offers exactly one tool, `memory_init`, and switches to the store once it is provisioned."""
from __future__ import annotations
import asyncio
import base64
import os
import shlex
import sys
from pathlib import Path
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from . import server as srv
from .provision import TEMPLATE_HINT, InitRefused, SystemPostgres, apply_init, plan_init
from .paths import ConfigError
from .registry import Registry
from .store import Store

INIT_DESCRIPTION = (
    "This project has no memory store. Calling this creates one: a database and a state directory "
    "under ~/.slopymemory and a registry entry for this directory. Ask the user before calling it. "
    "name: defaults to the directory's name. dialect: 'coding' (default) or 'design'.")


TEST_HOOKS = ("SLOPYMEM_FAKE_PG", "SLOPYMEM_CWD", "SLOPYMEM_SERVER_CMD")   # env-driven fakes the tests inject


class _FakePg:  # tests only (SLOPYMEM_FAKE_PG=1|cannot|broken): no database is created
    def database_exists(self, n): return False
    def create_database(self, n): pass
    def has_pgvector(self): return True

    def can_provision(self):
        mode = os.environ.get("SLOPYMEM_FAKE_PG")
        if mode == "broken":
            raise RuntimeError("psql: connection refused")
        return mode != "cannot"

    def describe(self): return "fake"


def _cause(e: BaseException) -> BaseException:
    """anyio re-raises whatever leaves a task group inside an ExceptionGroup ("unhandled errors in a
    TaskGroup (1 sub-exception)"); the message the harness shows must be the cause, not the wrapper."""
    while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
        e = e.exceptions[0]
    return e


class Launcher:
    def __init__(self) -> None:
        self.cwd = Path(os.environ.get("SLOPYMEM_CWD", os.getcwd()))
        self.store: Store | None = None
        self.upstream: ClientSession | None = None
        self.ready = asyncio.Event()
        self.failure: str | None = None
        self.closing = asyncio.Event()
        self.connect_task: asyncio.Task | None = None
        self.server = Server("memory")
        self._register_handlers()
        if active := [h for h in TEST_HOOKS if os.environ.get(h)]:
            # Never silent: with SLOPYMEM_FAKE_PG set, memory_init reports a store "created" with no
            # database behind it. A stray variable in a user's shell must announce itself.
            print(f"slopymem-mcp: TEST HOOK active: {', '.join(active)}", file=sys.stderr)
        if cmd := os.environ.get("SLOPYMEM_SERVER_CMD"):      # tests: a fake upstream
            srv.server_command = lambda store, _c=shlex.split(cmd): _c

    def _pg(self):
        return _FakePg() if os.environ.get("SLOPYMEM_FAKE_PG") else SystemPostgres()

    def resolve(self) -> Store | None:
        name = Registry.load().resolve(self.cwd)
        return Store.load(name) if name else None

    def _start_connecting(self, store: Store) -> None:
        """Run `connect()` as its OWN top-level task, never inline inside a request handler's task.
        anyio requires whatever task OPENS a cancel scope to be the one that CLOSES it; the upstream
        connection must outlive the single request (or the startup handshake) that triggers it, so it
        cannot be opened from within that request's own task and closed later from a different one
        (that raised "Attempted to exit a cancel scope in a different task than it was entered in" —
        found running this against the SDK's real streamablehttp_client/ClientSession task groups).
        This task instead holds the connection open itself until `self.closing` is set at shutdown."""
        self.store = store                     # known from here; `self.upstream` is the readiness signal
        self.connect_task = asyncio.create_task(self.connect(store))

    async def connect(self, store: Store) -> None:
        """Start the server if needed, wait for it, open the upstream session, then hold it open
        until shutdown — opened and closed by this same task. Errors are kept, not raised: the
        handshake with the harness has already been answered; tool calls report the failure."""
        try:
            await asyncio.to_thread(srv.ensure_up, store)
            async with streamablehttp_client(f"http://127.0.0.1:{store.port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    try:                        # the port answering HTTP is not the handshake completing
                        await asyncio.wait_for(session.initialize(), srv.DEADLINE_S)
                    except TimeoutError:
                        raise srv.ServerNotUp(
                            f"store {store.name}: the server on port {store.port} answers HTTP but did not complete "
                            f"the MCP handshake within {srv.DEADLINE_S:.0f}s — log: {store.log_file()} — see SETUP.md#servers")
                    self.upstream = session
                    self.ready.set()
                    await self.closing.wait()          # keep the connection open until the launcher exits
        except Exception as e:             # any failure is reported through the tools, never swallowed
            e = _cause(e)
            # ServerNotUp already names the store, its log file and an anchor (see server.py); any
            # other exception (e.g. an httpx ConnectError from the streamable-http handshake) does not,
            # so it gets the same anchor here rather than surfacing as a bare, uninvestigable message.
            self.failure = f"{e}" if isinstance(e, srv.ServerNotUp) else (
                f"store {store.name}: {e} — log: {store.log_file()} — see SETUP.md#servers")
            print(f"slopymem-mcp: {self.failure}", file=sys.stderr)
            self.ready.set()

    def _register_handlers(self) -> None:
        s = self.server

        @s.list_tools()
        async def list_tools() -> list[types.Tool]:
            await self.ready.wait()
            if self.upstream is None:
                if self.failure:
                    raise RuntimeError(self.failure)
                description = INIT_DESCRIPTION
                # can_provision() shells out to psql; with Postgres down or psql missing it can
                # raise (check=True) instead of returning False. That must not take memory_init
                # off the list — the whole point of the tool is to be there to explain the
                # blocker — so the check itself is guarded, and run off the event loop.
                try:
                    ok = await asyncio.to_thread(self._pg().can_provision)
                    why = ""
                except Exception as e:
                    ok, why = False, f"{e}"
                if not ok:
                    description += " NOTE: this machine cannot provision a store yet — " + TEMPLATE_HINT
                    if why:
                        description += f" (check failed: {why})"
                return [types.Tool(name="memory_init", description=description, inputSchema={
                    "type": "object",
                    "properties": {"name": {"type": ["string", "null"]},
                                   "dialect": {"type": "string", "enum": ["coding", "design"], "default": "coding"}}})]
            return (await self.upstream.list_tools()).tools

        @s.call_tool()
        async def call_tool(name: str, arguments: dict | None):
            await self.ready.wait()
            if self.upstream is None:
                if name != "memory_init":
                    raise RuntimeError(self.failure or f"no memory store for {self.cwd} — call memory_init first — see SETUP.md#registry")
                return await self._init(arguments or {})
            # The lowlevel Server.call_tool() decorator forwards a returned types.CallToolResult
            # whole (isError + structuredContent preserved) — verified against mcp 1.27.1's source,
            # which special-cases `isinstance(results, types.CallToolResult)` ahead of its own
            # content-list/dict normalization. So the upstream result is passed straight through
            # rather than unpacked into `.content` and re-raised on `.isError`.
            return await self.upstream.call_tool(name, arguments or {})

        @s.list_prompts()
        async def list_prompts() -> list[types.Prompt]:
            await self.ready.wait()
            if self.upstream is None:
                if self.failure:                # the same message on every surface the harness may read first
                    raise RuntimeError(self.failure)
                return []
            return (await self.upstream.list_prompts()).prompts

        @s.list_resources()
        async def list_resources() -> list[types.Resource]:
            await self.ready.wait()
            if self.upstream is None:
                if self.failure:
                    raise RuntimeError(self.failure)
                return []
            return (await self.upstream.list_resources()).resources

        @s.read_resource()
        async def read_resource(uri):
            await self.ready.wait()
            if self.upstream is None:
                raise RuntimeError(self.failure or f"no memory store for {self.cwd} — call memory_init first — see SETUP.md#registry")
            # mcp 1.27.1's read_resource decorator (read from lowlevel/server.py) does not accept a
            # ReadResourceResult or a plain list of TextResourceContents/BlobResourceContents back —
            # only str, bytes, or Iterable[ReadResourceContents] (mcp.server.lowlevel.helper_types).
            # So the upstream's ReadResourceResult.contents is unpacked into that shape; a blob's
            # base64 text is decoded back to bytes so the decorator re-encodes it as a blob again
            # rather than as text.
            result = await self.upstream.read_resource(uri)
            contents = []
            for c in result.contents:
                data = base64.b64decode(c.blob) if isinstance(c, types.BlobResourceContents) else c.text
                contents.append(ReadResourceContents(content=data, mime_type=c.mimeType, meta=c.meta))
            return contents

    async def _init(self, args: dict) -> list[types.TextContent]:
        pg = self._pg()
        try:
            plan = plan_init(self.cwd, args.get("name"), args.get("dialect", "coding"), pg)
            store = apply_init(plan, pg)
        except InitRefused as e:
            raise RuntimeError(str(e)) from e
        self.ready.clear()
        self._start_connecting(store)          # own task — see _start_connecting's docstring
        await self.ready.wait()
        if self.upstream is None:
            raise RuntimeError(self.failure or "the store's server did not come up — see SETUP.md#servers")
        await self.server.request_context.session.send_tool_list_changed()
        return [types.TextContent(type="text", text=f"created store {store.name} for {self.cwd} "
                                  f"(database {store.database}, port {store.port}); the memory tools are now available")]

    async def run(self) -> None:
        try:
            store = self.resolve()
        except ConfigError as e:                   # a hand-edited registry/store.toml with a typo: the
            self.failure = str(e)                  # handshake is still answered; the tools raise the message
            self.ready.set()
            print(f"slopymem-mcp: {self.failure}", file=sys.stderr)
            store = None
        if store is not None:
            self._start_connecting(store)           # handshake first, the wait happens under the tools
        elif self.failure is None:
            self.ready.set()                       # init mode: answer immediately
        try:
            async with stdio_server() as (read, write):
                # tools_changed=True: _init sends notifications/tools/list_changed once a store is
                # provisioned, so the capability advertised at initialize must say the tool list can
                # change — the default (False) would have every harness believe list_tools is static.
                init_options = self.server.create_initialization_options(NotificationOptions(tools_changed=True))
                await self.server.run(read, write, init_options)
        finally:
            self.closing.set()
            if self.connect_task is not None:
                try:                            # bounded: a wedged upstream must not keep the launcher alive
                    await asyncio.wait_for(self.connect_task, srv.DEADLINE_S)
                except TimeoutError:
                    name = self.store.name if self.store else "?"
                    print(f"slopymem-mcp: store {name}: the upstream connection did not close within "
                          f"{srv.DEADLINE_S:.0f}s; leaving it — see SETUP.md#servers", file=sys.stderr)


def main() -> None:
    asyncio.run(Launcher().run())


if __name__ == "__main__":
    main()
