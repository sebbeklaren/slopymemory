from agent_memory.spaces import store as m3
from agent_memory.spaces.wave import synapse_wave


def test_wave_spreads_with_attenuation_and_hop_cap(conn):
    a = m3.place_node(conn, "semantic", "A", "fact", (0.0, 0.0, 0.0))
    b = m3.place_node(conn, "procedural", "B", "fact", (0.0, 0.0, 0.0))
    c = m3.place_node(conn, "social", "C", "fact", (0.0, 0.0, 0.0))
    m3.bind_constellation(conn, [a, b])     # a-b
    m3.bind_constellation(conn, [b, c])     # b-c (so c is 2 hops from a)
    act = synapse_wave(conn, seeds={a: 1.0}, hop_cap=1, attenuation=0.5, cutoff=0.0)
    assert act[a] == 1.0 and act[b] == 0.5 and c not in act      # 1 hop reaches b only
    act2 = synapse_wave(conn, seeds={a: 1.0}, hop_cap=2, attenuation=0.5, cutoff=0.0)
    assert abs(act2[c] - 0.25) < 1e-9                            # 2 hops: 1.0*0.5*0.5
    act3 = synapse_wave(conn, seeds={a: 1.0}, hop_cap=2, attenuation=0.5, cutoff=0.3)
    assert c not in act3                                         # cutoff prunes the 0.25 spread
