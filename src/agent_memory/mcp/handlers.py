# src/agent_memory/mcp/handlers.py
"""Pure MCP tool logic over the m3 LiveStore. tenant is threaded (single-tenant store, unused for
scoping in v1); session_key drives co-occurrence edge-spawn within a session. No MCP import here."""
from datetime import datetime, timezone

# The four content-facet spaces a query_facets dict may address. Semantic is NEVER a facet key — it
# is always seeded from the raw query — so a caller that passes {"semantic": ...} is a bug we reject.
# (Canonical space list mirrors live_store._BREADTH_SPACES minus semantic / live_store_state._FACET_SPACES.)
_CONTENT_FACET_SPACES = frozenset({"function", "feeling", "fiction", "player_facing"})


def _validate_facet_map(facets, *, arg: str) -> None:
    """Shared MCP-boundary validation for a {space: text} facet map — used for BOTH the query side
    (`query_facets`, B1) and the save side (`facets`, B2), so the two contracts can never drift. None
    = absent (ok). A dict must map CONTENT-facet space names -> non-empty strings; unknown keys (incl.
    'semantic', which is NEVER a facet — it is seeded/derived from the raw text itself), non-string or
    empty/whitespace values raise ValueError (fail loud at the contract, never silently drop a facet)."""
    if facets is None:
        return
    if not isinstance(facets, dict):
        raise ValueError(f"{arg} must be a dict of {{space: text}} or None, got {type(facets).__name__}")
    unknown = set(facets) - _CONTENT_FACET_SPACES
    if unknown:
        raise ValueError(f"{arg} keys must be content spaces {sorted(_CONTENT_FACET_SPACES)}; "
                         f"got unknown {sorted(unknown)}")
    for space, text in facets.items():
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{arg}[{space!r}] must be a non-empty string, got {text!r}")


def _validate_query_facets(query_facets) -> None:
    """B1 query-side wrapper over the shared facet-map validator (kept for the existing call site)."""
    _validate_facet_map(query_facets, arg="query_facets")


def memory_save(conn, store, tenant: str, session_key: str, text: str, scope: str | None = None,
                thread: str | None = None, facets: dict[str, str] | None = None,
                save_concepts: list | None = None, supersedes: str | None = None) -> dict:
    """Save agent text into the m3 live store under `session_key` (co-occurrence). Empty -> explicit
    no-op. During bootstrap returns {status: buffered, ...}; warm returns {status: saved, memory_id}.
    Optional `thread` durably associates saves sharing the label across sessions.
    `facets` (B2): the memory's extracted content facets {space: text} (function/feeling/fiction/
    player_facing — same aspect definitions as query_facets, NONE-honest so silent aspects are omitted).
    Validated at this boundary (unknown space incl. 'semantic' / empty text -> ValueError). When
    provided + warm, each facet is persisted and (iff its projector is loaded) placed into its space
    and bound to the memory's semantic node; the reply gains facets_placed / facets_deferred.
    `save_concepts` (concept-primary): the memory's extracted typed concepts as a list of [space, label]
    pairs (same concept dialect as memory_retrieve's query_concepts). When provided the concepts are
    placed/deduped/linked so the memory is immediately concept-retrievable; the reply gains
    concepts_placed. None/absent -> byte-identical to the current save.
    `supersedes`: the memory_id of a stored memory this save corrects (on the user's explicit confirmation).
    Validated at this boundary (None ok; else a non-empty string, else ValueError). Passed through to
    the store, which fail-loud-rejects an unknown target (nothing saved) and, on success, durably
    binds the correction (reply gains `supersedes`)."""
    _validate_facet_map(facets, arg="facets")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
        raise ValueError("supersedes must be a memory_id string or None")
    if not text or not text.strip():
        return {"status": "noop", "note": "empty input — nothing saved"}
    return store.save(conn, text, session_key=session_key, now=datetime.now(timezone.utc),
                      scope=scope, thread=thread, facets=facets, save_concepts=save_concepts,
                      supersedes=supersedes)


def memory_retrieve(conn, store, tenant: str, query: str, k: int = 5,
                    query_facets: dict | None = None, facets: dict | None = None,
                    query_concepts: list | None = None) -> dict:
    """Retrieve a graded set of memories (semantic seed + co-occurrence burst, memory-level top-k).
    Returns {status: 'warming up'|'ok', results: [{memory_id, score, text}]} — input to reason
    over (text = the memory's own content; null only for pre-fix legacy rows).
    `tenant` is inert in v1 (single-tenant, matches memory_save).
    `query_facets` (B1 enhancer): the query's extracted content facets {space: text} for
    in-distribution seeding — None omits (raw-text fallback), {} means semantic-only. Validated at
    this boundary (unknown space / empty text -> ValueError). `facets` is the legacy project/episodic
    coordinate knob (forward-compat, not exposed by the MCP tool).
    `query_concepts` (concept-primary): the query's extracted typed concepts as a list of
    [space, label] pairs (same concept dialect as the memory concept layer, NONE-honest, sparse). Only
    consumed when settings.m3_retrieval_mode == 'concept_primary'; otherwise ignored (current path)."""
    _validate_query_facets(query_facets)
    return store.retrieve(conn, query, k, query_facets=query_facets, facets=facets,
                          query_concepts=query_concepts)
