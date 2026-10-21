from __future__ import annotations

import hashlib
import re

import numpy as np
import pytest

from src.config import ROOT, Settings


class TestEmbedder:
    """Deterministic substitute for storage tests; never used for retrieval quality claims."""

    __test__ = False

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += len(texts)
        vectors = []
        for text in texts:
            vector = np.zeros(32, dtype=np.float32)
            for word in re.findall(r"\w+", text.lower()):
                vector[int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % 32] += 1
            vector /= max(np.linalg.norm(vector), 1e-12)
            vectors.append(vector.tolist())
        return vectors


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # Reuse only the tokenizer cache. Databases remain isolated in pytest's temp directory.
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(ROOT / "data/models/tiktoken"))
    config = Settings(data_dir=tmp_path / "data", chunk_size=128, chunk_overlap=24)
    config.prepare()
    return config


@pytest.fixture
def embedder():
    return TestEmbedder()


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "content"
    for name, content in {
        "developer/hooks.rst": "Hooks\n=====\n\nuseBus registers an event bus listener.\n\n"
        ".. code-block:: javascript\n\n   useBus(this.env.bus, 'event', callback);\n",
        "applications/finance/accounting/invoices.rst": "Customer invoices\n================\n\n"
        "Post a customer invoice and register its payment for reconciliation.\n",
        "applications/inventory_and_mrp/inventory/warehouses.md": "# Warehouses\n\n"
        "Inventory warehouse routes control product replenishment and stock movement.\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root
