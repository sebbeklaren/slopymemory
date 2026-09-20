# src/agent_memory/spaces/projection_umap.py
"""UMAP projector with the same interface as projection.Projector, for the Phase-2.5 PCA-vs-UMAP
fidelity comparison. Training points (the nodes) return their FITTED embedding (UMAP's best, via an
exact-bytes cache); unseen points (queries) go through out-of-sample .transform(). UMAP has no EVR,
so explained_variance_ratio is NaN."""
import numpy as np


class UMAPProjector:
    def __init__(self, model, fit_vectors: np.ndarray, fit_embedding: np.ndarray):
        self._model = model
        self.explained_variance_ratio = float("nan")
        # cache fitted coords by exact bytes of each training row (float64)
        self._cache = {fit_vectors[i].tobytes(): tuple(float(x) for x in fit_embedding[i])
                       for i in range(len(fit_vectors))}

    def project(self, vec) -> tuple:
        v = np.asarray(vec, dtype="float64")
        hit = self._cache.get(v.tobytes())
        if hit is not None:
            return hit
        emb = self._model.transform(v.reshape(1, -1))[0]
        return tuple(float(x) for x in emb)


def fit_umap(embeddings, dims: int = 3, random_state: int = 0, n_neighbors=None) -> UMAPProjector:
    """n_neighbors=None -> the historical min(15, max(2, n-1)) heuristic (byte-identical default);
    an explicit value is honored but still capped at n-1 (UMAP requires n_neighbors < n_samples).
    Exposed so an upcoming experiment can sweep n_neighbors without editing the fit."""
    import umap
    X = np.asarray(embeddings, dtype="float64")
    if n_neighbors is None:
        n_neighbors = min(15, max(2, len(X) - 1))       # UMAP requires n_neighbors < n_samples
    else:
        n_neighbors = min(n_neighbors, len(X) - 1)
    model = umap.UMAP(n_components=dims, random_state=random_state, n_neighbors=n_neighbors)
    emb = model.fit_transform(X)
    return UMAPProjector(model, X, np.asarray(emb, dtype="float64"))
