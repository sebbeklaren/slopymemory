"""`slopymem-mcp`: the one MCP server every harness registers. Spawned per session in the project's
directory; resolves the directory to a store; starts the store's server on demand and WAITS for it;
bridges stdio ↔ the server's streamable HTTP. Holds no session state of its own. With no store
resolved it offers exactly one tool, `memory_init`, and switches to the store once it is provisioned.
A store server restarted or crashed under an open session is reconnected on the next call — the
harness keeps this process alive across that, so this process has to do it."""
from __future__ import annotations
import asyncio
import base64
import os
import shlex
import sys
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import TypeVar
import anyio
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
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


T = TypeVar("T")


class SessionLost(Exception):
    """The upstream MCP session is gone: the store's server restarted or crashed under it."""


# The transport's own error for a POST the server answered with 404: the server no longer knows the
# session id, i.e. it is a new process on the same port. Observed with the fake server killed and started
# again under an open session — every call raised McpError('Session terminated') with this code.
_SESSION_TERMINATED = 32600


def _lost(e: BaseException) -> bool:
    """Does this exception mean the upstream session is gone? Observed with the fake server killed
    mid-session: a new server on the same port → McpError 'Session terminated'; no server on the port →
    the transport's task group dies on the failed POST and every LATER call raises anyio's
    ClosedResourceError (the first one hangs instead — `_on` handles that). BrokenResourceError and
    CONNECTION_CLOSED are the SDK's other two names for the same state: the reader side gone, and the
    read stream closed under a pending request."""
    if isinstance(e, McpError):        # both are the SDK's own synthetic errors: matched by code AND text, so an
        return (e.error.code, e.error.message) in (     # upstream's genuine error with a shared code never reconnects
            (_SESSION_TERMINATED, "Session terminated"), (types.CONNECTION_CLOSED, "Connection closed"))
    return isinstance(e, (anyio.ClosedResourceError, anyio.BrokenResourceError))


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
        self.ready = asyncio.Event()           # a connect attempt has concluded (upstream set, or failure set)
        self.failure: str | None = None
        self.closing = asyncio.Event()         # releases the CURRENT upstream connection: shutdown, or a reconnect
        self.connect_task: asyncio.Task | None = None
        self._attempts = 0                     # connect attempts started; a call compares to see if one ran since it looked
        self._reconnecting = asyncio.Lock()    # concurrent callers that found the same loss share one attempt
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
        This task instead holds the connection open itself until its `closing` event is set — at
        shutdown, or by `_reconnect` when the connection has to be replaced."""
        self.store = store                     # known from here; `self.upstream` is the readiness signal
        self.closing = asyncio.Event()         # this connection's release
        self._attempts += 1
        self.connect_task = asyncio.create_task(self.connect(store, self.closing))

    def _superseded(self) -> bool:
        """Is the running connect task no longer the launcher's current one? (A replaced connection that
        was still opening, or whose close overran the deadline, must not write the launcher's state.)"""
        return self.connect_task is not None and self.connect_task is not asyncio.current_task()

    def _unreachable(self, what: str) -> str:
        store = self.store
        return f"store {store.name}: {what} — log: {store.log_file()} — see SETUP.md#servers"

    async def connect(self, store: Store, closing: asyncio.Event | None = None) -> None:
        """Start the server if needed, wait for it, open the upstream session, then hold it open
        until released — opened and closed by this same task. Errors are kept, not raised: the
        handshake with the harness has already been answered; tool calls report the failure."""
        closing = self.closing if closing is None else closing
        session = None
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
                    if not self._superseded():
                        self.upstream = session
                        self.ready.set()
                        await closing.wait()           # keep the connection open until released
        except Exception as e:             # any failure is reported through the tools, never swallowed
            e = _cause(e)
            if closing.is_set():           # released on purpose (a reconnect, or shutdown) and the close itself failed —
                failure = (f"store {store.name}: the released connection did not close cleanly "   # said, but it is
                           f"({e or type(e).__name__})")                                          # not a store failure
            else:
                # ServerNotUp already names the store, its log file and an anchor (see server.py); any
                # other exception (e.g. an httpx ConnectError from the streamable-http handshake) does not,
                # so it gets the same anchor here rather than surfacing as a bare, uninvestigable message.
                failure = f"{e}" if isinstance(e, srv.ServerNotUp) else (     # anyio's stream errors carry no text
                    f"store {store.name}: {e or type(e).__name__} — log: {store.log_file()} — see SETUP.md#servers")
            print(f"slopymem-mcp: {failure}", file=sys.stderr)
            if not self._superseded():
                self.failure = failure
                self.ready.set()
        finally:
            if self.upstream is session:   # whatever ended this task ended its session: never leave a dead one in place
                self.upstream = None

    async def _session(self) -> ClientSession | None:
        """The live upstream session, or None without a store (init mode, or a config failure — the
        handlers report those as before). With a store and no live session: a call that waited on the
        attempt in flight (the startup connect, or a reconnect another call began) takes its outcome; a
        call that finds an attempt already concluded in failure makes ONE new attempt of its own — the
        server may well be back by now — and raises the failure shape if it is not."""
        waited = not self.ready.is_set()
        await self.ready.wait()
        if self.upstream is None and self.store is not None:
            if waited:
                raise RuntimeError(self.failure or self._unreachable("no server connection"))
            await self._reconnect(self._attempts)
        return self.upstream

    async def _reconnect(self, seen: int) -> None:
        """Replace the upstream connection: release the connect task that holds it (the task that opened
        it closes it — the anyio rule), start a fresh one — `ensure_up` and the handshake, each bounded by
        DEADLINE_S exactly as at startup — and wait for its outcome: a live session, or the failure raised
        with the store, its log and the anchor. Announced on stderr once per attempt. `seen` is the
        attempt whose session the caller found lost: when a newer attempt has been made by the time the
        caller holds the lock, that attempt's outcome is the caller's — one attempt per call, never a
        loop, and never a healthy fresh connection torn down over a loss of the previous one."""
        async with self._reconnecting:
            if self._attempts == seen:
                store = self.store
                print(f"slopymem-mcp: store {store.name}: server session lost — reconnecting", file=sys.stderr)
                self.closing.set()
                if self.connect_task is not None:
                    try:                        # bounded, as at shutdown: a wedged close must not hang the call
                        await asyncio.wait_for(self.connect_task, srv.DEADLINE_S)
                    except TimeoutError:
                        print(f"slopymem-mcp: store {store.name}: the lost connection did not close within "
                              f"{srv.DEADLINE_S:.0f}s; leaving it — see SETUP.md#servers", file=sys.stderr)
                # Only now: the old task's close can itself fail (another call's request still in flight on
                # it — the SDK raises BrokenResourceError on teardown) and its except path then sets `ready`
                # and `failure`; both must be reset AFTER it has finished, or the wait below returns at once
                # with no session (observed with two calls losing the session together).
                self.ready.clear()
                self.upstream, self.failure = None, None
                self._start_connecting(store)
            await self.ready.wait()
            if self.upstream is None:
                raise RuntimeError(self.failure or self._unreachable("reconnect failed"))

    async def _forward(self, fn: Callable[[ClientSession], Awaitable[T]]) -> T:
        """One upstream call. A session the server has lost is reconnected once and the call retried
        once; a healthy session never reconnects; a session lost again at once is a failure, not a loop."""
        seen, session = self._attempts, self.upstream      # read together: the attempt this session came from
        try:
            return await self._on(session, fn)
        except SessionLost:
            await self._reconnect(seen)
            try:
                return await self._on(self.upstream, fn)
            except SessionLost as again:
                raise RuntimeError(self._unreachable(f"server session lost again right after reconnecting ({again})")) from again

    async def _on(self, session: ClientSession, fn: Callable[[ClientSession], Awaitable[T]]) -> T:
        """Run one call on the session while watching the connect task that holds its connection. When the
        server has crashed, the call's own POST cannot connect, the transport's task group dies and that
        task ends — but the SDK leaves the request waiting forever (its pending-request cleanup runs under
        the same cancellation and is cancelled itself; observed with the fake server). So the connect task
        ending under a call is read as the loss, the call is abandoned and reported lost."""
        if session is None:                # gone between the caller's look and the call
            raise SessionLost("no live server session")
        holder = self.connect_task
        call = asyncio.ensure_future(fn(session))
        try:
            await asyncio.wait({call, holder} if holder is not None else {call}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not call.done():
                call.cancel()
                await asyncio.wait({call})
        if call.cancelled():
            raise SessionLost("the connection closed under the call")
        try:
            return call.result()
        except Exception as e:
            if _lost(e):
                raise SessionLost(f"{e}" or type(e).__name__) from e
            raise

    def _register_handlers(self) -> None:
        s = self.server

        @s.list_tools()
        async def list_tools() -> list[types.Tool]:
            if await self._session() is None:
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
            return (await self._forward(lambda u: u.list_tools())).tools

        @s.call_tool()
        async def call_tool(name: str, arguments: dict | None):
            if await self._session() is None:
                if name != "memory_init":
                    raise RuntimeError(self.failure or f"no memory store for {self.cwd} — call memory_init first — see SETUP.md#registry")
                return await self._init(arguments or {})
            # The lowlevel Server.call_tool() decorator forwards a returned types.CallToolResult
            # whole (isError + structuredContent preserved) — verified against mcp 1.27.1's source,
            # which special-cases `isinstance(results, types.CallToolResult)` ahead of its own
            # content-list/dict normalization. So the upstream result is passed straight through
            # rather than unpacked into `.content` and re-raised on `.isError`.
            return await self._forward(lambda u: u.call_tool(name, arguments or {}))

        @s.list_prompts()
        async def list_prompts() -> list[types.Prompt]:
            if await self._session() is None:
                if self.failure:                # the same message on every surface the harness may read first
                    raise RuntimeError(self.failure)
                return []
            return (await self._forward(lambda u: u.list_prompts())).prompts

        @s.list_resources()
        async def list_resources() -> list[types.Resource]:
            if await self._session() is None:
                if self.failure:
                    raise RuntimeError(self.failure)
                return []
            return (await self._forward(lambda u: u.list_resources())).resources

        @s.read_resource()
        async def read_resource(uri):
            if await self._session() is None:
                raise RuntimeError(self.failure or f"no memory store for {self.cwd} — call memory_init first — see SETUP.md#registry")
            # mcp 1.27.1's read_resource decorator (read from lowlevel/server.py) does not accept a
            # ReadResourceResult or a plain list of TextResourceContents/BlobResourceContents back —
            # only str, bytes, or Iterable[ReadResourceContents] (mcp.server.lowlevel.helper_types).
            # So the upstream's ReadResourceResult.contents is unpacked into that shape; a blob's
            # base64 text is decoded back to bytes so the decorator re-encodes it as a blob again
            # rather than as text.
            result = await self._forward(lambda u: u.read_resource(uri))
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
