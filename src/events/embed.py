"""Sentence-transformer embeddings -> event relevance scoring.

Local, free embeddings (no external LLM dependency). Each event is scored by cosine
similarity of its text against the vertical's "shock prototype" sentences, giving a
0..1 relevance score used later in attribution.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class EventEmbedder:
    """Wraps a sentence-transformer for relevance scoring against shock prototypes."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    def relevance(self, events: pd.DataFrame, prototypes: list[str]) -> pd.Series:
        """Return a 0..1 relevance score per event row (max cosine vs any prototype)."""
        if events.empty:
            return pd.Series(dtype="float64")
        ev_vecs = self.encode(events["text"].fillna("").tolist())
        proto_vecs = self.encode(prototypes)
        sims = ev_vecs @ proto_vecs.T                 # cosine (vectors normalized)
        scores = sims.max(axis=1).clip(0.0, 1.0)
        return pd.Series(scores, index=events.index, name="relevance")
