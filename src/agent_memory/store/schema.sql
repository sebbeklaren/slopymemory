-- src/agent_memory/store/schema.sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS nodes (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label                   text NOT NULL,
    embed_text              text NOT NULL,
    embedding               vector(768) NOT NULL,
    embedding_model_version text NOT NULL,
    types                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    kind                    text NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()  -- reserved; nothing updates it yet
);

-- sources: raw-source half of the artifact tier (summary/hub fields deferred)
CREATE TABLE IF NOT EXISTS sources (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    uri         text NOT NULL,
    kind        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- extractor-v1 node columns (nullable so pre-extractor fixture nodes stay valid)
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS normalized_label  text;
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS provenance        uuid;      -- logical ref to sources.id (no hard FK in v1)
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS extractor_version text;
CREATE INDEX IF NOT EXISTS idx_nodes_kind_normlabel ON nodes (kind, normalized_label);

-- two-strength dynamics columns
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS storage_strength   real        NOT NULL DEFAULT 1.0;
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS retrieval_strength real        NOT NULL DEFAULT 1.0;
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS last_activated_at  timestamptz NOT NULL DEFAULT now();

-- negative-signal relation (supersession v1)
CREATE TABLE IF NOT EXISTS negative_link (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,   -- B (superseding/asserting)
    target_id   uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,   -- A (whose reinforcement is gated)
    kind        text NOT NULL DEFAULT 'supersession',   -- PROVENANCE only; gating never branches on this
    scope       text NOT NULL DEFAULT 'objective',      -- PROVENANCE only; at m3 derived from node's space
    strength    real NOT NULL DEFAULT 1.0,              -- confidence-by-repetition; near-inert for the gate
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS negative_link_pair_uq ON negative_link (source_id, target_id, kind);
CREATE INDEX IF NOT EXISTS negative_link_target_idx ON negative_link (target_id);
