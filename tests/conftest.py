import os, socket, subprocess, sys, time, http.server, threading
from pathlib import Path
import pytest


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
