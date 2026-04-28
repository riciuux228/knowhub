"""Embedding model for QMD — sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2).

Replaces the previous GGUF model. Same 384-dim output, ~20x faster on CPU.
Model weights shared with gitmem0 daemon (already cached in container).
"""

import numpy as np

EMBED_DIM = 384
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model = None


def get_model():
    """Get or create the singleton SentenceTransformer model."""
    global _model
    if _model is not None:
        return _model

    from sentence_transformers import SentenceTransformer

    print(f"[QMD] Loading SentenceTransformer: {_MODEL_NAME}", flush=True)
    _model = SentenceTransformer(_MODEL_NAME)
    print(f"[QMD] SentenceTransformer loaded (dim={EMBED_DIM})", flush=True)
    return _model


def embed_text(text: str) -> np.ndarray:
    """Embed a single text string. Returns 384-dim float32 normalized vector."""
    model = get_model()
    vec = model.encode(text[:8000], normalize_embeddings=True)
    return np.array(vec, dtype=np.float32)


def embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Embed multiple texts with batch inference. Returns list of 384-dim vectors."""
    model = get_model()
    vecs = model.encode([t[:8000] for t in texts], normalize_embeddings=True, batch_size=32)
    return [np.array(v, dtype=np.float32) for v in vecs]


def get_embed_dim() -> int:
    """Get embedding dimension."""
    return EMBED_DIM
