CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS m3_space (
    id    smallint PRIMARY KEY,
    name  text UNIQUE NOT NULL,
    source text NOT NULL
);

CREATE TABLE IF NOT EXISTS m3_node (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id          smallint NOT NULL REFERENCES m3_space(id),
    label             text NOT NULL,
    kind              text NOT NULL,
    coord             vector(3) NOT NULL,
    layer             text NOT NULL DEFAULT 'everyday',
    source_ref        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_activated_at timestamptz NOT NULL DEFAULT now(),
    activation_count  int NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS m3_node_space_idx ON m3_node (space_id);

CREATE TABLE IF NOT EXISTS m3_synapse (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    src_node           uuid NOT NULL REFERENCES m3_node(id),
    dst_node           uuid NOT NULL REFERENCES m3_node(id),
    type               text NOT NULL DEFAULT 'assoc',
    storage_strength   real NOT NULL DEFAULT 1.0,
    retrieval_strength real NOT NULL DEFAULT 1.0,
    provenance         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_activated_at  timestamptz NOT NULL DEFAULT now(),
    activation_count   int NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS m3_synapse_src_idx ON m3_synapse (src_node);
CREATE UNIQUE INDEX IF NOT EXISTS m3_synapse_pair_uq ON m3_synapse (src_node, dst_node, type);

-- Per-node accessibility (mirrors v1 node strength columns; second strength quantity, proximity-wave gain)
ALTER TABLE m3_node ADD COLUMN IF NOT EXISTS storage_strength    real        NOT NULL DEFAULT 1.0;
ALTER TABLE m3_node ADD COLUMN IF NOT EXISTS retrieval_strength  real        NOT NULL DEFAULT 1.0;
ALTER TABLE m3_node ADD COLUMN IF NOT EXISTS last_activated_at   timestamptz NOT NULL DEFAULT now();

-- Synapse two-strength activated; last_activated_at is the missing piece (strength columns already existed)
ALTER TABLE m3_synapse ADD COLUMN IF NOT EXISTS last_activated_at timestamptz NOT NULL DEFAULT now();

-- The m3 parallel negative_link table (FKs to m3_node, separate from the 768 negative_link)
CREATE TABLE IF NOT EXISTS m3_negative_link (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES m3_node(id) ON DELETE CASCADE,
    target_id   uuid NOT NULL REFERENCES m3_node(id) ON DELETE CASCADE,
    kind        text NOT NULL DEFAULT 'supersession',
    scope       text NOT NULL DEFAULT 'objective',
    strength    real NOT NULL DEFAULT 1.0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS m3_negative_link_pair_uq ON m3_negative_link (source_id, target_id, kind);
CREATE INDEX IF NOT EXISTS m3_negative_link_target_idx ON m3_negative_link (target_id);

-- The m3 positive_link table (unary — a cite names one node; FK to m3_node, mirror-in-
-- discipline of m3_negative_link). kind/scope provenance-only; gating consults link PRESENCE only.
CREATE TABLE IF NOT EXISTS m3_positive_link (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id     uuid NOT NULL REFERENCES m3_node(id) ON DELETE CASCADE,
    kind        text NOT NULL DEFAULT 'validation',
    scope       text NOT NULL DEFAULT 'objective',
    strength    real NOT NULL DEFAULT 1.0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS m3_positive_link_node_uq  ON m3_positive_link (node_id, kind);
CREATE INDEX        IF NOT EXISTS m3_positive_link_node_idx ON m3_positive_link (node_id);

-- Memory-layer text (the compact node's own content). thread/source_ptr are RESERVED:
-- thread = per-thread co-occurrence tag; source_ptr = an
-- archive pointer (not used yet). No FK to m3_node — the memory row is the unit.
CREATE TABLE IF NOT EXISTS m3_memory (
  memory_id  text PRIMARY KEY,
  text       text NOT NULL,
  thread     text,
  source_ptr text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS m3_memory_thread_idx ON m3_memory (thread);

-- Refit policy: make m3_memory SELF-DESCRIBING so a refit re-places every memory
-- faithfully from the DB alone. `scope` = the code-path the PROJECT coord is derived from; `at` =
-- the save timestamp the fixed-origin EPISODIC coord is derived from. Both nullable (legacy rows
-- backfilled by scripts/backfill_memory_meta.py). Idempotent ADD COLUMN IF NOT EXISTS so existing
-- DBs pick them up via apply_schema/_ensure_schema.
ALTER TABLE m3_memory ADD COLUMN IF NOT EXISTS scope text;
ALTER TABLE m3_memory ADD COLUMN IF NOT EXISTS at    timestamptz;

-- Multi-space support: one facet DESCRIPTION per (memory, content-space). Self-describing
-- store — the placement pass fits one projector per populated space on ITS own facet corpus.
-- CONTRAST with m3_memory: the memory `text` is an IMMUTABLE record (upsert DO NOTHING), a facet is
-- DERIVED + REFINABLE (upsert DO UPDATE — re-extraction may sharpen it). NEVER-PAD: an empty facet
-- is NO ROW, not a blank row — the CHECK is the DB backstop (upsert_facet refuses in Python too).
CREATE TABLE IF NOT EXISTS m3_facet (
    memory_id         text NOT NULL,
    space             text NOT NULL,
    text              text NOT NULL,
    extractor_version text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, space),
    CHECK (length(btrim(text)) > 0)
);

-- Word-layer (768-cosine concept lighting). Concept nodes live in DEDICATED concept-spaces
-- (isolation: the current retrieval never seeds them) and store their OWN 768-dim embedding for
-- cosine lighting — they are NOT 3D-placed (coord is a dummy). Nullable: only kind='concept' rows
-- populate it. The whole layer is reversible (drop the column + table + concept nodes).
ALTER TABLE m3_node ADD COLUMN IF NOT EXISTS concept_embedding vector(768);

-- concept->text vote links. ON DELETE CASCADE so dropping a concept node cleans its links.
CREATE TABLE IF NOT EXISTS m3_concept_link (
    concept_node uuid NOT NULL REFERENCES m3_node(id) ON DELETE CASCADE,
    memory_id    text NOT NULL,
    strength     real NOT NULL DEFAULT 1.0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (concept_node, memory_id)
);
CREATE INDEX IF NOT EXISTS m3_concept_link_memory_idx  ON m3_concept_link (memory_id);
CREATE INDEX IF NOT EXISTS m3_concept_link_concept_idx ON m3_concept_link (concept_node);

-- Supersession binding: MEMORY-level user-confirmed corrections. Deliberately NOT
-- node-level (m3_negative_link is FK'd to m3_node and per-aspect linking silently writes zero rows
-- when no spaces are shared — fatal for a mechanism whose job is catching the invisible case).
-- `strength` is an INERT SEAM (repeat adjudication bumps it; nothing reads it yet) — like the
-- synapse/decay seams, do not assume it does work. No ranking effect anywhere: links are surfaced
-- in retrieval payloads (ADD-ONLY), never used to demote/reorder.
CREATE TABLE IF NOT EXISTS m3_memory_supersession (
    stale_memory_id text NOT NULL REFERENCES m3_memory(memory_id) ON DELETE CASCADE,
    by_memory_id    text NOT NULL REFERENCES m3_memory(memory_id) ON DELETE CASCADE,
    strength        real NOT NULL DEFAULT 1.0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stale_memory_id, by_memory_id),
    CHECK (stale_memory_id <> by_memory_id)
);
