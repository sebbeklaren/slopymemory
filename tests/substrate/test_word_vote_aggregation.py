import pytest

from agent_memory.spaces.word_vote import aggregate_votes

def test_decay_out_of_range_raises():
    """decay>=1 IS the full within-space sum (TF-IDF anti-pattern); decay<0 subtracts on odd terms.
    Both violate cross-space convergence -> reject at the door. Default path (None->0.25) still works."""
    hits = [("A", "function", 1.0, 1.0)]
    with pytest.raises(ValueError):
        aggregate_votes(hits, decay=1.0)
    with pytest.raises(ValueError):
        aggregate_votes(hits, decay=-0.1)
    v = aggregate_votes(hits)                      # decay=None -> settings default 0.25
    assert v == {"A": 1.0}

def test_cross_space_dominates_same_space_piling():
    """4 concepts piled in ONE space must lose badly to 4 concepts across FOUR spaces."""
    piled = [("A", "function", 1.0, 1.0)] * 4
    spread = [("B", sp, 1.0, 1.0) for sp in ("function", "feeling", "fiction", "player_facing")]
    v = aggregate_votes(piled + spread, decay=0.25)
    # steep saturation: A ~= 1 + .25 + .0625 + .0156 = 1.328 ; B = 4.0
    assert v["B"] > 2.5 * v["A"]

def test_saturation_is_the_mechanism_teeth():
    """If per-space saturation were removed (full sum within a space), A would TIE B — RED."""
    piled = [("A", "function", 1.0, 1.0)] * 4
    spread = [("B", sp, 1.0, 1.0) for sp in ("function", "feeling", "fiction", "player_facing")]
    v = aggregate_votes(piled + spread, decay=0.25)
    assert v["A"] < 1.5 and v["B"] == 4.0        # a no-saturation impl gives A == 4.0 == B

def test_activation_weighted_not_binary_count():
    """A concept reached by the burst (low activation) contributes LESS than a proximity-lit one."""
    prox = [("C", "function", 1.0, 1.0)]         # directly lit
    burst = [("D", "function", 0.4, 1.0)]        # burst-attenuated
    v = aggregate_votes(prox + burst, decay=0.25)
    assert v["C"] > v["D"]                        # binary-count impl would tie them
