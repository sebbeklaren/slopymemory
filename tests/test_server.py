import os, sys, time, threading
from pathlib import Path
import pytest
from slopymemory import server
from slopymemory.store import Store


def test_probe_true_for_any_http_answer_false_when_refused(fake_http_server, free_port):
    assert server.probe(fake_http_server) is True
    assert server.probe(free_port) is False


def test_ensure_up_spawns_once_under_the_lock_and_waits(tmp_home, free_port, monkeypatch):
    """Two callers race; ONE server process must be started. The 'server' is a script that sleeps,
    then binds and answers HTTP — the launcher must wait for it, not fail on the first refusal."""
    st = Store(name="s", dialect="coding", port=free_port, database="s_db", postgres="system")
    st.save()
    fake = tmp_home / "fake_server.py"
    fake.write_text(
        "import os,time,http.server\n"
        "time.sleep(1.0)\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(s): s.send_response(406); s.end_headers()\n"
        "    def log_message(s,*a): pass\n"
        "http.server.HTTPServer(('127.0.0.1', int(os.environ['AM_MCP_PORT'])), H).serve_forever()\n")
    monkeypatch.setattr(server, "server_command", lambda st: [sys.executable, str(fake)])
    starts = []
    real_spawn = server.spawn_detached
    monkeypatch.setattr(server, "spawn_detached", lambda st: (starts.append(1), real_spawn(st))[1])
    errs = []
    def go():
        try: server.ensure_up(st, deadline_s=15)
        except Exception as e: errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errs == [] and len(starts) == 1 and server.probe(free_port)
    assert st.log_file().exists()
    assert server.stop(st) is True
    time.sleep(0.5)
    assert server.probe(free_port) is False


def test_ensure_up_raises_a_named_error_past_the_deadline(tmp_home, free_port, monkeypatch):
    import signal
    st = Store(name="dead", dialect="coding", port=free_port, database="d", postgres="system"); st.save()
    monkeypatch.setattr(server, "server_command", lambda st: [sys.executable, "-c", "import time; time.sleep(30)"])
    pids = []
    real_spawn = server.spawn_detached
    monkeypatch.setattr(server, "spawn_detached", lambda st: (pids.append((p := real_spawn(st)).pid), p)[1])
    try:
        with pytest.raises(server.ServerNotUp) as e:
            server.ensure_up(st, deadline_s=1.5)
        assert "SETUP.md#servers" in str(e.value) and str(st.log_file()) in str(e.value)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_probe_is_false_for_a_port_that_answers_but_not_with_http(free_port):
    """A foreign process on the port (or a half-started one) answers bytes that are not HTTP; that is
    'not up', not an exception out of `slopymem list`."""
    import socket
    ready = threading.Event()
    def serve():
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", free_port)); s.listen(); ready.set()
            c, _ = s.accept()
            with c:
                c.sendall(b"nope\n")
    t = threading.Thread(target=serve, daemon=True); t.start(); ready.wait(2)
    assert server.probe(free_port) is False
    t.join(2)


def test_ensure_up_reports_a_server_that_exits_at_once_with_its_log_tail(tmp_home, free_port, monkeypatch):
    """A server that dies on start (missing database, import error) must not be waited on for the whole
    deadline: the exit code and the last log lines are the diagnosis, and they are in the message."""
    st = Store(name="dies", dialect="coding", port=free_port, database="d", postgres="system"); st.save()
    monkeypatch.setattr(server, "server_command",
                        lambda st: [sys.executable, "-c", "import sys; print('line one'); print('FATAL: no such database', file=sys.stderr); sys.exit(3)"])
    t0 = time.monotonic()
    with pytest.raises(server.ServerNotUp) as e:
        server.ensure_up(st, deadline_s=30)
    assert time.monotonic() - t0 < 10
    msg = str(e.value)
    assert "exited with code 3" in msg and "FATAL: no such database" in msg and "dies" in msg and "SETUP.md#servers" in msg


def test_pid_on_port_names_a_missing_ss(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(RuntimeError) as e:
        server.pid_on_port(1)
    assert "ss not found" in str(e.value) and "iproute2" in str(e.value) and "SETUP.md#servers" in str(e.value)


def test_stop_refuses_a_process_that_is_not_this_stores_server(tmp_home, free_port):
    """`ss` names whatever holds the port; a foreign program that took a freed port must not be signalled."""
    import subprocess, signal
    st = Store(name="held", dialect="coding", port=free_port, database="h", postgres="system"); st.save()
    p = subprocess.Popen([sys.executable, "-c",
                          f"import socket,time; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
                          f"s.bind(('127.0.0.1', {free_port})); s.listen(); time.sleep(30)"])
    try:
        for _ in range(50):
            if server.pid_on_port(free_port): break
            time.sleep(0.1)
        with pytest.raises(RuntimeError) as e:
            server.stop(st)
        assert "not this store's server" in str(e.value) and str(p.pid) in str(e.value) and "SETUP.md#servers" in str(e.value)
        assert p.poll() is None, "the foreign process must not have been signalled"
    finally:
        p.send_signal(signal.SIGTERM); p.wait(5)


def test_stop_reports_a_port_that_answers_with_no_visible_owner(tmp_home, fake_http_server, monkeypatch):
    """`ss -p` shows pids only for the caller's own processes: a port that answers but shows no owner is
    'someone else's process', reported as such — never 'was not running'."""
    st = Store(name="ghost", dialect="coding", port=fake_http_server, database="g", postgres="system"); st.save()
    monkeypatch.setattr(server, "pid_on_port", lambda port: None)
    with pytest.raises(RuntimeError) as e:
        server.stop(st)
    assert "no owning process" in str(e.value) and "SETUP.md#servers" in str(e.value)
