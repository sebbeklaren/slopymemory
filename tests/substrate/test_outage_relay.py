"""A real outage: the server reaches Postgres through a relay the test can cut, so "down" is a connection the
operating system genuinely refuses, not a mock. Cut -> a save is held -> restore -> a retrieval brings the held
save's TEXT back. With the drain disabled the same run does not find it, so the test can fail."""
import dataclasses
import os
import socket
import tempfile
import threading

import pytest

PG_SOCKET = "/var/run/postgresql/.s.PGSQL.5432"


class Relay:
    """A Unix-socket relay in front of the local Postgres socket. `open = False` closes new connections at once —
    the client sees the server go away, the way a stopped database looks."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="pgr", dir="/tmp")         # short: a socket path is limited to ~107 bytes
        self.path = os.path.join(self.dir, ".s.PGSQL.5432")
        self.open = True
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(self.path)
        self.sock.listen(16)
        threading.Thread(target=self._accept, daemon=True).start()

    @staticmethod
    def _pipe(a, b):
        try:
            while (data := a.recv(65536)):
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            if not self.open:
                c.close()
                continue
            u = socket.socket(socket.AF_UNIX)
            u.connect(PG_SOCKET)
            threading.Thread(target=self._pipe, args=(c, u), daemon=True).start()
            threading.Thread(target=self._pipe, args=(u, c), daemon=True).start()

    def close(self):
        self.sock.close()
        for f in (self.path,):
            try:
                os.unlink(f)
            except OSError:
                pass
        os.rmdir(self.dir)


@pytest.mark.embed
@pytest.mark.skipif(not os.path.exists(PG_SOCKET), reason="needs the local Postgres Unix socket")
@pytest.mark.parametrize("drain", [True, False])
def test_a_save_made_during_a_real_outage_is_retrievable_after_it(conn, tmp_path, monkeypatch, drain):
    import agent_memory.spaces.live_store_state as ls
    from agent_memory.embed.nomic import NomicEmbedder
    from agent_memory.mcp import server
    from agent_memory.spaces.live_store_state import LiveStore
    from conftest import _url
    from tests_support_fakes import Proj
    relay = Relay()
    try:
        dbname = _url().rsplit("/", 1)[-1]
        s = dataclasses.replace(server.settings, database_url=f"postgresql:///{dbname}?host={relay.dir}",
                                mcp_invocation_log=str(tmp_path / "inv.jsonl"))
        monkeypatch.setattr(server, "settings", s)
        monkeypatch.setattr(ls, "settings", dataclasses.replace(ls.settings, m3_retrieval_mode="concept_primary"))
        monkeypatch.setattr(server, "_store", LiveStore(NomicEmbedder(), mode="fixed", projector=Proj()))
        monkeypatch.setattr(server, "_state", {"schema_ready": True, "failing": False})
        if not drain:
            monkeypatch.setattr(server, "_drain", lambda conn, skip=None: None)
        relay.open = False
        held = server.memory_save(tenant="t", text="The staging database lives on port 6543.",
                                  save_concepts=[["semantic", "staging database port"]])
        assert held["status"] == "held", held
        relay.open = True
        out = server.memory_retrieve(tenant="t", query="which port is the staging database on?",
                                     query_concepts=[["semantic", "staging database port"]])
        assert out["status"] == "ok", out
        found = any((r.get("text") or "").startswith("The staging database") for r in out["results"])
        assert found is drain
    finally:
        relay.close()
