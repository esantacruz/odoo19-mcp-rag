from types import SimpleNamespace

import pytest

from src.config import Settings
from src.embeddings import LocalEmbedder, OpenAIEmbedder


def test_openai_requires_explicit_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIEmbedder(Settings(provider="openai"))


def test_openai_batches_and_restores_result_order():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[float(text)])
                for i, text in reversed(list(enumerate(kwargs["input"])))
            ]
        )

    embedder = OpenAIEmbedder.__new__(OpenAIEmbedder)
    embedder.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    embedder.model, embedder.batch_size = "text-embedding-3-small", 2
    assert embedder.encode(["1", "2", "3"]) == [[1.0], [2.0], [3.0]]
    assert len(calls) == 2


@pytest.mark.integration
def test_local_windows_cover_suffix_and_normalize():
    import numpy as np

    embedder = LocalEmbedder(Settings.from_env())
    prefix = "invoice accounting payment " * 180
    suffix = " warehouse inventory replenishment " * 180
    first, second, stock = np.array(embedder.encode([prefix, prefix + suffix, suffix]))
    assert first.shape == (384,)
    assert np.allclose(np.linalg.norm([first, second, stock], axis=1), 1, atol=1e-5)
    # With silent 256-token truncation, prefix and prefix+suffix would be identical.
    assert float(second @ stock) > float(first @ stock) + 0.05
