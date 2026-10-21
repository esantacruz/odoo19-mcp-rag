"""Explicit embeddings: Chroma must never silently download a default embedding model."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from src.config import Settings


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class LocalEmbedder:
    def __init__(self, settings: Settings):
        settings.prepare()
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(settings.torch_threads)
        self.model = SentenceTransformer(
            settings.local_model,
            revision=settings.local_revision,
            cache_folder=str(settings.model_dir),
            device="cpu",
            local_files_only=settings.offline,
            trust_remote_code=False,
        )
        self.batch_size = settings.batch_size

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # Feed token IDs directly so decoding/re-encoding cannot truncate a window.
        tokenizer = self.model.tokenizer
        budget = self.model.max_seq_length - tokenizer.num_special_tokens_to_add(pair=False)
        if any(not text.strip() for text in texts):
            raise ValueError("Cannot embed empty text")
        encoded_windows = tokenizer(
            list(texts),
            padding=False,
            truncation=True,
            max_length=self.model.max_seq_length,
            return_overflowing_tokens=True,
            return_attention_mask=True,
        )
        owners = encoded_windows.pop("overflow_to_sample_mapping")
        windows = [
            {name: values[i] for name, values in encoded_windows.items()}
            for i in range(len(owners))
        ]
        special = self.model.max_seq_length - budget
        weights = [len(window["input_ids"]) - special for window in windows]
        vectors = np.zeros((len(texts), self.model.get_embedding_dimension()), dtype=np.float32)
        import torch

        self.model.eval()
        with torch.inference_mode():
            for start in range(0, len(windows), self.batch_size):
                features = tokenizer.pad(
                    windows[start : start + self.batch_size], padding=True, return_tensors="pt"
                )
                encoded = self.model(features)["sentence_embedding"].cpu().numpy()
                for offset, vector in enumerate(encoded):
                    i = start + offset
                    vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
                    vectors[owners[i]] += vector * weights[i]
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        return vectors.tolist()


class OpenAIEmbedder:
    def __init__(self, settings: Settings):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required for provider=openai")
        from openai import OpenAI

        self.client = OpenAI(timeout=45, max_retries=3)
        self.model = settings.openai_model
        self.batch_size = settings.batch_size

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            result = self.client.embeddings.create(
                model=self.model,
                input=list(texts[start : start + self.batch_size]),
                encoding_format="float",
            )
            vectors.extend(item.embedding for item in sorted(result.data, key=lambda x: x.index))
        return vectors


def create_embedder(settings: Settings) -> Embedder:
    return LocalEmbedder(settings) if settings.provider == "local" else OpenAIEmbedder(settings)
