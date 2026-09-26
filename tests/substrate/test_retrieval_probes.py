"""The retrieval probes as a test: the checked-in corpus, saved through the real save path with the real
embedder, must reach at least its baseline — no probe may lose ground, and the hit count may not fall. See
scripts/retrieval_probes.py for what the corpus and the probes lean on. Needs a *_test database and the embedding model."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import retrieval_probes as ks  # noqa: E402

@pytest.fixture(scope="module")
def loaded(_db_ok, tmp_path_factory):
    import psycopg
    from pgvector.psycopg import register_vector
    import agent_memory.spaces.live_store_state as ls
    import agent_memory.spaces.word_vote as wv
    from agent_memory.embed.nomic import NomicEmbedder
    from conftest import _url
    saved = (ls.settings, wv.settings)
    conn = psycopg.connect(_url(), autocommit=True)
    register_vector(conn)
    ks.coding_dialect(tmp_path_factory.mktemp("retrieval_probes"))
    store = ks.load_corpus(conn, NomicEmbedder())
    yield conn, store
    ls.settings, wv.settings = saved
    conn.close()


@pytest.mark.embed
def test_the_retrieval_probes_hold_their_baseline(loaded):
    conn, store = loaded
    base = json.loads(ks.BASELINE.read_text())
    cur = ks.reading(conn, store, ks.load_probes())
    report = ks.compare(base, cur)
    assert report["regressed"] == [], report
    assert cur["hits"] >= base["hits"], report
    assert cur == base, f"the reading moved without the baseline being updated: {report}"


@pytest.mark.embed
def test_the_probes_can_fail_seeding_only_the_named_space_loses_every_cross_space_probe(loaded):
    """The instrument's own failure, shown: the same corpus read with each query concept seeded only in the space
    the probe named. Every cross-space probe misses — so a green reading is the seeding working, not an easy set."""
    import dataclasses
    import agent_memory.spaces.word_vote as wv
    conn, store = loaded
    probes = ks.load_probes()
    kept = wv.settings
    wv.settings = dataclasses.replace(kept, m3_concept_seed_spaces="own")
    try:
        narrowed = ks.reading(conn, store, probes)
    finally:
        wv.settings = kept
    cross = [p["id"] for p in probes if p["kind"] == "cross-space"]
    assert cross and all(narrowed["ranks"][pid] is None for pid in cross), narrowed["ranks"]
    assert narrowed["hits"] < json.loads(ks.BASELINE.read_text())["hits"]


def test_the_probes_apply_every_setting_the_coding_dialect_sets():
    from slopymemory.store import DIALECT_ENV
    assert set(DIALECT_ENV["coding"]) <= set(ks.ENV_FIELDS)


def test_compare_names_a_regression_and_refuses_a_different_corpus():
    base = {"corpus": {"memories": 45}, "hits": 2, "ranks": {"a": 1, "b": 3}}
    worse = {"corpus": {"memories": 45}, "hits": 1, "ranks": {"a": 2, "b": None}}
    assert ks.compare(base, worse)["regressed"] == ["a", "b"]
    with pytest.raises(ValueError, match="corpus changed size"):
        ks.compare(base, {**worse, "corpus": {"memories": 46}})
