"""`slopymem-mcp`: the one MCP server every harness registers. Spawned per session in the project's
directory; resolves the directory to a store; starts the store's server on demand and WAITS for it;
bridges stdio ↔ the server's streamable HTTP. Holds no session state of its own. With no store
resolved it offers exactly one tool, `memory_init`, and switches to the store once it is provisioned."""
from __future__ import annotations
import asyncio
import os
import shlex
import sys
from pathlib import Path
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from . import server as srv
from .provision import TEMPLATE_HINT, InitRefused, SystemPostgres, apply_init, plan_init
from .registry import Registry
from .store import Store

INIT_DESCRIPTION = (
    "This project has no memory store. Calling this creates one: a database and a state directory "
    "under ~/.slopymemory and a registry entry for this directory. Ask the user before calling it. "
    "name: defaults to the directory's name. dialect: 'coding' (default) or 'design'.")


class _FakePg:  # tests only (SLOPYMEM_FAKE_PG=1|cannot): no database is created
    def database_exists(self, n): return False
    def create_database(self, n): pass
    def has_pgvector(self): return True
    def can_provision(self): return os.environ.get("SLOPYMEM_FAKE_PG") != "cannot"
    def describe(self): return "fake"


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
        self.connect_task = asyncio.create_task(self.connect(store))

    async def connect(self, store: Store) -> None:
        """Start the server if needed, wait for it, open the upstream session, then hold it open
        until shutdown — opened and closed by this same task. Errors are kept, not raised: the
        handshake with the harness has already been answered; tool calls report the failure."""
        try:
            await asyncio.to_thread(srv.ensure_up, store)
            async with streamablehttp_client(f"http://127.0.0.1:{store.port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.upstream, self.store = session, store
                    self.ready.set()
                    await self.closing.wait()          # keep the connection open until the launcher exits
        except Exception as e:             # any failure is reported through the tools, never swallowed
            self.failure = f"{e}"
            print(f"slopymem-mcp: {e}", file=sys.stderr)
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
                if not self._pg().can_provision():
                    description += " NOTE: this machine cannot provision a store yet — " + TEMPLATE_HINT
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
            return (await self.upstream.list_prompts()).prompts if self.upstream else []

        @s.list_resources()
        async def list_resources() -> list[types.Resource]:
            await self.ready.wait()
            return (await self.upstream.list_resources()).resources if self.upstream else []

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
        store = self.resolve()
        if store is None:
            self.ready.set()                       # init mode: answer immediately
        else:
            self._start_connecting(store)           # handshake first, the wait happens under the tools
        try:
            async with stdio_server() as (read, write):
                await self.server.run(read, write, self.server.create_initialization_options())
        finally:
            self.closing.set()
            if self.connect_task is not None:
                await self.connect_task


def main() -> None:
    asyncio.run(Launcher().run())


if __name__ == "__main__":
    main()
