# src/agent_memory/spaces/ingest.py
"""Place facts into the semantic space: embed (nomic/stub) -> fit a 768->3D projector on the
batch -> project each -> place_node. The projector is returned so the SAME projection can be
applied to queries at retrieval time (coords must share one projection).

place_corpus (the coordinate-substrate path) is different: it places one node per (memory, space)
using each space's OWN coordinate source — semantic = UMAP fit on a background corpus, project =
code-path prefix hash, episodic = numeric timestamp — instead of a shared per-batch PCA."""
from collections import namedtuple

import numpy as np

from agent_memory.config import settings
from agent_memory.spaces import coordinate_sources as cs
from agent_memory.spaces import store as m3
from agent_memory.spaces.projection import Projector, fit_pca

PlacedCorpus = namedtuple("PlacedCorpus", ["refs", "sem_projector", "epoch_range"])


def ingest_semantic(conn, embedder, facts) -> tuple[dict[str, str], Projector]:
    embs = np.stack([np.asarray(embedder.embed_document(f.embed_text), dtype="float64") for f in facts])
    projector = fit_pca(embs)
    refs: dict[str, str] = {}
    for f, e in zip(facts, embs):
        coord = projector.project(e)
        refs[f.ref] = m3.place_node(conn, "semantic", f.label, f.kind, coord, source_ref=f.embed_text)
    return refs, projector


def fit_constellation_projectors(embedder, memories, *, projector_factory=fit_pca) -> dict:
    """Fit one projector per space, over the union of every memory's per-space aspect-texts.
    Does NOT touch the DB. Caller passes the resulting {space: Projector} dict to
    place_constellations (per-session ingest, no refit) — used by the longitudinal eval harness
    so cross-session coords stay comparable."""
    spaces = sorted({sp for m in memories for sp in m["aspects"]})
    projectors: dict = {}
    for sp in spaces:
        texts = [m["aspects"][sp] for m in memories if sp in m["aspects"]]
        embs = np.stack([np.asarray(embedder.embed_document(t), dtype="float64") for t in texts])
        projectors[sp] = projector_factory(embs)
    return projectors


def place_constellations(conn, embedder, projectors: dict, memories) -> dict[str, dict[str, str]]:
    """Place each memory's per-space aspect-nodes using the GIVEN projectors (no fitting), and
    bind each memory's aspect-nodes into a constellation. Spaces present in a memory's aspects
    but NOT in `projectors` are silently skipped (the caller's contract; per-session ingest may
    legitimately have facts that don't cover every projected space)."""
    node_ids: dict[str, dict[str, str]] = {}
    for m in memories:
        per_space: dict[str, str] = {}
        for sp, text in m["aspects"].items():
            if sp not in projectors:                   # silently skip un-projectable spaces
                continue
            coord = projectors[sp].project(np.asarray(embedder.embed_document(text), dtype="float64"))
            per_space[sp] = m3.place_node(conn, sp, m["id"], "fact", coord, source_ref=text)
        m3.bind_constellation(conn, list(per_space.values()))
        node_ids[m["id"]] = per_space
    return node_ids


def ingest_constellations(conn, embedder, memories, *, projector_factory=fit_pca) -> tuple[dict[str, dict[str, str]], dict]:
    """Convenience: fit projectors on `memories`, then place + bind. Backward-compatible —
    existing callers (the eval scripts + tests) keep their interface.

    projector_factory: callable(embeddings) -> projector. Defaults to fit_pca; pass fit_umap for
    the PCA-vs-UMAP fidelity comparison."""
    projectors = fit_constellation_projectors(embedder, memories, projector_factory=projector_factory)
    node_ids = place_constellations(conn, embedder, projectors, memories)
    return node_ids, projectors


def place_memory(conn, sem_proj, epoch_range, embedder, memory, *, episodic_coord_fn=None) -> dict[str, str]:
    """Place ONE memory's aspect-nodes (each space's own coordinate source) + bind them.
    memory: {ref, content, timestamp, [code_path]}. The PROJECT node is placed only when `code_path`
    is present (general agent memory may carry no scope -> a 2-node semantic+episodic constellation).
    episodic_coord_fn (default None) selects the episodic policy: None -> legacy
    cs.episodic_coord(t, epoch_range) (byte-identical for existing callers); a callable fn(t)->coord
    -> the live fixed-origin policy (epoch_range then unused). Returns {space: node_id}.

    settings.m3_episodic_on (default True = byte-identical): when False, the episodic node is not
    placed at all (its constellation edges shrink accordingly); semantic/project are unaffected."""
    coords = {"semantic": cs.semantic_coord(sem_proj, embedder, memory["content"])}
    if memory.get("code_path"):
        coords["project"] = cs.project_coord(memory["code_path"])
    if settings.m3_episodic_on:
        coords["episodic"] = (cs.episodic_coord(memory["timestamp"], epoch_range) if episodic_coord_fn is None
                              else episodic_coord_fn(memory["timestamp"]))
    per_space = {sp: m3.place_node(conn, sp, memory["ref"], "memory", coord, source_ref=memory["ref"])
                 for sp, coord in coords.items()}
    m3.bind_constellation(conn, list(per_space.values()))
    return per_space


def place_corpus(conn, embedder, corpus, *, background_contents):
    """Place one node per (memory, space) using each space's OWN coordinate source, and bind each
    memory's aspect-nodes. corpus: list of {ref, content, code_path, timestamp}. Expects a NON-EMPTY
    corpus (build_epoch_range needs >=1 timestamp; an empty corpus raises rather than silently
    returning {}). Returns PlacedCorpus(refs, sem_projector, epoch_range) — refs {ref:{space:node_id}};
    sem_projector + epoch_range are returned so retrieval shares the SAME semantic projection AND
    time-normalization (the episodic facet needs epoch_range)."""
    sem_proj = cs.fit_semantic(embedder, background_contents)
    epoch_range = cs.build_epoch_range([m["timestamp"] for m in corpus])
    out = {m["ref"]: place_memory(conn, sem_proj, epoch_range, embedder, m) for m in corpus}
    return PlacedCorpus(refs=out, sem_projector=sem_proj, epoch_range=epoch_range)
