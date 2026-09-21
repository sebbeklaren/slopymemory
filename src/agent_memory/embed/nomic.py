# src/agent_memory/embed/nomic.py
import numpy as np
from agent_memory.config import settings

# What each pinned snapshot must hold for THIS loader — defined once, here; `slopymem install-model` imports these and
# fetches exactly that: the weights repository without the exported formats nothing here reads (onnx/, openvino/: 1.7 GB
# of the 2.2 GB), the code repository's Python modules only. The hub's offline resolution checks its cached tree listing
# against what is asked for — asked for the whole tree, it called the fetched subset incomplete and refused, so no server
# started on any fresh machine (seen on a clean container; the developer machine's older cache carried no listing).
WEIGHTS_IGNORE = ("onnx/*", "openvino/*")
CODE_ALLOW = ("*.py",)


class NomicEmbedder:
    """Real local embedder: nomic-embed-text-v1.5 via sentence-transformers, in-process, CPU.
    Nomic models require task prefixes: 'search_document: ' for stored text, 'search_query: ' for queries.

    Built from the two PINNED snapshot directories in the Hugging Face cache — the weights repository at
    `settings.embed_revision` and the code repository the model's classes come from (trust_remote_code) at
    `settings.embed_code_revision` — never from a repository's moving head:
    - `SentenceTransformer(name, revision=…)` pins only the config/tokenizer/modules files: nomic's own remote code
      loads `model.safetensors` through `cached_file(name, …)` with no revision, i.e. from `main`. Given a local
      DIRECTORY instead, that loader reads the tensors from the directory — the pinned snapshot.
    - `SentenceTransformer(name, model_kwargs={"code_revision": …})` never reaches transformers on sentence-transformers
      5.5 (the key is consumed on the way in). Building the `Transformer` module directly passes `code_revision` to
      BOTH `AutoConfig` and `AutoModel`, as two separate dicts (one shared dict is emptied by the first consumer).
    Both directories are resolved with `local_files_only=True` and the same patterns `slopymem install-model` fetched
    with (WEIGHTS_IGNORE, CODE_ALLOW): a pin that is not in the cache fails loud, naming what fetches it, instead of
    falling back to `main`. Nothing here needs a `refs/main` in the cache — exactly what `slopymem install-model` leaves
    on a fresh machine."""

    model_version = "nomic-embed-text-v1.5"
    MAX_SEQ_LENGTH = 8192          # the pinned snapshot's sentence_bert_config.json
    CODE_REPO = "nomic-ai/nomic-bert-2048"   # named by the snapshot's config.json `auto_map` (`<repo>--<module>.<Class>`)

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        from sentence_transformers.sentence_transformer.modules import Pooling, Transformer
        self.dim = settings.embed_dim
        self.weights_dir = self._local_snapshot(settings.embed_model, settings.embed_revision, "weights")
        self.code_dir = self._local_snapshot(self._code_repo(self.weights_dir), settings.embed_code_revision, "code")
        code = settings.embed_code_revision
        # `code_dir` above is only the fail-loud PRE-CHECK. What binds the code pin is `code_revision` in the two dicts
        # below: transformers resolves `<repo>--<module>.<Class>` from the auto_map through the hub's commit-hash fast
        # path (`snapshots/<pin>/<module>.py`, no refs, no network when present). Do not fold this back into
        # `SentenceTransformer(name, model_kwargs=…)`: that path consumes the key before either loader sees it.
        transformer = Transformer(self.weights_dir, max_seq_length=self.MAX_SEQ_LENGTH,
                                  model_kwargs={"trust_remote_code": True, "code_revision": code},
                                  config_kwargs={"trust_remote_code": True, "code_revision": code})
        # modules.json of the pinned snapshot: Transformer + mean Pooling (1_Pooling/config.json), nothing else
        self._model = SentenceTransformer(modules=[transformer, Pooling(self.dim, pooling_mode="mean")], device="cpu")

    @staticmethod
    def _local_snapshot(repo: str, revision: str, what: str) -> str:
        """The pinned snapshot's directory, offline, resolved against the subset `install-model` fetched for `what`
        ("weights" | "code"): the hub compares its cached tree listing to these patterns, not to the whole tree."""
        from huggingface_hub import snapshot_download
        patterns = {"weights": {"ignore_patterns": list(WEIGHTS_IGNORE)}, "code": {"allow_patterns": list(CODE_ALLOW)}}[what]
        try:
            return snapshot_download(repo, revision=revision, local_files_only=True, **patterns)
        except Exception as e:      # huggingface_hub's own family of "not in the cache" errors
            raise RuntimeError(
                f"the embedder's pinned {what} snapshot is not in the Hugging Face cache: {repo} at revision {revision} "
                f"({type(e).__name__}). Run `slopymem install-model` to fetch exactly that snapshot; the embedder never "
                f"loads a repository's head in its place (every stored coordinate was embedded with this pin)") from e

    @classmethod
    def _code_repo(cls, weights_dir: str) -> str:
        """The repository the model's classes live in, from the snapshot's `auto_map` (falls back to the known one)."""
        import json, os
        try:
            auto_map = json.load(open(os.path.join(weights_dir, "config.json"))).get("auto_map", {})
        except (OSError, ValueError):
            return cls.CODE_REPO
        repos = {v.split("--", 1)[0] for v in auto_map.values() if isinstance(v, str) and "--" in v}
        return repos.pop() if len(repos) == 1 else cls.CODE_REPO

    def _embed(self, text: str, prefix: str) -> np.ndarray:
        v = self._model.encode([f"{prefix}{text}"], normalize_embeddings=True)[0]
        return np.asarray(v, dtype="float32")

    def embed_document(self, text: str) -> np.ndarray:
        return self._embed(text, "search_document: ")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(text, "search_query: ")
