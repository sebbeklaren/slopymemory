# src/agent_memory/strength.py
"""Pure two-strength math (no DB, no I/O). Decay is storage-modulated exponential
(synapse-space.md: retrieval decays at a rate inversely proportional to storage);
reinforcement uses the Bjork spacing effect (bump proportional to 1-retrieval)."""
import math
from datetime import datetime


def age_days(last_activated_at: datetime, now: datetime) -> float:
    dt = (now - last_activated_at).total_seconds() / 86400.0
    return dt if dt > 0.0 else 0.0


def effective_retrieval(retrieval_strength: float, storage_strength: float,
                        last_activated_at: datetime, now: datetime, lambda_: float) -> float:
    """What search sees after lazy-on-read decay. storage_strength >= 1 slows decay."""
    dt = age_days(last_activated_at, now)
    return retrieval_strength * math.exp(-(lambda_ / storage_strength) * dt)


def reinforce_coupled(storage_strength: float, retrieval_strength: float,
                      last_activated_at: datetime, now: datetime,
                      lambda_: float, sigma: float, rho: float) -> tuple[float, float]:
    """Couple the two channels: decay the stored retrieval to `now`, THEN spacing-boost the
    decayed base, and bump storage. Boosting the decayed (not stale-1.0) base is what makes a
    dormant node's reinforcement large (spacing) and 'forgotten-but-recoverable' representable.

    Returns: (new_storage_strength, new_retrieval_strength)."""
    decayed = effective_retrieval(retrieval_strength, storage_strength, last_activated_at, now, lambda_)
    new_retrieval = decayed + rho * (1.0 - decayed)   # <= 1.0 since decayed in [0,1]
    return storage_strength + sigma, new_retrieval


def tiebreak_score(sim: float, eff: float, alpha: float) -> float:
    """Bounded tiebreaker: effective-retrieval modulates similarity only within [alpha, 1],
    so decay reorders near-ties but cannot flip a clear-sim winner."""
    return sim * (alpha + (1.0 - alpha) * eff)
