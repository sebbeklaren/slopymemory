"""Related memories: the memories strongly tied to a retrieved result, delivered with it in the same reply.

An agent retrieves once and does not ask again, so a memory that no query concept reaches — a naming convention
saved in the same design thread as the button rule the query did reach — is never seen unless it arrives with the
result it belongs to. Each result may carry up to `per_result` of them under `related`.

Strong means one of two ties the saving agent created itself: the same thread (it declared both memories part of
one topic), or at least two shared concepts. A session-only tie does not count. Synapse strengths are not used:
nothing reinforces them yet, so every link reads 1.0 and they cannot rank anything.

ADD-ONLY, like the supersession enrichment: the result list keeps its members, order, scores and texts. A memory
already in the list, already attached to a higher-ranked result, or superseded by a correction is not attached.
No neighbour -> no `related` key (absent, never an empty list); per_result 0 -> the results unchanged."""

MIN_SHARED_CONCEPTS = 2

_NEIGHBOURS = """
WITH me AS (SELECT thread FROM m3_memory WHERE memory_id = %(mid)s),
shared AS (
    SELECT l2.memory_id, count(*) AS n
    FROM m3_concept_link l1 JOIN m3_concept_link l2 ON l2.concept_node = l1.concept_node
    WHERE l1.memory_id = %(mid)s AND l2.memory_id <> %(mid)s
    GROUP BY l2.memory_id)
SELECT m.memory_id, m.text,
       (m.thread IS NOT NULL AND m.thread = (SELECT thread FROM me)) AS same_thread,
       coalesce(s.n, 0) AS shared
FROM m3_memory m LEFT JOIN shared s ON s.memory_id = m.memory_id
WHERE m.memory_id <> %(mid)s
  AND ((m.thread IS NOT NULL AND m.thread = (SELECT thread FROM me)) OR coalesce(s.n, 0) >= %(min_shared)s)
  AND NOT EXISTS (SELECT 1 FROM m3_memory_supersession x WHERE x.stale_memory_id = m.memory_id)
ORDER BY same_thread DESC, shared DESC, m.created_at DESC, m.memory_id
"""


def attach_related(conn, results: list[dict], per_result: int) -> list[dict]:
    if per_result <= 0 or not results:
        return results
    taken = {r["memory_id"] for r in results}
    with conn.cursor() as cur:
        for r in results:                                   # rank order: a higher result claims a neighbour first
            cur.execute(_NEIGHBOURS, {"mid": r["memory_id"], "min_shared": MIN_SHARED_CONCEPTS})
            picked = []
            for mid, text, _same_thread, _shared in cur.fetchall():
                if mid in taken:
                    continue
                picked.append({"memory_id": mid, "text": text})
                taken.add(mid)
                if len(picked) == per_result:
                    break
            if picked:
                r["related"] = picked
    return results
