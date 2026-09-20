# src/agent_memory/spaces/projection.py
"""Project 768-dim embeddings -> 3D coordinates for the `semantic` (and other embedding-sourced)
spaces. PCA via numpy SVD: deterministic (no sklearn, no UMAP non-determinism). The 768 is a
coordinate SOURCE, not the model. Fit once on a corpus, then project. `explained_variance_ratio`
reports how much variance the 3 kept components capture (guard against attributing retrieval loss
to dimensionality when it is really the projection)."""
from dataclasses import dataclass

import numpy as np


@dataclass
class Projector:
    mean: np.ndarray                  # (768,)
    components: np.ndarray            # (3, 768), unit rows, sign-fixed
    explained_variance_ratio: float  # fraction of total variance the 3 kept PCs capture

    def project(self, vec: np.ndarray) -> tuple[float, float, float]:
        v = np.asarray(vec, dtype="float64") - self.mean
        c = self.components @ v
        return (float(c[0]), float(c[1]), float(c[2]))


def fit_pca(embeddings: np.ndarray, dims: int = 3) -> Projector:
    X = np.asarray(embeddings, dtype="float64")
    mean = X.mean(axis=0)
    Xc = X - mean
    # right singular vectors = principal axes; rows of Vt are the components
    _, s, Vt = np.linalg.svd(Xc, full_matrices=False)
    comps = Vt[:dims].copy()
    # when n < dims, SVD yields only min(n, dims) components -> pad to exactly `dims` rows with an
    # orthonormal complement (deterministic Gram-Schmidt over the standard basis) so `project`
    # always returns `dims` coords. Done BEFORE the sign-fix so all rows get the same convention.
    if comps.shape[0] < dims:
        basis = list(comps)
        d = comps.shape[1]
        i = 0
        while len(basis) < dims and i < d:
            v = np.zeros(d, dtype="float64")
            v[i] = 1.0
            for b in basis:                      # project out existing basis
                v = v - (b @ v) * b
            nrm = np.linalg.norm(v)
            if nrm > 1e-9:
                basis.append(v / nrm)
            i += 1
        comps = np.vstack(basis)
    # sign convention for determinism: make each component's largest-|value| entry positive
    for i in range(comps.shape[0]):
        j = int(np.argmax(np.abs(comps[i])))
        if comps[i, j] < 0:
            comps[i] = -comps[i]
    total = float((s ** 2).sum())
    evr = float((s[:dims] ** 2).sum() / total) if total > 0 else 0.0
    return Projector(mean=mean, components=comps, explained_variance_ratio=evr)
