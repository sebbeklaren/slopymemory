"""Shared stand-ins for the save-path tests: a deterministic embedder, a projector that places everything at the
origin, and a warm LiveStore built from them — no model, no UMAP."""
import numpy as np

from agent_memory.spaces.live_store_state import LiveStore


class Emb:
    def embed_document(self, text):
        v = np.zeros(768, dtype="float32")
        v[sum(map(ord, text)) % 768] = 1.0
        return v

    def embed_query(self, text):
        return self.embed_document(text)


class Proj:
    def project(self, vec):
        return (0.0, 0.0, 0.0)


def stub_fit(embedder, texts):
    return Proj()


def warm_store() -> LiveStore:
    return LiveStore(Emb(), mode="fixed", projector=Proj())


# --- the MCP server with a database the test can take away -------------------------------------------------------
import dataclasses

import psycopg
import pytest

REAL_CONNECT = psycopg.connect


@pytest.fixture
def srv(tmp_path, monkeypatch, conn):
    """The server module pointed at the test database, its state files in tmp_path, a warm stand-in store."""
    import agent_memory.durability as du
    import agent_memory.spaces.live_store_state as ls
    from agent_memory.mcp import server
    from conftest import _url
    s = dataclasses.replace(server.settings, database_url=_url(), m3_buffer_path=str(tmp_path / "buf.jsonl"),
                            m3_projector_path=str(tmp_path / "p.pkl"), mcp_invocation_log=str(tmp_path / "inv.jsonl"))
    for mod in (server, du, ls):
        monkeypatch.setattr(mod, "settings", s)
    monkeypatch.setattr(server, "_store", warm_store())
    monkeypatch.setattr(server, "_state", {"schema_ready": True, "failing": False})
    monkeypatch.setattr(server, "_recovered", None, raising=False)
    return tmp_path


def down(monkeypatch):
    """Every new connection is refused, the way a stopped Postgres refuses it; the message carries a URL with a
    password so the tests can see it cleaned."""
    from agent_memory.mcp import server

    def refuse(*a, **k):
        raise psycopg.OperationalError("connection to postgresql://u:" + "pw@h/db failed: Connection refused")
    monkeypatch.setattr(server.psycopg, "connect", refuse)


def up(monkeypatch):
    from agent_memory.mcp import server
    monkeypatch.setattr(server.psycopg, "connect", REAL_CONNECT)
