"""Configuration shared by ingestion and serving; no network or model loading on import."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/odoo/documentation.git"
BRANCH = "19.0"
COLLECTION = "odoo19_docs"
PIPELINE_VERSION = "2"
SOURCE_POLICY = "exclude-explicit-outdated-and-legal-translations-v1"


def flag(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name}: use true/false")
    return value in {"true", "1"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "data"
    provider: str = "local"
    local_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    local_revision: str = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
    openai_model: str = "text-embedding-3-small"
    chunk_size: int = 800
    chunk_overlap: int = 150
    batch_size: int = 32
    torch_threads: int = 4
    offline: bool = True

    def __post_init__(self) -> None:
        if self.provider not in {"local", "openai"}:
            raise ValueError("ODOO_RAG_EMBEDDING_PROVIDER must be local or openai")
        if not 64 <= self.chunk_size <= 2000:
            raise ValueError("chunk_size must be between 64 and 2000")
        if not 0 <= self.chunk_overlap < self.chunk_size // 2:
            raise ValueError("chunk_overlap must be >= 0 and < chunk_size / 2")
        if self.batch_size < 1 or self.torch_threads < 1:
            raise ValueError("batch_size and torch_threads must be positive")

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(ROOT / ".env", override=False)
        data = Path(os.getenv("ODOO_RAG_DATA_DIR", "data")).expanduser()
        return cls(
            data_dir=(data if data.is_absolute() else ROOT / data).resolve(),
            provider=os.getenv("ODOO_RAG_EMBEDDING_PROVIDER", "local"),
            local_model=os.getenv("ODOO_RAG_LOCAL_MODEL", cls.local_model),
            local_revision=os.getenv("ODOO_RAG_LOCAL_REVISION", cls.local_revision),
            openai_model=os.getenv("ODOO_RAG_OPENAI_MODEL", cls.openai_model),
            chunk_size=int(os.getenv("ODOO_RAG_CHUNK_SIZE", "800")),
            chunk_overlap=int(os.getenv("ODOO_RAG_CHUNK_OVERLAP", "150")),
            batch_size=int(os.getenv("ODOO_RAG_BATCH_SIZE", "32")),
            torch_threads=int(os.getenv("ODOO_RAG_TORCH_THREADS", "4")),
            offline=flag("ODOO_RAG_OFFLINE", True),
        )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / "documentation"

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "chroma_db"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def manifest_path(self) -> Path:
        return self.db_dir / "manifest.json"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "index.lock"

    @property
    def fingerprint(self) -> str:
        """Reject indexes created with a different model, tokenizer or parsing strategy."""
        config = {
            "pipeline": PIPELINE_VERSION,
            "branch": BRANCH,
            "provider": self.provider,
            "model": self.local_model if self.provider == "local" else self.openai_model,
            "revision": self.local_revision if self.provider == "local" else "api",
            "pooling": "token-weighted-windows-v1",
            "tokenizer": "cl100k_base",
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
        }
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def prepare(self) -> None:
        for path in (self.data_dir, self.db_dir, self.model_dir):
            path.mkdir(parents=True, exist_ok=True)
        # All caches stay in the project. Disable network telemetry and progress output.
        os.environ.setdefault("HF_HOME", str(self.model_dir))
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(self.model_dir / "tiktoken"))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        if self.offline:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
