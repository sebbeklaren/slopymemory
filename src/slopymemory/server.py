"""The store's server as a process: probe it, start it detached from the harness session (it must
outlive the session), wait for it under a start lock (two launchers → one server), stop it on request.
The server binds its port only after the embedder is loaded, so "answers HTTP" means "ready"."""
from __future__ import annotations
import fcntl
import http.client
import os
import signal
import subprocess
import time
from pathlib import Path
from . import paths
from .store import Store

SERVER_MODULE = "agent_memory.mcp.server"
# Measured cold start on the development machine: 11.1 s to the first HTTP answer,
# dominated by the embedder load. Deadline = 3× that, floored at 60 s, so a slow disk or a first
# model fetch does not read as a failure.
DEADLINE_S = 60.0


class ServerNotUp(RuntimeError):
    """The store's server is not answering; the message names the store, the diagnosis and the anchor."""


def _timed_out(store: Store, seconds: float) -> ServerNotUp:
    return ServerNotUp(f"store {store.name}: no server answering on port {store.port} after {seconds:.0f}s; "
                       f"log: {store.log_file()} — see SETUP.md#servers")


def _died(store: Store, returncode: int) -> ServerNotUp:
    return ServerNotUp(f"store {store.name}: server exited with code {returncode} before it answered — "
                       f"last log lines: {log_tail(store)} — log: {store.log_file()} — see SETUP.md#servers")


def log_tail(store: Store, lines: int = 5) -> str:
    """The last lines of the store's log, or why they could not be read — never an exception."""
    try:
        tail = store.log_file().read_text(errors="replace").splitlines()[-lines:]
    except OSError as e:
        return f"(log unreadable: {e})"
    return " | ".join(l.strip() for l in tail) or "(log empty)"


def probe(port: int, timeout: float = 0.5) -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/mcp")
        c.getresponse()
        return True
    except (OSError, http.client.HTTPException):    # refused, timed out, or answered with something not HTTP
        return False
    finally:
        try:
            c.close()
        except Exception:
            pass


def server_command(store: Store) -> list[str]:
    return [str(paths.venv_python()), "-m", SERVER_MODULE]


# The servers this process started, by pid. The handle is KEPT for the life of this process: the child is
# detached on purpose (a new session; it outlives us), and a Popen dropped while its child runs is a
# ResourceWarning that says exactly that — holding it is the honest way to have no warning.
_spawned: dict[int, subprocess.Popen] = {}


def spawn_detached(store: Store) -> subprocess.Popen:
    """Start the server in its own session and RETURN the Popen: the caller polls it, so a server that
    dies at once is reported at once, with its exit code."""
    store.log_file().parent.mkdir(parents=True, exist_ok=True)
    store.path().mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **store.server_env()}
    with open(store.log_file(), "ab") as log:
        p = subprocess.Popen(server_command(store), env=env, cwd=store.path(), stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    _spawned[p.pid] = p
    return p


def ensure_up(store: Store, deadline_s: float = DEADLINE_S) -> None:
    if probe(store.port):
        return
    store.path().mkdir(parents=True, exist_ok=True)
    with open(store.lock_file(), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)          # the second caller waits here while the first starts
        try:
            p = None
            if not probe(store.port):
                p = spawn_detached(store)
            t0 = time.monotonic()
            while time.monotonic() - t0 < deadline_s:
                if probe(store.port):
                    return
                if p is not None and p.poll() is not None:
                    raise _died(store, p.returncode)
                time.sleep(0.25)
            raise _timed_out(store, deadline_s)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def pid_on_port(port: int) -> int | None:
    """The pid listening on the port, as `ss` shows it — only for the caller's own processes."""
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        raise RuntimeError("ss not found — install iproute2 — see SETUP.md#servers") from None
    for line in out.splitlines():
        if f":{port} " in line and "pid=" in line:
            return int(line.split("pid=")[1].split(",")[0])
    return None


def _proc(pid: int, what: str) -> str:
    try:
        return Path(f"/proc/{pid}/{what}").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError as e:
        return f"(unreadable: {e})"


def _owns(pid: int, store: Store) -> bool:
    """Is the process on the store's port the store's server? Its command names the server module, or its
    environment carries the port this store gave it (every server spawned here does; so do the test fakes)."""
    return SERVER_MODULE in _proc(pid, "cmdline") or f"AM_MCP_PORT={store.port} " in _proc(pid, "environ")


def stop(store: Store, wait_s: float = 10.0) -> bool:
    """SIGTERM the store's server and wait for the port to close. False when nothing was running. Raises
    RuntimeError (with the anchor) when the port is held by something that is not this store's server, or
    when the server is still listening after `wait_s`."""
    pid = pid_on_port(store.port)
    if pid is None:
        if probe(store.port):
            raise RuntimeError(f"store {store.name}: port {store.port} answers but no owning process found "
                               f"(a process of another user?) — see SETUP.md#servers")
        return False
    if not _owns(pid, store):
        raise RuntimeError(f"store {store.name}: port {store.port} is held by pid {pid}, which is not this store's "
                           f"server (command: {_proc(pid, 'cmdline').strip()[:120]}); not signalled — see SETUP.md#servers")
    os.kill(pid, signal.SIGTERM)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        if not probe(store.port):
            return True
        time.sleep(0.2)
    # Not False: False means "nothing was running", and `remove` would go on to drop the database under
    # a server that is still serving it.
    raise RuntimeError(f"store {store.name}: server pid {pid} did not exit within {wait_s:g} s of SIGTERM; "
                       f"still listening on port {store.port} — see SETUP.md#servers")
