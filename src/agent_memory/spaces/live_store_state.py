# src/agent_memory/spaces/live_store_state.py
"""LiveStore: the one stateful unit for the live MCP memory server. Holds the (loaded/None) semantic
projector, the bootstrap buffer, and the in-memory {session_key: [semantic_node_id]} co-occurrence
map. save()/retrieve() orchestrate the warming-vs-warm logic; all placement/retrieval/edge logic
lives in the stateless spaces primitives (save_memory, retrieve_memories, coordinate_sources)."""
import json
import logging
import os
import uuid
from pathlib import Path

from agent_memory.config import settings
from agent_memory.spaces import coordinate_sources as cs
from agent_memory.spaces.live_store import (save_memory, retrieve_memories, _memory_texts,
                                            thread_semantic_nodes, place_facets)
from agent_memory.spaces.concepts import place_text_concepts
from agent_memory.spaces.memory_supersession import note_memory_supersession, attach_supersessions

log = logging.getLogger(__name__)

# Multi-space retrieval: the four content-facet spaces whose per-space projectors seed a query alongside semantic.
_FACET_SPACES = ("function", "feeling", "fiction", "player_facing")


def load_facet_projectors(projector_dir: str) -> dict:
    """Load projector_<space>.pkl for each content-facet space present in `projector_dir`. Empty dir
    string -> {} (feature OFF). A missing file skips that space (logged); a corrupt one is handled by
    cs.load_projector (warn -> None -> skipped). Shared by LiveStore.warmup and the self-retrieval
    scan so the env-driven facet-seeding path loads ONE way (no duplicated loop, no signature change
    on the scan)."""
    out: dict = {}
    if not projector_dir:
        return out
    for space in _FACET_SPACES:
        proj = cs.load_projector(os.path.join(projector_dir, f"projector_{space}.pkl"))
        if proj is None:
            log.info("facet projector absent for space=%s in %s — space skipped", space, projector_dir)
        else:
            out[space] = proj
    return out


def _normalize_thread(thread) -> str | None:
    """strip+lowercase; empty/whitespace ⇒ absent. A CONTENT tag, never a session id."""
    if not isinstance(thread, str):
        return None
    t = thread.strip().lower()
    return t or None


class LiveStore:
    def __init__(self, embedder, *, mode=None, bootstrap_n=None, projector=None, fit_fn=cs.fit_semantic):
        """bootstrap_n must be >= 4 for the default UMAP fit_fn (n_samples > dims=3); tests inject
        a stub fit_fn so smaller values are fine there."""
        self.embedder = embedder
        self.mode = mode or settings.m3_projector_mode
        self.bootstrap_n = bootstrap_n or settings.m3_bootstrap_n
        self.fit_fn = fit_fn
        self.projector = projector                 # None until warm
        self._buffer = []                          # [(memory_id, text, session_key, scope, timestamp, thread, facets, save_concepts, supersedes)]
        self._session_nodes = {}    # session_key -> [semantic node_id]; in-memory by design:
                                    # DB keeps memories+edges across restart, only interrupted-session linking is lost
        self._epi = cs.fixed_origin_episodic()
        self.facet_projectors = {}                 # multi-space retrieval: {space: projector}; empty = feature OFF.
        self._facet_projectors_loaded = False      # load-once latch (warmup OR first retrieve)

    @property
    def warm(self) -> bool:
        return self.projector is not None

    def _ensure_facet_projectors(self) -> None:
        """Load the per-space facet projectors once (idempotent). Runs at warmup() AND lazily at first
        retrieve (so an injected-projector warm store — constructed without warmup — still picks them
        up). settings.m3_facet_projector_dir == "" -> feature OFF (leaves facet_projectors untouched,
        so a test may inject them directly). A missing projector_<space>.pkl skips that space (printed
        once); a corrupt one is handled by cs.load_projector (warn -> None -> skipped)."""
        if self._facet_projectors_loaded:
            return
        self._facet_projectors_loaded = True
        loaded = load_facet_projectors(settings.m3_facet_projector_dir)
        if loaded:                                   # empty dir -> preserve any test-injected projectors
            self.facet_projectors.update(loaded)
            log.info("loaded %d facet projector(s) from %s: %s",
                     len(loaded), settings.m3_facet_projector_dir, sorted(loaded))

    def warmup(self) -> None:
        """At server start: warm-load a persisted projector if present (survives restart); else for
        `fixed` mode fit once on the background corpus; `bootstrap` stays cold — but first recover
        any WAL left by a pre-warm restart (the buffered saves are real content, e.g. interview
        answers; losing them silently is the failure this removes). A WAL alongside a warm/fixed
        path is inconsistent (fit clears it) -> fail loud, don't guess."""
        from datetime import datetime
        self._ensure_facet_projectors()             # multi-space retrieval: independent of the semantic warm state
        wal = Path(settings.m3_buffer_path)
        loaded = cs.load_projector(settings.m3_projector_path)
        if loaded is not None:
            if wal.exists():
                raise RuntimeError(
                    f"buffer WAL {wal} exists alongside a persisted projector — fit should have "
                    "cleared it; refusing to guess (inspect + remove or replay manually)")
            self.projector = loaded
            return
        if self.mode == "fixed":
            if wal.exists():
                raise RuntimeError(
                    f"buffer WAL {wal} exists but mode=fixed never buffers — mode/state mismatch; "
                    "refusing to guess (inspect + remove or replay manually)")
            with open(settings.m3_background_corpus_path) as f:
                bg = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            self.projector = self.fit_fn(self.embedder, bg)
            cs.save_projector(self.projector, settings.m3_projector_path)
            return
        if wal.exists():                                        # bootstrap cold path: recover
            for ln in wal.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                self._buffer.append((r["memory_id"], r["text"], r["session_key"],
                                     r["scope"], datetime.fromisoformat(r["now"]), r.get("thread"),
                                     r.get("facets"),           # absent in WALs from before facets existed -> None
                                     r.get("save_concepts"),    # absent in pre-concept WALs -> None
                                     r.get("supersedes")))      # absent in pre-supersession WALs -> None

    def _next_id(self) -> str:
        return uuid.uuid4().hex          # process-restart-safe (a per-process counter collides on source_ref)

    def _wal_append(self, mid, text, session_key, scope, now, thread=None, facets=None,
                    save_concepts=None, supersedes=None) -> None:
        """Write-ahead one buffered save (pre-warm only): full record to disk BEFORE
        returning 'buffered', so a restart can never silently lose real content (including
        power loss, via fsync). B2: `facets` rides the record (backward-compatible — read with
        .get, like `thread`) so buffered saves place their facets at fit time, not just semantic.
        `save_concepts` rides the same way so buffered saves place their concepts at fit time too.
        `supersedes` rides the same way so a buffered correction's supersession link is written at
        fit time (after the target's row is replayed in)."""
        path = Path(settings.m3_buffer_path)
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"memory_id": mid, "text": text, "session_key": session_key,
                                "scope": scope, "now": now.isoformat(), "thread": thread,
                                "facets": facets, "save_concepts": save_concepts,
                                "supersedes": supersedes},
                               ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _wal_clear(self) -> None:
        Path(settings.m3_buffer_path).unlink(missing_ok=True)

    def _place(self, conn, mid, text, session_key, scope, now, thread=None, facets=None,
               save_concepts=None):
        """Place one memory (semantic + episodic/project + co-occurrence wiring), then — B2 — persist
        + (iff projectors loaded) place its content facets, star-bound to the new semantic node, and —
        concept-primary — place/dedup/link the memory's typed concepts (768-cosine layer). Returns
        (ids, facets_placed, facets_deferred, concepts_placed). Shared by BOTH the warm path and the
        buffered fit-time replay, so facet + concept placement is identical on both routes."""
        if thread:
            # DB-backed durable wiring: the thread's existing members, regardless of session or
            # process lifetime. Looked up BEFORE save_memory inserts the new row (no self-edge).
            prior = thread_semantic_nodes(conn, thread)
        else:
            prior = list(self._session_nodes.get(session_key, []))
        ids = save_memory(conn, self.projector, self.embedder, self._epi, ref=mid, text=text,
                          timestamp=now, scope=scope, thread=thread,
                          prior_session_nodes=prior)
        if not thread:
            # tagged saves never join the session clique — the thread IS their boundary
            self._session_nodes.setdefault(session_key, []).append(ids["semantic"])
        placed, deferred = [], []
        if facets:                                             # the gate: no facets -> byte-identical
            placed, deferred = place_facets(
                conn, memory_id=mid, semantic_node_id=ids["semantic"], facets=facets,
                facet_projectors=self.facet_projectors, embedder=self.embedder,
                extractor_version=settings.m3_save_extractor_version)
        concepts_placed = 0
        if save_concepts:                                      # the gate: no concepts -> byte-identical
            concepts_placed = len(place_text_concepts(
                conn, mid, [tuple(c) for c in save_concepts], self.embedder))
        return ids, placed, deferred, concepts_placed

    def _fit_and_place(self, conn) -> dict:
        """Fit the semantic projector on the buffered texts, then replay every buffered save through
        _place (which places each memory's facets AND concepts too). Returns
        {memory_id: (placed, deferred, concepts_placed)} so the triggering save() can report ITS own
        facet/concept placement accurately."""
        self.projector = self.fit_fn(self.embedder, [b[1] for b in self._buffer])
        results: dict = {}
        pending: list = []           # supersession links, written AFTER placement (both rows must exist)
        for mid, text, session_key, scope, ts, thread, facets, save_concepts, supersedes in self._buffer:
            _, placed, deferred, concepts_placed = self._place(
                conn, mid, text, session_key, scope, ts, thread, facets, save_concepts)
            results[mid] = (placed, deferred, concepts_placed)
            if supersedes:
                pending.append((mid, supersedes))
        for mid, sup in pending:     # all buffered rows now exist -> FK targets are satisfiable
            note_memory_supersession(conn, mid, sup)
        self._buffer = []
        cs.save_projector(self.projector, settings.m3_projector_path)
        self._wal_clear()
        return results

    def save(self, conn, text, *, session_key, now=None, scope=None, memory_id=None, thread=None,
             facets=None, save_concepts=None, supersedes=None) -> dict:
        """Optional `facets` = {content-space: text} (validated at the MCP boundary). When
        provided + warm, each facet is persisted (m3_facet) and placed into its space (iff its
        projector is loaded) + bound to the semantic node; the reply gains facets_placed /
        facets_deferred. facets=None/empty -> byte-identical to before facets existed (no facet keys in the reply).
        Buffered saves carry facets on the WAL and place them at fit time (reported deferred until the
        fit; the triggering warm-up save reports its actual placement).

        Concept-primary: optional `save_concepts` = list of [base_space, label] pairs (same dialect as
        retrieve's query_concepts). When provided the memory's typed concepts are placed/deduped/linked
        (768-cosine layer) so the save is immediately concept-retrievable; the reply gains
        concepts_placed = <n>. save_concepts=None/empty -> byte-identical (no concepts_placed key).
        Buffered saves carry save_concepts on the WAL and place them at fit time. `now` defaults to
        the current UTC instant when omitted.

        Supersession: optional `supersedes` = the memory_id of a stored memory this save corrects (on
        the user's explicit confirmation). FAIL-LOUD pre-check BEFORE any placement — the target must exist in
        m3_memory OR the current bootstrap buffer; an unknown/typo'd id returns {status: error} and
        NOTHING is saved (never a half-success). When it holds, the memory-level supersession link
        (by=this save, stale=target) is written — immediately on the warm path, or after the fit-time
        replay for a buffered save (so the buffered target's row exists first) — and the reply gains
        `supersedes`. None/empty -> byte-identical to a save without it."""
        mid = memory_id or self._next_id()
        thread = _normalize_thread(thread)
        if not text or not text.strip():
            return {"status": "noop", "note": "empty input — nothing saved"}
        supersedes = supersedes.strip() if isinstance(supersedes, str) else None
        supersedes = supersedes or None
        if supersedes:                                          # fail-loud existence check, pre-placement
            buffered_ids = {b[0] for b in self._buffer}
            if supersedes not in buffered_ids:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM m3_memory WHERE memory_id = %s", (supersedes,))
                    if cur.fetchone() is None:
                        return {"status": "error",
                                "note": f"supersedes target {supersedes} not found — nothing saved"}
        if now is None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
        if facets:
            self._ensure_facet_projectors()                    # load per-space projectors before placement
        if not self.warm:                                       # bootstrap buffering
            self._buffer.append((mid, text, session_key, scope, now, thread, facets, save_concepts,
                                 supersedes))
            self._wal_append(mid, text, session_key, scope, now, thread, facets, save_concepts,
                             supersedes)
            if len(self._buffer) >= self.bootstrap_n:
                results = self._fit_and_place(conn)             # writes buffered supersession links post-replay
                out = {"status": "saved", "memory_id": mid, "warmed": True}
                placed, deferred, concepts_placed = results.get(mid, ([], [], 0))
                if supersedes:
                    out["supersedes"] = supersedes
                if facets:
                    out["facets_placed"] = placed
                    out["facets_deferred"] = deferred
                if save_concepts:
                    out["concepts_placed"] = concepts_placed
                return out
            out = {"status": "buffered", "warming": True,
                   "count": len(self._buffer), "need": self.bootstrap_n}
            if supersedes:                                      # recorded on the WAL, link written at fit
                out["supersedes"] = supersedes
            if facets:                                          # persisted on the WAL, not yet placed
                out["facets_placed"] = []
                out["facets_deferred"] = sorted(facets)
            return out
        _, placed, deferred, concepts_placed = self._place(
            conn, mid, text, session_key, scope, now, thread, facets, save_concepts)
        out = {"status": "saved", "memory_id": mid}
        if supersedes:
            note_memory_supersession(conn, mid, supersedes)
            out["supersedes"] = supersedes
        if facets:
            out["facets_placed"] = placed
            out["facets_deferred"] = deferred
        if save_concepts:
            out["concepts_placed"] = concepts_placed
        return out

    def retrieve(self, conn, query, k, *, query_facets=None, facets=None, epoch_range=None,
                 query_concepts=None, **kwargs) -> dict:
        if not self.warm:
            return {"status": "warming up", "results": []}
        self._ensure_facet_projectors()             # lazy load for an injected-projector warm store
        # REVERSIBLE concept-primary switch: when the mode flag is on AND the caller supplied the
        # agent's query→concepts, retrieve as ONE activation field over concepts. The flag
        # defaulting to "memories" (or query_concepts absent) leaves the path below byte-identical —
        # the one-flag revert. retrieve_concept_primary already returns [{memory_id, score, text}].
        if settings.m3_retrieval_mode == "concept_primary" and query_concepts:
            from agent_memory.spaces.word_vote import retrieve_concept_primary
            results = retrieve_concept_primary(conn, self.embedder, query_concepts, k)
            return {"status": "ok", "results": attach_supersessions(conn, results)}
        # query_facets (B1 enhancer): None -> raw-text fallback; dict (possibly {}) -> in-distribution
        # facet seeding (no raw-text facet transforms). `facets` is the legacy project/episodic knob.
        mems = retrieve_memories(conn, self.projector, self.embedder, query, k,
                                 query_facets=query_facets, facets=facets, epoch_range=epoch_range,
                                 facet_projectors=self.facet_projectors, **kwargs)
        texts = _memory_texts(conn, [m for m, _ in mems])
        if any(m not in texts for m, _ in mems):
            log.warning("retrieve: %d/%d results have no text row (pre-fix legacy data)",
                        sum(1 for m, _ in mems if m not in texts), len(mems))
        results = [{"memory_id": m, "score": round(s, 4), "text": texts.get(m)} for m, s in mems]
        return {"status": "ok", "results": attach_supersessions(conn, results)}
