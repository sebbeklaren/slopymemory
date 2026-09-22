# src/agent_memory/spaces/facets.py
"""The m3_facet store (multi-space support). A facet is a short, facet-pure description of ONE
aspect of a memory — its function / feeling / fiction / player-facing meaning — extracted per
content-space. The placement pass fits one projector per populated space on ITS facet corpus and
places one node per (memory, space).

CONTRAST with m3_memory (spaces/live_store): the memory TEXT is an IMMUTABLE record — its upsert is
ON CONFLICT DO NOTHING, deliberately, so a re-save never rewrites the original words. A FACET is
DERIVED + REFINABLE — its upsert is ON CONFLICT DO UPDATE, so a re-extraction with a sharper
prompt/version replaces the prior description. NEVER-PAD: an empty/whitespace facet is NO ROW (this
module raises; the DB CHECK length(btrim(text))>0 is the backstop)."""
import psycopg


def upsert_facet(conn: psycopg.Connection, memory_id: str, space: str, text: str,
                 extractor_version: str | None) -> None:
    """Insert or SHARPEN the facet for (memory_id, space). ON CONFLICT DO UPDATE because facets are
    derived + refinable (unlike m3_memory's DO NOTHING immutable record — see module docstring).
    Refuses empty/whitespace text with ValueError (never-pad: an empty facet is NO ROW); the DB
    CHECK is the backstop if a raw INSERT bypasses this helper. `created_at` is left untouched on a
    sharpen — it marks the row's first creation."""
    if not text or not text.strip():
        raise ValueError(f"facet text must be non-empty/non-whitespace (memory_id={memory_id!r}, "
                         f"space={space!r}); an empty facet is NO ROW, not a blank row")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO m3_facet (memory_id, space, text, extractor_version) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (memory_id, space) DO UPDATE "
            "SET text = EXCLUDED.text, extractor_version = EXCLUDED.extractor_version",
            (memory_id, space, text, extractor_version),
        )


def facets_for_space(conn: psycopg.Connection, space: str) -> list[tuple[str, str]]:
    """All (memory_id, text) facets in one space, ordered by memory_id — the placement pass turns
    each row into one node in that space's projected coordinate frame."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT memory_id, text FROM m3_facet WHERE space = %s ORDER BY memory_id",
            (space,),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]
