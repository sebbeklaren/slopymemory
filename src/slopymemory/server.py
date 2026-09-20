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
import warnings
from pathlib import Path
from . import paths
from .store import Store

SERVER_MODULE = "agent_memory.mcp.server"
# Measured cold start on this machine (Plan A Task 2 step 1): 11.1 s to the first HTTP answer,
# dominated by the embedder load. Deadline = 3× that, floored at 60 s, so a slow disk or a first
# model fetch does not read as a failure.
DEADLINE_S = 60.0


class ServerNotUp(RuntimeError):
    def __init__(self, store: Store, seconds: float):
        super().__init__(
            f"store {store.name}: no server answering on port {store.port} after {seconds:.0f}s; "
            f"log: {store.log_file()} — see SETUP.md#servers")


def probe(port: int, timeout: float = 0.5) -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/mcp")
        c.getresponse()
        return True
    except OSError:
        return False
    finally:
        try:
            c.close()
        except Exception:
            pass


def server_command(store: Store) -> list[str]:
    return [str(paths.venv_python()), "-m", SERVER_MODULE]


def spawn_detached(store: Store) -> int:
    store.log_file().parent.mkdir(parents=True, exist_ok=True)
    store.path().mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **store.server_env()}
    with open(store.log_file(), "ab") as log:
        p = subprocess.Popen(server_command(store), env=env, cwd=store.path(), stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        pid = p.pid
    # Suppress ResourceWarning: process is intentionally detached in a new session and will outlive
    # this Python process, so the Popen object's lack of explicit wait() is expected and safe.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*subprocess.*still running", category=ResourceWarning)
        del p
    return pid


def ensure_up(store: Store, deadline_s: float = DEADLINE_S) -> None:
    if probe(store.port):
        return
    store.path().mkdir(parents=True, exist_ok=True)
    with open(store.lock_file(), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)          # the second caller waits here while the first starts
        try:
            if not probe(store.port):
                spawn_detached(store)
            t0 = time.monotonic()
            while time.monotonic() - t0 < deadline_s:
                if probe(store.port):
                    return
                time.sleep(0.25)
            raise ServerNotUp(store, deadline_s)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def pid_on_port(port: int) -> int | None:
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, check=False).stdout
    for line in out.splitlines():
        if f":{port} " in line and "pid=" in line:
            return int(line.split("pid=")[1].split(",")[0])
    return None


def stop(store: Store, wait_s: float = 10.0) -> bool:
    pid = pid_on_port(store.port)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        if not probe(store.port):
            return True
        time.sleep(0.2)
    return not probe(store.port)
