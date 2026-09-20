# src/agent_memory/spaces/coordinate_sources.py
"""Per-space coordinate sources. Each space's coords come from ITS OWN
source: project ← code-path (prefix hash), episodic ← timestamp (numeric+cyclic), semantic ←
content (embed → background-fit UMAP)."""
import hashlib
import logging
import math
import os as _os
import pickle

import numpy as np

_log = logging.getLogger(__name__)

from agent_memory.spaces.projection_umap import fit_umap

# --- project: cumulative-prefix stable hash ---
_PROJ_W = (1.0, 0.2, 0.04)              # steep per-axis decay: leading (shallowest-differing) axis
                                        # dominates L2 so depth-ordering holds in the large majority
                                        # (statistical — hash gives no min-separation; see tests)


def _hash01(s: str) -> float:
    """Stable (cross-process) hash of a string to [0,1). NOT Python hash() (salted)."""
    return int.from_bytes(hashlib.sha1(s.encode("utf-8")).digest()[:8], "big") / 2 ** 64


def project_coord(code_path: str) -> tuple[float, float, float]:
    """coord_k = weight_k * hash(path-prefix-through-level-k). Two paths sharing the first k levels
    have identical axes 0..k-1, so distance is a function of shared-prefix DEPTH (hash, not index →
    no sibling-adjacency artifact; far-magnitude is hash-arbitrary, so the proof uses binary
    near/not-near)."""
    levels = [p for p in code_path.split("/") if p]
    coord = []
    for k in range(3):
        prefix = "/".join(levels[: min(k + 1, len(levels))]) if levels else ""
        coord.append(_PROJ_W[k] * _hash01(prefix))
    return tuple(coord)


# --- episodic: absolute-time-dominant + down-weighted cyclic time-of-day ---
_EPI_W_ABS, _EPI_W_CYC = 1.0, 0.1       # w_abs >> w_cyc: "temporally near = same instant"


def build_epoch_range(timestamps) -> tuple[float, float]:
    es = [t.timestamp() for t in timestamps]
    return (min(es), max(es))


def episodic_coord(t, epoch_range: tuple[float, float]) -> tuple[float, float, float]:
    """(w_abs*scaled_epoch, w_cyc*sin(2pi*tod), w_cyc*cos(2pi*tod)). Absolute time dominates; the
    cyclic time-of-day is a down-weighted recurrence axis that cannot pull same-clock-time/
    different-instant pairs near. `epoch_range` must span every timestamp being placed (build it
    once over the whole corpus via build_epoch_range — the fixed-corpus batch-fit invariant);
    a timestamp outside the range yields scaled_epoch outside [0,1] by design (not clamped, so an
    out-of-corpus timestamp surfaces rather than being silently absorbed). Sub-second precision is
    intentionally dropped from tod (a coarse recurrence axis)."""
    t_min, t_max = epoch_range
    span = (t_max - t_min) or 1.0
    scaled = (t.timestamp() - t_min) / span
    tod = (t.hour * 3600 + t.minute * 60 + t.second) / 86400.0
    ang = 2 * math.pi * tod
    return (_EPI_W_ABS * scaled, _EPI_W_CYC * math.sin(ang), _EPI_W_CYC * math.cos(ang))


def episodic_coord_fixed(t, t0: float, unit: float) -> tuple[float, float, float]:
    """Drift-free episodic coord for a LIVE store: the absolute axis is (t - t0)/unit against a
    FIXED origin + unit, NOT the corpus min/max (which moves every save -> drift; Catch A). The two
    cyclic time-of-day axes are intrinsic and identical to episodic_coord. t0/unit are config
    (AM_M3_EPISODIC_T0 / AM_M3_EPISODIC_UNIT, env-overridable)."""
    scaled = (t.timestamp() - t0) / unit
    tod = (t.hour * 3600 + t.minute * 60 + t.second) / 86400.0   # duplicated (2 lines) to leave the
    ang = 2 * math.pi * tod                                      # byte-identical legacy fn untouched
    return (_EPI_W_ABS * scaled, _EPI_W_CYC * math.sin(ang), _EPI_W_CYC * math.cos(ang))


def invert_episodic_coord_fixed(coord0, t0: float | None = None, unit: float | None = None):
    """Inverse of episodic_coord_fixed's ABSOLUTE axis (axis 0), for the refit backfill. Forward:
    coord0 = _EPI_W_ABS * (t.timestamp() - t0) / unit, so t.timestamp() = t0 + coord0/_EPI_W_ABS * unit.
    Returns a tz-aware UTC datetime. t0/unit default to config (AM_M3_EPISODIC_T0 / _UNIT).

    PRECISION (honest, load-bearing): m3_node.coord is vector(3) float32, so `coord0` read back from
    the DB has already lost precision vs the original t. Inverting it recovers the ORIGINAL instant
    only within one float32 ULP of the day-offset (~<=10.6 s worst case for t0=2020-01-01/unit=1 day
    over 2025-2030). BUT the value returned is a FIXED POINT: re-placing it through
    episodic_coord_fixed reproduces the SAME float32 coord0 bit-exact — which is the invariant the
    refit actually needs (faithful re-placement, not micro-second time recovery). The two cyclic
    time-of-day axes (1,2) are NOT recoverable from coord0 (sin/cos, coarse, non-injective) — the
    backfill recovers absolute time only; tod is re-derived from the recovered instant."""
    from datetime import datetime, timezone
    from agent_memory.config import settings
    _t0 = settings.m3_episodic_t0 if t0 is None else t0
    _unit = settings.m3_episodic_unit if unit is None else unit
    ts = _t0 + (float(coord0) / _EPI_W_ABS) * _unit
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def fixed_origin_episodic(t0: float | None = None, unit: float | None = None):
    """Return an episodic policy `fn(t) -> coord` bound to the fixed origin/unit (config defaults).
    Pass to place_memory(..., episodic_coord_fn=fixed_origin_episodic()) for the live/smoke path."""
    from agent_memory.config import settings
    _t0 = settings.m3_episodic_t0 if t0 is None else t0
    _unit = settings.m3_episodic_unit if unit is None else unit
    return lambda t: episodic_coord_fixed(t, _t0, _unit)


# --- semantic: content embedding -> UMAP fitted on a BACKGROUND corpus, projected out-of-sample ---
def fit_semantic(embedder, background_contents: list[str], n_neighbors=None):
    """Fit the semantic projector on a BACKGROUND corpus (n >> dims). NEVER on the ~6-memory gold —
    UMAP at n≈d is noise, which would make the semantic gate pass/fail on projection
    noise rather than on the substrate. The background corpus must cover the gold's domain so the
    gold's out-of-sample projection lands meaningfully. n_neighbors passes through to fit_umap
    (None -> historical heuristic, byte-identical default)."""
    embs = np.asarray([embedder.embed_document(c) for c in background_contents], dtype="float64")
    return fit_umap(embs, dims=3, n_neighbors=n_neighbors)


def semantic_coord(projector, embedder, content: str) -> tuple[float, float, float]:
    """Project ONE content through the background-fit projector. A content NOT in the background
    corpus misses the projector's exact-bytes cache and goes through a genuine out-of-sample
    UMAP.transform (NOT a re-fit) — the gold is always projected this way."""
    return projector.project(embedder.embed_document(content))


def save_projector(projector, path: str) -> None:
    """Pickle a fitted projector (PCA or UMAP-backed — both picklable). Caller persists after the
    bootstrap/fixed fit. NOTE: UMAP pickles are version-fragile (numba/sklearn/umap-learn) — deps
    must stay pinned; load_projector re-fits rather than crashing when they drift."""
    _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(projector, f)


def load_projector(path: str):
    """Load a pickled projector, or None if absent/unreadable. Fail-loud-fall-to-fit: on
    a corrupt/incompatible pickle (e.g. a stale file after a code/dep change) LOG a warning and return
    None so the caller re-fits — never crash the server start or silently serve a broken projector."""
    if not _os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as e:                       # corrupt / incompatible / dep-version-mismatch
        _log.warning("projector load failed at %s (%s: %s) — re-fitting", path, type(e).__name__, e)
        return None
