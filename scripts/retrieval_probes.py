#!/usr/bin/env python3
"""The retrieval probes: does retrieval bring the known target memory into the top k for each probe?

The corpus (tests/substrate/retrieval_probes/corpus.yaml) is saved through the real save path into a *_test database with
the real embedder; each probe is retrieved the way the coding dialect retrieves (concept-primary, with the probe's
query_concepts). The reading is objective — the target's rank, no judge — and every reading carries the corpus size,
because a fixed top-k window gets more competitive as a corpus grows: a bare "N/M" is not comparable across corpora.

A change to retrieval is judged against the checked-in baseline (tests/substrate/retrieval_probes/baseline.json), probe by
probe: improved, unchanged or regressed. A change that improves updates the baseline in the same commit, so the gain
is visible in the diff.

Usage: retrieval_probes.py [--db postgresql:///slopymem_substrate_test] [--json] [--baseline PATH]
The database is TRUNCATED: its name must end in _test."""
import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent / "tests" / "substrate" / "retrieval_probes"
CORPUS, PROBES, BASELINE = HERE / "corpus.yaml", HERE / "probes.yaml", HERE / "baseline.json"
TABLES = "m3_facet, m3_memory, m3_memory_supersession, m3_positive_link, m3_negative_link, m3_synapse, m3_node"


def load_probes(path: Path = PROBES) -> list[dict]:
    probes = yaml.safe_load(path.read_text())["probes"]
    for p in probes:
        missing = {"id", "kind", "query", "query_concepts", "expect_prefix"} - p.keys()
        if missing:
            raise ValueError(f"probe {p.get('id')!r} is missing {sorted(missing)}")
        p.setdefault("k", 5)
    return probes


# The coding dialect's server environment (slopymemory.store.DIALECT_ENV["coding"]), as the settings fields it
# sets — read from the product's own table, so the probes cannot drift from what a coding store runs.
ENV_FIELDS = {"AM_M3_RETRIEVAL_MODE": ("m3_retrieval_mode", str),
              "AM_M3_WORD_DEPTH_STEEPNESS": ("m3_word_depth_steepness", float),
              "AM_M3_BOOTSTRAP_N": ("m3_bootstrap_n", int),
              "AM_M3_CONCEPT_SEED_SPACES": ("m3_concept_seed_spaces", str)}


def coding_dialect(workdir: Path, **override):
    """Install the coding dialect's settings where the save and retrieve paths read them, with the save path's
    files in a scratch directory (never the caller's checkout). `override` replaces single fields — a
    counterfactual reading, e.g. m3_concept_seed_spaces="own"."""
    import agent_memory.spaces.live_store_state as ls
    import agent_memory.spaces.word_vote as wv
    from slopymemory.store import DIALECT_ENV
    env = DIALECT_ENV["coding"]
    unknown = set(env) - set(ENV_FIELDS)
    if unknown:
        raise RuntimeError(f"the coding dialect sets {sorted(unknown)}, which the probes do not apply")
    fields = {f: cast(env[k]) for k, (f, cast) in ENV_FIELDS.items() if k in env}
    fields.update(override)
    s = dataclasses.replace(ls.settings, m3_buffer_path=str(workdir / "m3_buffer.jsonl"),
                            m3_projector_path=str(workdir / "m3_projector.pkl"), **fields)
    ls.settings = s
    wv.settings = dataclasses.replace(wv.settings, **fields)
    return s


def load_corpus(conn, embedder, path: Path = CORPUS):
    """Empty the database's memory tables, then save every corpus memory through LiveStore.save, session by
    session. Returns the warm store."""
    from agent_memory.spaces.live_store_state import LiveStore
    from agent_memory.spaces import store as m3
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {TABLES} CASCADE")
    m3.seed_spaces(conn)
    from agent_memory.spaces.live_store_state import settings
    store = LiveStore(embedder, mode="bootstrap", bootstrap_n=settings.m3_bootstrap_n)
    for session in yaml.safe_load(path.read_text())["sessions"]:
        for m in session["memories"]:
            out = store.save(conn, m["text"], session_key=session["session"], thread=m.get("thread"),
                             save_concepts=[list(c) for c in m["concepts"]])
            if out.get("status") not in ("saved", "buffered"):
                raise RuntimeError(f"corpus save failed: {out} for {m['text'][:60]!r}")
    if not store.warm:
        raise RuntimeError(f"the corpus has fewer than {store.bootstrap_n} memories; the store never warmed")
    return store


def corpus_state(conn) -> dict:
    out = {}
    with conn.cursor() as cur:
        for table, key in (("m3_memory", "memories"), ("m3_node", "nodes"), ("m3_synapse", "synapses")):
            cur.execute(f"SELECT count(*) FROM {table}")
            out[key] = cur.fetchone()[0]
    return out


def rank(conn, store, probe: dict) -> int | None:
    """1-based rank of the first result whose text starts with expect_prefix, within the probe's k; None when
    it is not there."""
    out = store.retrieve(conn, probe["query"], probe["k"], query_concepts=[list(c) for c in probe["query_concepts"]])
    for i, r in enumerate(out["results"], 1):
        if (r.get("text") or "").startswith(probe["expect_prefix"]):
            return i
    return None


def reading(conn, store, probes: list[dict]) -> dict:
    ranks = {p["id"]: rank(conn, store, p) for p in probes}
    return {"corpus": corpus_state(conn), "hits": sum(r is not None for r in ranks.values()), "n": len(ranks),
            "ranks": ranks, "kinds": {p["id"]: p["kind"] for p in probes}}


def classify(old: int | None, new: int | None) -> str:
    if old == new:
        return "unchanged"
    if new is None:
        return "regressed"
    if old is None or new < old:
        return "improved"
    return "regressed"


def compare(baseline: dict, current: dict) -> dict:
    """Per-probe deltas against the baseline. The probe sets and the corpus size must match: a reading on a
    different corpus is a different claim."""
    if set(baseline["ranks"]) != set(current["ranks"]):
        raise ValueError("the probe sets differ; update the baseline together with the probes")
    if baseline["corpus"]["memories"] != current["corpus"]["memories"]:
        raise ValueError(f"the corpus changed size ({baseline['corpus']['memories']} -> "
                         f"{current['corpus']['memories']} memories); take a new baseline on the unchanged code first")
    deltas = {pid: classify(baseline["ranks"][pid], current["ranks"][pid]) for pid in current["ranks"]}
    return {"deltas": deltas, "hits": (baseline["hits"], current["hits"]),
            "regressed": sorted(p for p, d in deltas.items() if d == "regressed")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="postgresql:///slopymem_substrate_test")
    ap.add_argument("--json", action="store_true", help="print the reading as JSON (a new baseline)")
    ap.add_argument("--baseline", default=str(BASELINE), help="compare against this reading")
    a = ap.parse_args(argv)
    name = a.db.rsplit("/", 1)[-1].split("?")[0]
    if not name.endswith("_test"):
        print(f"retrieval probes: refusing {name!r} — the corpus load empties the database; its name must end in _test")
        return 2
    import psycopg
    from pgvector.psycopg import register_vector
    from agent_memory.embed.nomic import NomicEmbedder
    from agent_memory.spaces import store as m3
    from agent_memory.store import db
    with tempfile.TemporaryDirectory() as tmp, psycopg.connect(a.db, autocommit=True) as conn:
        register_vector(conn)
        db.apply_schema(conn); m3.apply_schema(conn)
        coding_dialect(Path(tmp))
        store = load_corpus(conn, NomicEmbedder())
        cur = reading(conn, store, load_probes())
    if a.json:
        print(json.dumps(cur, indent=1, sort_keys=True))
        return 0
    print(f"retrieval probes: {cur['hits']}/{cur['n']} probes reach their target within k "
          f"[corpus: {cur['corpus']['memories']} memories, {cur['corpus']['nodes']} nodes]")
    base = json.loads(Path(a.baseline).read_text()) if Path(a.baseline).exists() else None
    for pid, r in cur["ranks"].items():
        delta = f"  {classify(base['ranks'][pid], r)} (was {base['ranks'][pid]})" if base and pid in base["ranks"] else ""
        print(f"  {pid:28} {cur['kinds'][pid]:12} rank {r if r else 'MISS'}{delta}")
    if base:
        report = compare(base, cur)
        print(f"baseline {report['hits'][0]} -> now {report['hits'][1]}; regressed: {report['regressed'] or 'none'}")
        return 1 if report["regressed"] or report["hits"][1] < report["hits"][0] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
