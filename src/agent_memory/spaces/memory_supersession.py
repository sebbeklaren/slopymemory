# src/agent_memory/spaces/memory_supersession.py
"""MEMORY-level supersession binding: user-confirmed corrections, surfaced not
scored. Deliberately memory-level, not aspect-level (per-shared-space linking silently writes zero
rows when no spaces are shared); the node-level negative_link machinery is untouched (evals).
`strength` is an INERT SEAM — nothing reads it. GUARD: nothing in this module may reorder, rescore,
filter, or demote a result — attach_supersessions is ADD-ONLY by contract
(tests/test_memory_supersession.py::test_attach_is_add_only)."""
from agent_memory.spaces.live_store import _memory_texts


def note_memory_supersession(conn, by_memory_id: str, stale_memory_id: str, *,
                             bump: float = 1.0) -> None:
    """Record/strengthen 'by supersedes stale'. Idempotent on the pair: a repeat adjudication
    bumps strength (Hebbian; inert seam — nothing reads it yet)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_memory_supersession (stale_memory_id, by_memory_id, strength) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (stale_memory_id, by_memory_id) "
            "DO UPDATE SET strength = m3_memory_supersession.strength + EXCLUDED.strength, "
            "updated_at = now()",
            (stale_memory_id, by_memory_id, bump))


def _out_edges(conn, ids: list[str]) -> dict[str, list[str]]:
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT stale_memory_id, by_memory_id FROM m3_memory_supersession "
                    "WHERE stale_memory_id = ANY(%s)", (list(ids),))
        out: dict[str, list[str]] = {}
        for stale, by in cur.fetchall():
            out.setdefault(stale, []).append(by)
        return out


def _reach_and_cycle(start: str, edges_of) -> tuple[set, bool]:
    """DFS from `start` along by-links (iterative, path-colored). Returns (reached, cycle):
      reached = every node reachable via >=1 edge, `start` itself excluded;
      cycle    = a back-edge into the ACTIVE path exists — a REAL supersession loop.
    Crucially, a head reached by two paths (DAG re-convergence, e.g. a diamond) is NOT a cycle:
    the second path lands on a node that is already fully explored (`done`), not on an ancestor
    still on the stack (`onpath`). A global visited set can't tell those apart; the path colors can."""
    reached: set = set()
    cycle = False
    onpath: set = set()               # gray: nodes on the current DFS stack (ancestors of `node`)
    done: set = set()                 # black: fully-explored subtrees
    stack = [(start, iter(edges_of(start)))]
    onpath.add(start)
    while stack:
        node, it = stack[-1]
        descended = False
        for child in it:
            reached.add(child)
            if child in onpath:
                cycle = True          # back-edge into the active path = real loop
                continue
            if child in done:
                continue              # already fully explored: DAG re-convergence, NOT a cycle
            onpath.add(child)
            stack.append((child, iter(edges_of(child))))
            descended = True
            break
        if not descended:
            onpath.discard(node)
            done.add(node)
            stack.pop()
    reached.discard(start)
    return reached, cycle


def resolve_standing(conn, memory_ids: list[str]) -> dict[str, dict]:
    """For each id with an outgoing supersession link, walk the by-links to the terminal HEADS
    (nodes with no outgoing link). Returns {stale_id: {"heads": sorted [...], "cycle": bool,
    "others": [...]}} — ids with no outgoing link are absent. `others` = heads when heads exist,
    else the other cycle members (so a conflict payload always has candidates to show).
    Exactly one head and no cycle -> the standing version; anything else -> conflict
    (never-silently-pick). Cycle detection is PATH-based (see `_reach_and_cycle`): DAG
    re-convergence (a single head reached by two paths) is clean, not a conflict.
    Table is tiny (explicit adjudications only) — clarity over batching."""
    seeds = list(dict.fromkeys(memory_ids))
    directly_stale = set(_out_edges(conn, seeds))
    cache: dict[str, list[str]] = {}
    def edges_of(nid: str) -> list[str]:
        if nid not in cache:
            cache[nid] = _out_edges(conn, [nid]).get(nid, [])
        return cache[nid]
    out: dict[str, dict] = {}
    for start in seeds:
        if start not in directly_stale:
            continue
        reached, cycle = _reach_and_cycle(start, edges_of)
        # Heads = reached nodes with no outgoing link. Every reached node had its edges fetched
        # during the DFS, so `edges_of` is a cache hit here (no extra DB round-trips).
        heads = {n for n in reached if not edges_of(n)}
        others = sorted(heads) if heads else sorted(reached)
        out[start] = {"heads": sorted(heads), "cycle": cycle, "others": others}
    return out


def attach_supersessions(conn, results: list[dict]) -> list[dict]:
    """ADD-ONLY payload enrichment: a result whose memory is superseded gains `superseded_by`.
    Exactly one head + no cycle -> {"memory_id", "text"} (FULL text — the standing version rides
    with the result; id-only would force the second lookup this feature exists to remove).
    Cycle or divergent heads -> {"conflict": True, "candidates": [{memory_id, text}, ...]} for
    the user to resolve. NEVER reorders, rescores, filters, or drops a result;
    no links -> returns results unchanged (byte-identical)."""
    if not results:
        return results
    resolved = resolve_standing(conn, [r["memory_id"] for r in results])
    if not resolved:
        return results
    need_text = sorted({h for v in resolved.values() for h in v["others"]})
    texts = _memory_texts(conn, need_text)
    for r in results:
        v = resolved.get(r["memory_id"])
        if v is None:
            continue
        if len(v["heads"]) == 1 and not v["cycle"]:
            h = v["heads"][0]
            r["superseded_by"] = {"memory_id": h, "text": texts.get(h)}
        else:
            r["superseded_by"] = {
                "conflict": True,
                "candidates": [{"memory_id": h, "text": texts.get(h)} for h in v["others"]]}
    return results
