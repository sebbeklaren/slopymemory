# tests/test_live_store.py
from datetime import datetime, timezone

import numpy as np

from agent_memory.spaces import store as m3
from agent_memory.spaces.ingest import place_memory
from agent_memory.spaces.live_store import save_memory


class _StubEmbedder:
    def embed_document(self, text):
        v = np.zeros(768, dtype="float32"); v[0] = 1.0
        return v


class _StubProjector:
    def project(self, vec):
        return (0.0, 0.0, 0.0)


def test_place_memory_selectable_episodic_policy(conn):
    # episodic_coord_fn overrides the legacy epoch_range path; the episodic node lands at fn(t).
    m3.apply_schema(conn); m3.seed_spaces(conn)
    t = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    sentinel = (0.5, 0.0, 0.0)
    mem = {"ref": "M", "content": "x", "timestamp": t, "code_path": "a/b"}
    ids = place_memory(conn, _StubProjector(), None, _StubEmbedder(), mem,
                       episodic_coord_fn=lambda _t: sentinel)
    with conn.cursor() as cur:
        cur.execute("SELECT coord FROM m3_node WHERE id = %s", (ids["episodic"],))
        coord = tuple(round(c, 6) for c in cur.fetchone()[0])
    assert coord == sentinel


def test_place_memory_no_code_path_is_two_node_constellation(conn):
    # No code_path -> semantic + episodic only (general agent memory has no scope).
    m3.apply_schema(conn); m3.seed_spaces(conn)
    mem = {"ref": "M", "content": "x", "timestamp": datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)}
    ids = place_memory(conn, _StubProjector(), None, _StubEmbedder(), mem,
                       episodic_coord_fn=lambda _t: (0.0, 0.0, 0.0))
    assert set(ids) == {"semantic", "episodic"}


def test_place_memory_with_code_path_is_three_node(conn):
    m3.apply_schema(conn); m3.seed_spaces(conn)
    mem = {"ref": "M", "content": "x", "timestamp": datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
           "code_path": "ops/telemetry"}
    ids = place_memory(conn, _StubProjector(), None, _StubEmbedder(), mem,
                       episodic_coord_fn=lambda _t: (0.0, 0.0, 0.0))
    assert set(ids) == {"semantic", "episodic", "project"}


def _semantic_neighbours(conn, node_id):
    return {d for d, _ in m3.outgoing(conn, node_id)}


def test_save_memory_wires_cooccurrence_clique_within_session(conn):
    # Three saves in one session form a clique on their SEMANTIC nodes; a save in another session
    # shares no edge with them (co-occurrence = same-session, NOT proximity).
    m3.apply_schema(conn); m3.seed_spaces(conn)
    emb, proj, epi = _StubEmbedder(), _StubProjector(), (lambda _t: (0.0, 0.0, 0.0))
    t = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    session = []
    ids = {}
    for ref in ("A", "B", "C"):
        n = save_memory(conn, proj, emb, epi, ref=ref, text=ref, timestamp=t,
                        prior_session_nodes=list(session))
        ids[ref] = n["semantic"]; session.append(n["semantic"])
    d = save_memory(conn, proj, emb, epi, ref="D", text="D", timestamp=t, prior_session_nodes=[])
    # clique on A/B/C semantic nodes
    assert ids["B"] in _semantic_neighbours(conn, ids["A"])
    assert ids["C"] in _semantic_neighbours(conn, ids["A"])
    assert ids["C"] in _semantic_neighbours(conn, ids["B"])
    # D (other session) is isolated from the A/B/C clique
    assert ids["A"] not in _semantic_neighbours(conn, d["semantic"])


def test_save_memory_scope_makes_three_node_else_two(conn):
    m3.apply_schema(conn); m3.seed_spaces(conn)
    emb, proj, epi = _StubEmbedder(), _StubProjector(), (lambda _t: (0.0, 0.0, 0.0))
    t = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    no_scope = save_memory(conn, proj, emb, epi, ref="N", text="n", timestamp=t)
    with_scope = save_memory(conn, proj, emb, epi, ref="S", text="s", timestamp=t, scope="ops/auth")
    assert set(no_scope) == {"semantic", "episodic"}
    assert set(with_scope) == {"semantic", "episodic", "project"}
