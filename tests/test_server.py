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
    monkeypatch.setattr(server, "spawn_detached", lambda st: (pids.append(pid := real_spawn(st)), pid)[1])
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
