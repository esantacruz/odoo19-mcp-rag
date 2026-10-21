"""Persistent vector and lexical stores, protected by one process-safe maintenance lock."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from src.config import COLLECTION, SOURCE_POLICY, Settings
from src.parsing import Chunk


class IndexNotReady(RuntimeError):
    pass


def read_manifest(settings: Settings) -> dict:
    if not settings.manifest_path.exists():
        raise IndexNotReady("Índice ausente. Ejecute: python -m src.ingest")
    try:
        return json.loads(settings.manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise IndexNotReady("Manifiesto inválido; restaure respaldo o use --force-reindex") from exc


def check_manifest(settings: Settings, ready: bool = True) -> dict:
    manifest = read_manifest(settings)
    if manifest.get("fingerprint") != settings.fingerprint:
        raise IndexNotReady(
            "Modelo/configuración incompatible. Ejecute ingesta con --force-reindex"
        )
    if ready and manifest.get("state") != "ready":
        raise IndexNotReady("Ingesta incompleta. Reanude: python -m src.ingest --skip-download")
    if ready and manifest.get("source_policy") != SOURCE_POLICY:
        raise IndexNotReady(
            "Política de fuentes actualizada; ejecute python -m src.ingest --offline"
        )
    return manifest


def write_manifest(settings: Settings, manifest: dict) -> None:
    fd, filename = tempfile.mkstemp(dir=settings.db_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, settings.manifest_path)
    finally:
        Path(filename).unlink(missing_ok=True)


def chroma_client(settings: Settings):
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(
        path=str(settings.db_dir), settings=ChromaSettings(anonymized_telemetry=False)
    )


def get_collection(client, create: bool = False):
    if create:
        return client.get_or_create_collection(
            COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
    return client.get_collection(COLLECTION, embedding_function=None)


def lexical_connection(settings: Settings, readonly: bool = False) -> sqlite3.Connection:
    path = settings.db_dir / "lexical.sqlite3"
    if readonly:
        # URI escaping is necessary for spaces, ? and # in workspace paths.
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    else:
        connection = sqlite3.connect(path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "id UNINDEXED, file_path UNINDEXED, category UNINDEXED, area UNINDEXED, "
            "headings, text, tokenize='unicode61 remove_diacritics 2')"
        )
    return connection


def replace_lexical(connection: sqlite3.Connection, file_path: str, chunks: list[Chunk]) -> None:
    with connection:
        connection.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    chunk.id,
                    file_path,
                    chunk.metadata["category"],
                    chunk.metadata["area"],
                    chunk.metadata["headings"],
                    chunk.text,
                )
                for chunk in chunks
            ],
        )


def lexical_search(connection, query: str, limit: int, category: str | None) -> list[str]:
    # User input never becomes FTS syntax or SQL. Each term is quoted and bounded.
    stopwords = {
        "the",
        "a",
        "an",
        "of",
        "for",
        "to",
        "and",
        "in",
        "is",
        "how",
        "odoo",
        "de",
        "la",
        "el",
        "en",
        "para",
        "como",
        "los",
        "las",
        "un",
        "una",
    }
    terms = list(
        dict.fromkeys(
            t.lower()
            for t in re.findall(r"\w+", query, re.UNICODE)
            if len(t) > 1 and t.lower() not in stopwords
        )
    )[:24]
    if not terms:
        return []
    match = " OR ".join('"' + term + '"' for term in terms)
    sql = "SELECT id FROM chunks WHERE chunks MATCH ?"
    params: list = [match]
    if category:
        sql += " AND (category = ? OR area = ?)"
        params.extend([category, category])
    sql += " ORDER BY bm25(chunks, 0, 0, 0, 0, 3, 1) LIMIT ?"
    params.append(limit)
    return [row[0] for row in connection.execute(sql, params)]
