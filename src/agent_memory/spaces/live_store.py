# src/agent_memory/spaces/live_store.py
"""Live-store placement path for the m3 memory (raw-text-as-memory). Places one agent-saved memory
with the fixed-origin episodic policy + optional scope, then spawns co-occurrence (same-session)
note_related edges between the new memory's SEMANTIC node and the prior same-session semantic nodes
(connected-but-not-similar — the burst's reason to exist; NOT proximity, which is circular). Used by
the smoke-test driver (batch) and the MCP save handler. STATELESS re: session tracking
— the caller owns the {session_key: [semantic_node_ids]} map (the MCP save handler persists it)."""
from agent_memory.config import settings
from agent_memory.spaces import store as m3
from agent_memory.spaces.facets import upsert_facet
from agent_memory.spaces.ingest import place_memory
from agent_memory.spaces.retrieve import retrieve_seeded, seed_query_coords


def save_memory(conn, sem_proj, embedder, episodic_coord_fn, *, ref, text, timestamp,
                scope=None, thread=None, prior_session_nodes=()) -> dict[str, str]:
    """Place one memory (semantic + episodic + project-iff-scope), bind its constellation, then
    note_related its semantic node to each prior same-session semantic node. Returns {space: node_id}
    (always includes 'semantic')."""
    # Persist the memory's own text BEFORE placement (readability invariant: no placed
    # memory without a text row). Also persist scope + at so the store is SELF-DESCRIBING: a refit
    # re-derives project (from scope) and episodic (from at) coords bit-exact from the DB alone.
    # Idempotent — ON CONFLICT DO NOTHING means re-placing over a surviving row never rewrites
    # history (the refit relies on this to re-place without mutating scope/at/text/thread).
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_memory (memory_id, text, thread, scope, at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (memory_id) DO NOTHING",
            (ref, text, thread, scope, timestamp),
        )
    memory = {"ref": ref, "content": text, "timestamp": timestamp}
    if scope:
        memory["code_path"] = scope
    node_ids = place_memory(conn, sem_proj, None, embedder, memory,  # epoch_range=None: unused, episodic_coord_fn overrides
                            episodic_coord_fn=episodic_coord_fn)
    for prior in prior_session_nodes:
        m3.note_related(conn, node_ids["semantic"], prior)
    return node_ids


def place_facets(conn, *, memory_id, semantic_node_id, facets, facet_projectors, embedder,
                 extractor_version) -> tuple[list[str], list[str]]:
    """Save-time facet placement (stateless). For each (space, text) in `facets`:
      1. PERSIST it to m3_facet (upsert_facet — DO UPDATE, sharpen-on-re-save) with `extractor_version`.
         Persistence is UNCONDITIONAL: a facet is durable even when its space has no projector loaded
         yet, so it joins the map at the next refit (honest deferral).
      2. PLACE it iff that space has a LOADED projector: embed the facet text ONCE, transform through
         the space's projector (same coordinate frame as the multispace_pass backfill — embed_document),
         place a `memory`-kind node (source_ref=memory_id), and bind it star-to-semantic (an additive
         `assoc` edge to the memory's semantic node — the same constellation pattern used elsewhere).

    Returns (facets_placed, facets_deferred): the space names that got a node vs. those persisted-only
    (no projector). Ordered by the facets dict's iteration order. Caller guards `if facets:` (a None/
    empty map means no facet work at all — the byte-identical control)."""
    projectors = facet_projectors or {}
    placed: list[str] = []
    deferred: list[str] = []
    for space, text in facets.items():
        upsert_facet(conn, memory_id, space, text, extractor_version)
        proj = projectors.get(space)
        if proj is None:                                   # persisted-not-placed (honest deferral)
            deferred.append(space)
            continue
        coord = proj.project(embedder.embed_document(text))
        fnode = m3.place_node(conn, space, label=memory_id, kind="memory", coord=coord,
                              source_ref=memory_id)
        m3.bind_constellation(conn, [fnode, semantic_node_id])   # star-to-semantic (the constellation pattern)
        placed.append(space)
    return placed, deferred


def _node_memory_map(conn, node_ids) -> dict[str, str]:
    """node_id -> memory_id (source_ref). A memory's aspect-nodes share source_ref (set by place_node)."""
    if not node_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, source_ref FROM m3_node WHERE id::text = ANY(%s)", (list(node_ids),))
        return {r[0]: r[1] for r in cur.fetchall()}


# Breadth (the multi-space retrieval boost) counts ONLY semantic + the content-facet spaces. Episodic/project nodes are
# PLACEMENT STRUCTURE (every saved memory gets them via place_memory), so counting them would grant
# spurious importance from node-count, not facet richness — legacy-space breadth must never boost.
_BREADTH_SPACES = frozenset({"semantic", "function", "feeling", "fiction", "player_facing"})


def _node_meta(conn, node_ids) -> dict[str, tuple[str, str]]:
    """node_id -> (memory_id, space_name). The breadth boost + return_spaces need each considered
    node's SPACE as well as its memory; one join instead of the plain source_ref lookup."""
    if not node_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT n.id::text, n.source_ref, s.name FROM m3_node n "
            "JOIN m3_space s ON s.id = n.space_id WHERE n.id::text = ANY(%s)",
            (list(node_ids),))
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _memory_texts(conn, memory_ids) -> dict[str, str]:
    """memory_id -> text for the top-k enrichment. Missing rows (pre-fix legacy data) are
    simply absent — the caller renders them as text=None, never an error."""
    if not memory_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT memory_id, text FROM m3_memory WHERE memory_id = ANY(%s)",
                    (list(memory_ids),))
        return {r[0]: r[1] for r in cur.fetchall()}


def thread_semantic_nodes(conn, thread) -> list[str]:
    """Semantic node ids of all memories already in `thread`, chronological — the DB-backed
    durable wiring context: a rationale thread resumes across MCP sessions and server restarts
    (wiring by design structure, not process lifetime). Called BEFORE the new memory is inserted,
    so the lookup can never return the memory being saved (no self-edges)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT n.id::text FROM m3_memory m "
            "JOIN m3_node n ON n.source_ref = m.memory_id "
            "JOIN m3_space s ON s.id = n.space_id "
            "WHERE m.thread = %s AND s.name = 'semantic' ORDER BY m.created_at",
            (thread,))
        return [r[0] for r in cur.fetchall()]


def retrieve_memories(conn, sem_proj, embedder, query, k, *, query_facets=None, facets=None,
                      epoch_range=None, facet_projectors=None, return_spaces=False,
                      return_gradient=False, **kwargs):
    """Memory-level top-k: over-fetch k*OVERFETCH nodes, dedup aspect-nodes -> memories
    (max-activation node per memory, grouped by source_ref), truncate to k memories.

    Multi-space retrieval: `facet_projectors` = {space: projector} for the content-facet spaces.
    Two seeding modes drive the content-facet spaces, selected by `query_facets`:

      * `query_facets is None` -> the RAW-TEXT projector-transform FALLBACK (kept for
        non-LLM consumers): with projectors loaded, the raw query embedding is transformed through
        EVERY facet projector and seeds that space; with none loaded, nothing extra is seeded.
      * `query_facets` is a dict (possibly empty) -> IN-DISTRIBUTION query-facet seeding (the facet-
        seeding enhancer): NO raw-text facet transforms at all. Each supplied facet TEXT is embedded once and
        transformed through ITS OWN space's projector (facet descriptions are in-distribution for
        projectors fit on facet descriptions; raw queries are OOD and route by coincidence). A space
        with no supplied facet OR no loaded projector gets no seed; `{}` -> semantic-only seeding.

    Semantic is ALWAYS seeded with the raw query (in-distribution for the semantic projector, which
    was fit on raw memory texts). Facet-space seeds are weighted by m3_facet_seed_weight inside
    retrieve_seeded (weight 0 or no projectors = byte-identical to semantic-only). After the
    max-aggregation dedup, each memory's score gets a BOUNDED breadth boost score*(1+beta*(breadth-1))
    capped at (1+3*beta). The boost is GATED on facet_projectors being non-empty — no projectors
    loaded means NO boost regardless of beta, so the production path with no projectors configured
    stays byte-identical to semantic-only seeding. breadth = #distinct _BREADTH_SPACES (semantic + content-facet spaces ONLY;
    episodic/project are placement structure, never facet richness) in which the memory has an
    activated (>1e-9) considered node, floored at 1 (an episodic/project-only surfacing is never
    PENALIZED by the exclusion). beta=0 -> no boost.

    Returns [(memory_id, score)] desc, len<=k. With return_spaces=True returns
    (ranked, {memory_id: {space: activation}}) so a caller can SEE which space promoted each surfaced
    memory (the OOD spurious-promotion watch); default False keeps the legacy shape."""
    over = settings.m3_dedup_overfetch
    if query_facets is None:
        # RAW-TEXT projector-transform FALLBACK (the original behaviour, EXACTLY). Embed ONCE — the
        # same query vector feeds the semantic seed (passed into seed_query_coords) AND every facet
        # projector transform (nomic CPU latency on the hot path). Inactive ->
        # None, seed_query_coords embeds as before. Byte-identical either way (same text -> same vector).
        qvec = embedder.embed_query(query) if facet_projectors else None
        sc = seed_query_coords(sem_proj, embedder, query, facets=facets, epoch_range=epoch_range, qvec=qvec)
        if facet_projectors:
            # Same query embedding, transformed per space -> genuinely different per-space neighbourhoods.
            # The weight (incl. the 0.0 = OFF byte-exact control) is applied inside retrieve_seeded.
            for space, proj in facet_projectors.items():
                sc[space] = proj.project(qvec)
    else:
        # Facet-seeding enhancer: IN-DISTRIBUTION query-facet seeding. Semantic ALWAYS seeded with the raw query;
        # NO raw-text facet transforms. Each supplied facet text is embedded once (facets are distinct
        # texts) and transformed through ITS space's projector; a space with no facet or no loaded
        # projector seeds nothing. Empty dict -> semantic-only (silent-on-everything, distinct from the
        # None fallback). facet_projectors falsy -> nothing extra to seed regardless.
        sc = seed_query_coords(sem_proj, embedder, query, facets=facets, epoch_range=epoch_range)
        if facet_projectors:
            for space, facet_text in query_facets.items():
                proj = facet_projectors.get(space)
                if proj is not None:
                    sc[space] = proj.project(embedder.embed_query(facet_text))
    ranked, act = retrieve_seeded(conn, sc, k * over, return_activation=True, **kwargs)
    meta = _node_meta(conn, ranked)
    best: dict[str, float] = {}
    spaces: dict[str, dict[str, float]] = {}          # memory_id -> {space: max activation there}
    for nid in ranked:
        mem, space = meta.get(nid, (None, None))
        if mem is None:
            continue
        a = act[nid]
        # activation is non-negative; sentinel < 0 ensures a memory's first-seen node always wins init
        if a > best.get(mem, -1.0):
            best[mem] = a
        if a > 1e-9:                                   # breadth counts only genuinely-activated spaces
            d = spaces.setdefault(mem, {})
            if a > d.get(space, 0.0):
                d[space] = a
    boost = settings.m3_breadth_boost
    # GATED on facet_projectors: no projectors -> no boost regardless of beta (with no projectors
    # configured the production path is byte-identical to semantic-only seeding); beta=0 -> skip too (the control).
    if boost and facet_projectors:
        for mem in best:
            # content-space breadth only (semantic + facets); floored at 1 so an episodic/project-
            # only surfacing is merely unboosted, never penalized below its raw proximity score
            breadth = max(1, sum(1 for sp in spaces.get(mem, ()) if sp in _BREADTH_SPACES))
            best[mem] *= min(1.0 + boost * (breadth - 1), 1.0 + 3.0 * boost)
    ranked_mems = sorted(best.items(), key=lambda t: (-t[1], t[0]))[:k]
    if return_spaces and return_gradient:
        # Two optional trailing return values would make the tuple shape ambiguous at every call
        # site. Fail loud rather than pick one silently — nothing needs both today.
        raise ValueError("return_spaces and return_gradient are mutually exclusive")
    if return_spaces:
        return ranked_mems, {m: spaces.get(m, {}) for m, _ in ranked_mems}
    if return_gradient:
        # SEAM (additive, legacy path). `act` is the NODE-level gradient over the ASPECT population
        # (the legacy path's own synapse_wave — same attenuation/cutoff/hop_cap); `best` is the
        # MEMORY-level votes BEFORE the [:k] truncation above. Both already existed as locals.
        from agent_memory.spaces.word_vote import _reading
        return ranked_mems, _reading(act, best)
    return ranked_mems
