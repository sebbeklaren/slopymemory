import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql:///agent_memory")   # no role: the current OS user
    test_database_url: str = os.getenv("TEST_DATABASE_URL", "postgresql:///agent_memory_test")   # likewise
    embed_model: str = os.getenv("AM_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    # The ONE snapshot of the model every stored coordinate was embedded with. The embedder loads this revision,
    # never the repository's moving head: a newer upload under the same model name would shift every stored
    # coordinate without a word. Changing the model or its revision is a MIGRATION (re-embed every store), not a
    # config edit; this variable exists so that migration can be run deliberately.
    embed_revision: str = os.getenv("AM_EMBED_REVISION", "e9b6763023c676ca8431644204f50c2b100d9aab")
    # The model's classes live in a SECOND repository (trust_remote_code: nomic-ai/nomic-bert-2048, named by the
    # model config's auto_map) and are pinned the same way; a moving head there would change the forward pass
    # under the same weights. Passed as code_revision to both the config and the model loader.
    embed_code_revision: str = os.getenv("AM_EMBED_CODE_REVISION", "7710840340a098cfb869c4f65e87cf2b1b70caca")
    embed_dim: int = int(os.getenv("AM_EMBED_DIM", "768"))
    default_k: int = int(os.getenv("AM_DEFAULT_K", "10"))
    min_score: float = float(os.getenv("AM_MIN_SCORE", "-1.0"))
    type_boost_weight: float = float(os.getenv("AM_TYPE_BOOST_WEIGHT", "0.5"))
    candidate_multiplier: int = int(os.getenv("AM_CANDIDATE_MULTIPLIER", "5"))
    extractor_model: str = os.getenv("AM_EXTRACTOR_MODEL", "")
    extractor_base_url: str = os.getenv("AM_EXTRACTOR_BASE_URL", "http://127.0.0.1:8000/v1")
    extractor_version: str = os.getenv("AM_EXTRACTOR_VERSION", "extractor-v1")
    match_tau: float = float(os.getenv("AM_MATCH_TAU", "0.85"))
    sources_dir: str = os.getenv("AM_SOURCES_DIR", "sources")
    default_tenant: str = os.getenv("AM_DEFAULT_TENANT", "default")
    mcp_host: str = os.getenv("AM_MCP_HOST", "127.0.0.1")
    mcp_port: int = int(os.getenv("AM_MCP_PORT", "8765"))
    mcp_surface_floor: float = float(os.getenv("AM_MCP_SURFACE_FLOOR", "0.5"))  # legacy (768 MCP tiering, removed Phase 2); unused
    mcp_surface_core: float = float(os.getenv("AM_MCP_SURFACE_CORE", "0.7"))   # legacy (768 MCP tiering, removed Phase 2); unused
    mcp_invocation_log: str = os.getenv("AM_MCP_INVOCATION_LOG", "runs/mcp_invocations.jsonl")
    ts_decay_lambda: float = float(os.getenv("AM_TS_DECAY_LAMBDA", "0.1"))      # per virtual-day
    ts_storage_bump: float = float(os.getenv("AM_TS_STORAGE_BUMP", "0.5"))      # σ, monotonic
    ts_retrieval_bump: float = float(os.getenv("AM_TS_RETRIEVAL_BUMP", "0.5"))  # ρ, spacing
    ts_sim_floor: float = float(os.getenv("AM_TS_SIM_FLOOR", "0.8"))  # alpha: decay modulates sim only within [alpha,1] (near-tie tiebreaker)
    # Phase-2 constellations are ISOLATED CLIQUES (every aspect bound to every other; no
    # cross-memory synapses yet), so 1 hop already reaches a seeded memory's whole constellation.
    # hop_cap>=2 adds NO new reachable nodes — only back-flow cycles (a->b->a) that inflate dense
    # cliques and let topology dominate relevance (measured: hop=2 recall 0.33 vs hop=1
    # recall 0.83). Raise only once cross-memory synapses create real multi-hop paths AND the wave
    # suppresses back-flow.
    # LIVE-PATH COUPLING: raising hop_cap needs NO companion change to m3_cross_memory_on. Max-relax is
    # the DEFAULT at ALL hops (m3_cross_memory_on below defaults True), so the live path never runs the
    # wave in sum-mode and there is no "bump the two together" hazard. The real criterion for sum!=max is
    # not the hop count but MULTIPLE INCOMING CONTRIBUTIONS to one node — which a dense same-thread
    # clique already hits at hop 1 (a 6-clique's sum-lift evicted a stronger proximity hit). sum==max
    # still holds for sparse single-edge bridges, so the connected-but-not-similar burst value is
    # unchanged. Density-inflation is guarded by tests/{test_burst_density_invariant,test_clique_crowding}.py.
    m3_hop_cap: int = int(os.getenv("AM_M3_HOP_CAP", "1"))
    m3_attenuation: float = float(os.getenv("AM_M3_ATTENUATION", "0.5"))
    # max-relax burst (density->breadth-not-loudness). Default ON (the clique-crowding fix):
    # sum was never the intended density semantics; sum==max for sparse bridges. Env-overridable
    # (AM_M3_CROSS_MEMORY_ON=0 restores legacy sum-mode). retrieve_seeded resolves this from settings when
    # its arg is None (mirrors m3_hop_cap), so the live path picks it up with no call-site signature change.
    m3_cross_memory_on: bool = os.getenv("AM_M3_CROSS_MEMORY_ON", "1").lower() not in ("0", "false", "no", "off", "")
    m3_activation_cutoff: float = float(os.getenv("AM_M3_ACTIVATION_CUTOFF", "0.01"))
    m3_episodic_t0:   float = float(os.getenv("AM_M3_EPISODIC_T0",   "1577836800.0"))  # 2020-01-01Z fixed origin
    m3_episodic_unit: float = float(os.getenv("AM_M3_EPISODIC_UNIT", "86400.0"))       # 1 day; temporal-resolution knob
    # Multi-space Phase A: place the episodic aspect-node per memory? Default ON = byte-identical
    # (existing suites untouched). OFF -> place_memory skips the episodic node (its constellation
    # shrinks by one; semantic/project unaffected). Parsed like m3_cross_memory_on.
    m3_episodic_on: bool = os.getenv("AM_M3_EPISODIC_ON", "1").lower() not in ("0", "false", "no", "off", "")
    neg_strength_bump: float = float(os.getenv("AM_NEG_STRENGTH_BUMP", "1.0"))
    neg_downweight_beta: float = float(os.getenv("AM_NEG_DOWNWEIGHT_BETA", "1.0"))
    m3_acc_decay_lambda:   float = float(os.getenv("AM_M3_ACC_DECAY_LAMBDA",  "0.1"))
    m3_acc_storage_bump:   float = float(os.getenv("AM_M3_ACC_STORAGE_BUMP",  "0.5"))
    m3_acc_retrieval_bump: float = float(os.getenv("AM_M3_ACC_RETRIEVAL_BUMP","0.5"))
    m3_syn_decay_lambda:   float = float(os.getenv("AM_M3_SYN_DECAY_LAMBDA",  "0.1"))
    m3_syn_storage_bump:   float = float(os.getenv("AM_M3_SYN_STORAGE_BUMP",  "0.5"))
    m3_syn_retrieval_bump: float = float(os.getenv("AM_M3_SYN_RETRIEVAL_BUMP","0.5"))
    m3_pos_amplify: float = float(os.getenv("AM_M3_POS_AMPLIFY", "2.0"))  # cited-endpoint bump multiplier (presence-driven, fixed)
    m3_projector_mode:        str   = os.getenv("AM_M3_PROJECTOR_MODE", "bootstrap")   # "bootstrap" | "fixed"
    m3_bootstrap_n:           int   = int(os.getenv("AM_M3_BOOTSTRAP_N", "50"))         # buffer N saves then fit+freeze
    m3_dedup_overfetch:       int   = int(os.getenv("AM_M3_DEDUP_OVERFETCH", "3"))      # k*3 nodes -> >=k memories (max constellation = 3)
    m3_projector_path:        str   = os.getenv("AM_M3_PROJECTOR_PATH", "runs/m3_projector.pkl")
    m3_buffer_path:           str   = os.getenv("AM_M3_BUFFER_PATH", "runs/m3_buffer.jsonl")  # pre-warm WAL; cleared at fit
    m3_background_corpus_path: str  = os.getenv("AM_M3_BACKGROUND_CORPUS_PATH", "gold/substrate/background_corpus.txt")
    # --- Phase B1: multi-space retrieval integration (THE CLEARANCE) ---
    # Directory of per-space facet projectors (projector_<space>.pkl for the four content spaces),
    # produced by scripts/multispace_pass.py. "" = feature OFF (semantic-only seeding, byte-identical
    # to pre-B1). When set, LiveStore.warmup loads each present projector and every query is ALSO
    # seeded through that space's projector transform (the raw query lands in genuinely different
    # per-space neighbourhoods; a memory's own text through the function-projector lands near its own
    # function node — the crowded-twin recovery mechanism).
    m3_facet_projector_dir:   str   = os.getenv("AM_M3_FACET_PROJECTOR_DIR", "")
    # Weight on the FACET-space seed contributions (all non-'semantic' spaces); semantic is always
    # full weight. 1.0 = the spaces are peers (default). 0.0 = facet seeds contribute nothing (the
    # CONTROL: byte-identical to semantic-only). Applied inside retrieve_seeded (per-space).
    m3_facet_seed_weight:     float = float(os.getenv("AM_M3_FACET_SEED_WEIGHT", "1.0"))
    # Breadth-as-importance boost: a memory active in more spaces gets score*(1+beta*(breadth-1)),
    # capped at (1+3*beta). 0.0 = no boost (byte-identical). A bounded multiplier on proximity,
    # never a replacement. Applied memory-level, after max-aggregation dedup, before final top-k.
    # GATED on facet projectors being loaded: with no projectors (dir unset) the boost is a no-op
    # regardless of beta — the production path stays byte-identical to pre-B1 until the clearance
    # canonicalizes and the projector dir is configured. Breadth counts semantic + content-facet
    # spaces ONLY (episodic/project are placement structure, not facet richness — every saved memory
    # has them; see live_store._BREADTH_SPACES).
    m3_breadth_boost:         float = float(os.getenv("AM_M3_BREADTH_BOOST", "0.1"))
    # --- Phase B2: save-time facet extraction (new memories born multi-space) ---
    # extractor_version stamped on m3_facet rows written at SAVE time (provenance only — the prompt
    # source is spaces/facet_prompts.py, versioned + dialect-guarded; this string must NEVER signal
    # prompt/input drift). Distinct default from the Phase-A backfill's claude-phase-a-v1 so the DB
    # records which pass produced each facet.
    m3_save_extractor_version: str = os.getenv("AM_M3_SAVE_EXTRACTOR_VERSION", "claude-live-v1")
    # --- Word-layer concept-vote retrieval: cross-space convergence, NOT TF-IDF ---
    # Sub-additive decay applied within a space when aggregating a memory's concept contributions
    # (sorted desc): c0 + decay*c1 + decay^2*c2 + ... STEEP saturation (default 0.25) => the first
    # concept in a space dominates, so same-space piling barely grows while each distinct space adds
    # near-fully — the distinct-space dominance carries WITHOUT any distinct-space multiplier.
    # decay in [0,1): 0.0 = max-only (hardest saturation); ->1.0 = full sum (the TF-IDF anti-pattern).
    m3_word_saturation_decay: float = float(os.getenv("AM_M3_WORD_SATURATION_DECAY", "0.25"))
    # Steep GENUINE-DEPTH weight on the CROSS-SPACE combine. Each space's
    # within-space saturated vote is scaled by (space_best_act ** depth_steepness) before summing
    # across spaces, where space_best_act = the max ACTIVATION among that memory's hits in that space.
    # activation already encodes burst attenuation (burst-reached hits arrive with LOWER activation),
    # so raising it to a power drives weakly/burst-lit spaces toward 0 while genuine strong spaces
    # (act~1) keep ~full weight — genuine multi-space DEPTH survives, weakly-matched space-BREADTH
    # sinks. 0.0 => act**0 == 1 => EXACT plain-sum (byte-identical backward-compat no-op) — the
    # A/B control. Default 4.0 — the value production runs.
    m3_word_depth_steepness: float = float(os.getenv("AM_M3_WORD_DEPTH_STEEPNESS", "4.0"))
    # REVERSIBLE switch for the concept-primary activation field (one-field retrieval over concepts).
    # "memories" (default) = the current retrieve_memories path, BYTE-IDENTICAL — flipping this back is
    # the one-flag revert. "concept_primary" = route retrieval to retrieve_concept_primary when the
    # caller supplies query_concepts (the agent's query→concept extraction). To run concept-primary
    # retrieval, set AM_M3_RETRIEVAL_MODE=concept_primary AND AM_M3_WORD_DEPTH_STEEPNESS=4.0.
    m3_retrieval_mode: str = os.getenv("AM_M3_RETRIEVAL_MODE", "memories")
    # Master switch for the (B) 768-cosine word-layer (concept-vote lighting). Default OFF =
    # byte-identical to pre-word-layer retrieval (the concept-spaces 11-15 exist but are never
    # seeded). Parsed like m3_cross_memory_on.
    m3_word_layer_on: bool = os.getenv("AM_M3_WORD_LAYER_ON", "0").lower() not in ("0", "false", "no", "off", "")
    # Per-concept-space cosine cap: how many nearest concepts (by concept_embedding cosine) each
    # concept-space contributes to a query's vote tally.
    m3_word_query_concepts: int = int(os.getenv("AM_M3_WORD_QUERY_CONCEPTS", "8"))
    # Provenance stamp for the (B) concept extractor (concept nodes + their vote links). Must NEVER
    # signal input drift — bump only on a genuine extractor/prompt change.
    m3_word_extractor_version: str = os.getenv("AM_M3_WORD_EXTRACTOR_VERSION", "claude-concept-v1")


settings = Settings()
