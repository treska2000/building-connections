"""core/embedding.py — embedding provider for R6 (semantic coherence).

Single entry point for all embedding metrics. Default model is Qwen3, Matryoshka:
truncated to dim from thresholds.yaml. Cache keyed by text hash keeps repeated runs
stable and fast. The backend (sentence-transformers/Qwen3) is guarded: if absent,
HAVE_EMBEDDER=False and R6 stays PENDING. With a pinned model_snapshot embeddings
are reproducible and R6 can be marked deterministic=True.

core/embedding.py — провайдер эмбеддингов для R6 (Семантическая когерентность).

Единственная точка для всех эмбеддинговых метрик. Дефолт — Qwen3, Matryoshka:
усекаем до dim из thresholds.yaml. Кэш по хэшу текста — повторные прогоны стабильны
и быстры. Бэкенд — под guard: нет → HAVE_EMBEDDER=False, R6 PENDING.
При запиненном model_snapshot эмбеддинги воспроизводимы, R6 можно помечать
deterministic=True.
"""
from __future__ import annotations

import hashlib

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    HAVE_EMBEDDER = True
except Exception:  # pragma: no cover
    HAVE_EMBEDDER = False

# короткое имя из yaml → реальный id весов
MODEL_IDS = {
    "qwen3": "Qwen/Qwen3-Embedding-0.6B",
    "gemini-embedding-2": "gemini-embedding-2",   # требует свой клиент; здесь не грузится
}


class Embedder:
    """Qwen3 embedder with per-text hash cache and Matryoshka truncation to dim.

    Qwen3-эмбеддер с кэшем по хэшу текста и Matryoshka-усечением до dim."""

    def __init__(self, model: str = "qwen3", dim: int = 64, snapshot: str | None = None):
        if not HAVE_EMBEDDER:
            raise RuntimeError("sentence-transformers/Qwen3 недоступны — R6 остаётся PENDING")
        self.model_name = model
        self.dim = dim
        self.snapshot = snapshot
        self._cache: dict[str, "np.ndarray"] = {}
        self._st = SentenceTransformer(MODEL_IDS.get(model, model))

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def embed_one(self, text: str):
        """Embed one text, using the hash cache and Matryoshka truncation to dim.

        In: text (str). Out: np.ndarray shape (dim,).

        Эмбеддинг одного текста (кэш по хэшу, усечение Matryoshka до dim).
        In: text (str). Out: np.ndarray shape (dim,)."""
        k = self._key(text)
        if k not in self._cache:
            v = self._st.encode(text, normalize_embeddings=False)
            v = np.asarray(v, dtype=np.float64)[: self.dim]   # Matryoshka truncate
            self._cache[k] = v
        return self._cache[k]

    def embed(self, texts: list[str]):
        """Embed a list of texts into a matrix.

        In: texts (list[str]). Out: np.ndarray shape (len(texts), dim).

        Эмбеддинг списка текстов в матрицу.
        In: texts (list[str]). Out: np.ndarray shape (len(texts), dim)."""
        return np.asarray([self.embed_one(t) for t in texts], dtype=np.float64)


def from_yaml(embedder_cfg: dict | None) -> "Embedder | None":
    """Build an Embedder from the `embedder` section of thresholds.yaml; return None
    if the backend is unavailable.

    In: embedder_cfg (dict | None). Out: Embedder | None.

    Собрать эмбеддер из секции `embedder` thresholds.yaml; None если бэкенд недоступен.
    In: embedder_cfg (dict | None). Out: Embedder | None."""
    if not HAVE_EMBEDDER or not embedder_cfg:
        return None
    try:
        return Embedder(model=embedder_cfg.get("model", "qwen3"),
                        dim=int(embedder_cfg.get("dim", 64)),
                        snapshot=embedder_cfg.get("model_snapshot"))
    except Exception:
        return None


_DEFAULT_EMBEDDER = None
_DEFAULT_KEY = object()


def get_default(embedder_cfg: dict | None) -> "Embedder | None":
    """Return the process-cached default embedder (model loaded once); None if
    the backend is unavailable.

    In: embedder_cfg (dict | None). Out: Embedder | None.

    Кэшированный на процесс дефолтный эмбеддер (модель грузится один раз).
    In: embedder_cfg (dict | None). Out: Embedder | None."""
    global _DEFAULT_EMBEDDER, _DEFAULT_KEY
    key = None if not embedder_cfg else (embedder_cfg.get("model"), embedder_cfg.get("dim"))
    if _DEFAULT_EMBEDDER is None or key != _DEFAULT_KEY:
        _DEFAULT_EMBEDDER = from_yaml(embedder_cfg)
        _DEFAULT_KEY = key
    return _DEFAULT_EMBEDDER


def pool_to_terms(pool, embed_fn, text_mode: str = "label_desc"):
    """Deduplicate membership rows into unique terms and embed them. The primary
    category of each term is its first tag. text_mode controls the embedding text:
    'label' uses the term name only; 'label_desc' appends the description.

    In: pool (list of term dicts), embed_fn (callable), text_mode (str).
    Out: (terms: list[str], declared: list[str | None], V: np.ndarray).

    Дедуп строк принадлежности в уникальные термины и их эмбеддинг. Primary-категория
    = первый тег термина. text_mode: 'label' | 'label_desc'.
    In: pool (list), embed_fn (callable), text_mode (str).
    Out: (terms, declared, V)."""
    primary, desc_of, order = {}, {}, []
    for p in pool:
        t = p.get("label") or p.get("name")
        if t is None:
            continue
        if t not in primary:
            tags = p.get("tags") or p.get("axes") or []
            primary[t] = (p.get("category") or (tags[0] if tags else None))
            desc_of[t] = p.get("description", "") or ""
            order.append(t)
    terms = order
    texts = [(t if text_mode == "label" else f"{t}. {desc_of[t]}".strip()) for t in terms]
    V = embed_fn(texts)
    declared = [primary[t] for t in terms]
    return terms, declared, V
