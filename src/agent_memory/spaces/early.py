"""Search before the full search exists. Until a store holds `bootstrap_n` memories its projector is not fitted and its
saves wait in the buffer, so the warm path has nothing to search. Here the question is compared directly with each
buffered memory: dot product of the normalized query and document embeddings (cosine). File anchors and corrections
follow the warm path's rules, from the buffer instead of the database."""
from agent_memory.spaces import file_refs

# buffer row: (memory_id, text, session_key, scope, timestamp, thread, facets, save_concepts, supersedes, files)
_ID, _TEXT, _SUPERSEDES, _FILES = 0, 1, 8, 9


def message(n: int, need: int) -> str:
    return (f"This store has {n} memories. Until it has {need}, search compares your question directly with each "
            f"saved memory; from the {need}th on, the store's full search starts, which also uses concepts, threads "
            "and related memories. Every save is kept. Keep saving decisions as they are made.")


def save_message(n: int, need: int) -> str:
    return f"Saved and already searchable. The store has {n} of {need} memories; at {need} its full search starts."


def search(embedder, buffer: list, cache: dict, query: str, k: int, files: list[dict] | None) -> list[dict]:
    """The buffered memories ranked against `query`, anchored ones first, each corrected one marked superseded_by.
    `cache` maps memory_id -> document vector; it fills as memories are first searched (a restart refills it)."""
    if not buffer:
        return []
    q = embedder.embed_query(query)
    for row in buffer:
        if row[_ID] not in cache:
            cache[row[_ID]] = embedder.embed_document(row[_TEXT])
    scored = sorted(((float(q @ cache[r[_ID]]), i, r) for i, r in enumerate(buffer)), key=lambda t: (-t[0], t[1]))
    results = [{"memory_id": r[_ID], "score": round(s, 4), "text": r[_TEXT]} for s, _, r in scored[:k]]
    texts = {r[_ID]: r[_TEXT] for r in buffer}
    paths = {f["path"] for f in files or ()}
    newest_first = list(reversed(buffer))
    hits = [(r[_ID], r[_TEXT]) for r in newest_first if paths & {f["path"] for f in r[_FILES] or ()}]
    refs = {r[_ID]: [file_refs.shown(f) for f in r[_FILES]] for r in buffer if r[_FILES]}
    results = file_refs.arrange(results, hits, refs, files, k)
    return _corrections(results, buffer, texts)


def _corrections(results: list[dict], buffer: list, texts: dict) -> list[dict]:
    by: dict[str, list[str]] = {}
    for r in buffer:
        if r[_SUPERSEDES]:
            by.setdefault(r[_SUPERSEDES], []).append(r[_ID])
    for res in results:
        start = res["memory_id"]
        if start not in by:
            continue
        seen, heads, cycle, todo = set(), [], False, list(by[start])
        while todo:
            m = todo.pop(0)
            if m == start or m in seen:
                cycle = cycle or m == start
                continue
            seen.add(m)
            if m in by:
                todo.extend(by[m])
            else:
                heads.append(m)
        if len(heads) == 1 and not cycle:
            res["superseded_by"] = {"memory_id": heads[0], "text": texts.get(heads[0])}
        else:
            others = sorted(heads) if heads else sorted(seen)   # the standing versions, as resolve_standing offers them
            res["superseded_by"] = {"conflict": True,
                                    "candidates": [{"memory_id": m, "text": texts.get(m)} for m in others]}
    return results
