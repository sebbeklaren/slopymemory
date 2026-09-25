import os, socket, http.server, threading
import pytest


@pytest.fixture(autouse=True)
def _no_real_codex(monkeypatch):
    """No test may shell out to a real `codex` binary, even one installed on the machine running
    these tests — every test starts as if Codex were not on PATH; a test that needs it present
    patches this back to True itself."""
    monkeypatch.setattr("slopymemory.harness_memory._codex_available", lambda: False)


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPYMEM_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_http_server():
    """Anything that answers HTTP on a port — enough for the probe."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(406); self.end_headers()
        def log_message(self, *a): pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def broken_pg_tools(tmp_path, monkeypatch):
    """psql/createdb/dropdb on PATH that fail at once with 'FATAL: boom' on stderr — the shape of a
    Postgres that is down, a role without rights, or a missing database."""
    bin_dir = tmp_path / "fakebin"; bin_dir.mkdir()
    for tool in ("psql", "createdb", "dropdb"):
        f = bin_dir / tool
        f.write_text("#!/bin/sh\necho 'FATAL: boom' >&2\nexit 1\n"); f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir
