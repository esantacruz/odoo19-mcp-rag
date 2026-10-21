"""Download official docs, parse, chunk and resume indexing by per-file content hash."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock

from src.config import BRANCH, COLLECTION, REPOSITORY, SOURCE_POLICY, Settings
from src.embeddings import Embedder, create_embedder
from src.parsing import Chunker, document_chunks
from src.storage import (
    check_manifest,
    chroma_client,
    get_collection,
    lexical_connection,
    replace_lexical,
    write_manifest,
)

log = logging.getLogger(__name__)


def exclusion_reason(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root)
    if relative.parts[:3] == ("legal", "terms", "i18n"):
        return "translated legal contract outside the English functional/developer corpus"
    beginning = path.read_text(encoding="utf-8")[:4096]
    if re.search(
        r"this\s+(?:tutorial|documentation|document|guide|page)\s+is\s+(?:outdated|deprecated)",
        beginning,
        re.IGNORECASE,
    ):
        return "explicitly marked outdated/deprecated by Odoo"
    return None


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True, timeout=600
    )
    return result.stdout.strip()


def sync_repository(settings: Settings, update: bool = True) -> str:
    path = settings.raw_dir
    cloned = False
    if not path.exists():
        if not update:
            raise FileNotFoundError("No downloaded repository; omit --skip-download")
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                BRANCH,
                "--filter=blob:none",
                "--no-checkout",
                REPOSITORY,
                str(path),
            ],
            check=True,
            timeout=600,
            stdout=sys.stderr,
        )
        git(path, "sparse-checkout", "set", "--no-cone", "*.rst", "*.md", "/LICENSE", "/COPYRIGHT")
        git(path, "checkout", BRANCH)
        cloned = True
    origin = git(path, "remote", "get-url", "origin").removesuffix(".git")
    if origin != REPOSITORY.removesuffix(".git"):
        raise ValueError("Source repository is not the official odoo/documentation origin")
    if git(path, "branch", "--show-current") != BRANCH:
        raise ValueError("Source repository must be on branch 19.0")
    if git(path, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Source changes/untracked files detected; preserve them before indexing")
    if update and not cloned:
        # A depth-1 fetch cannot prove ancestry across several upstream commits.
        # Replace only this managed branch, after rejecting local commits and edits.
        if git(path, "rev-parse", "HEAD") != git(path, "rev-parse", "origin/" + BRANCH):
            raise ValueError(
                "Local commits detected; preserve them before updating the managed clone"
            )
        git(path, "fetch", "--depth", "1", "origin", BRANCH)
        git(path, "checkout", "-B", BRANCH, "FETCH_HEAD")
    if not (path / "content" / "developer").is_dir():
        raise ValueError("The official content/developer tree is missing")
    return git(path, "rev-parse", "HEAD")


def index_documents(
    settings: Settings,
    root: Path,
    commit: str,
    *,
    force: bool = False,
    embedder: Embedder | None = None,
) -> dict:
    """Caller holds the maintenance lock. Each completed file is a durable checkpoint."""
    settings.prepare()
    candidates = sorted(p for p in root.rglob("*") if p.suffix in {".rst", ".md"} and p.is_file())
    excluded = {
        "content/" + path.relative_to(root).as_posix(): reason
        for path in candidates
        if (reason := exclusion_reason(path, root))
    }
    paths = [
        path
        for path in candidates
        if "content/" + path.relative_to(root).as_posix() not in excluded
    ]
    if not paths:
        raise ValueError("Empty source tree; refusing to erase the existing index")
    manifest = None
    if settings.manifest_path.exists() and not force:
        manifest = check_manifest(settings, ready=False)
    # Preflight expensive dependencies before changing the existing index.
    chunker = Chunker(settings)
    embedder = embedder or create_embedder(settings)
    client = chroma_client(settings)
    if force:
        # Mark unavailable first. A failed rebuild cannot be served as a complete index.
        write_manifest(
            settings, {"state": "indexing", "fingerprint": settings.fingerprint, "files": {}}
        )
        if COLLECTION in [c.name for c in client.list_collections()]:
            client.delete_collection(COLLECTION)
    collection = get_collection(client, create=True)
    connection = lexical_connection(settings)
    if force:
        with connection:
            connection.execute("DELETE FROM chunks")
    manifest = manifest or {"fingerprint": settings.fingerprint, "files": {}}
    manifest.update(
        state="indexing", source_commit=commit, source_policy=SOURCE_POLICY, excluded_files=excluded
    )
    write_manifest(settings, manifest)
    started = time.perf_counter()
    changed, skipped, chunks_written = 0, 0, 0
    try:
        for position, path in enumerate(paths, 1):
            relative = "content/" + path.relative_to(root).as_posix()
            digest, chunks = document_chunks(path, root, chunker, commit)
            if manifest["files"].get(relative, {}).get("hash") == digest:
                skipped += 1
                continue
            old_ids = set(collection.get(where={"file_path": relative}, include=[])["ids"])
            for start in range(0, len(chunks), settings.batch_size):
                batch = chunks[start : start + settings.batch_size]
                # Headings are included in embeddings, while original content stays intact.
                vectors = embedder.encode([f"{c.metadata['headings']}\n\n{c.text}" for c in batch])
                collection.upsert(
                    ids=[c.id for c in batch],
                    embeddings=vectors,
                    documents=[c.text for c in batch],
                    metadatas=[c.metadata for c in batch],
                )
            stale = list(old_ids - {c.id for c in chunks})
            for start in range(0, len(stale), 1000):
                collection.delete(ids=stale[start : start + 1000])
            replace_lexical(connection, relative, chunks)
            manifest["files"][relative] = {"hash": digest, "chunks": len(chunks)}
            write_manifest(settings, manifest)
            changed += 1
            chunks_written += len(chunks)
            if position % 25 == 0 or position == len(paths):
                log.info(
                    "%d/%d files; changed=%d skipped=%d chunks=%d",
                    position,
                    len(paths),
                    changed,
                    skipped,
                    chunks_written,
                )
        live = {"content/" + path.relative_to(root).as_posix() for path in paths}
        indexed_paths = {m["file_path"] for m in collection.get(include=["metadatas"])["metadatas"]}
        lexical_paths = {
            row[0] for row in connection.execute("SELECT DISTINCT file_path FROM chunks")
        }
        deleted = (set(manifest["files"]) | indexed_paths | lexical_paths) - live
        for relative in deleted:
            collection.delete(where={"file_path": relative})
            replace_lexical(connection, relative, [])
            manifest["files"].pop(relative, None)
        total = collection.count()
        lexical_total = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
        expected = sum(file["chunks"] for file in manifest["files"].values())
        if not total or total != expected or lexical_total != expected:
            raise RuntimeError("Index counts do not agree; run --force-reindex")
        stats = {
            "source_files": len(candidates),
            "excluded_files": len(excluded),
            "files": len(paths),
            "changed": changed,
            "skipped": skipped,
            "deleted": len(deleted),
            "chunks": total,
            "chunks_written": chunks_written,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        manifest.update(state="ready", indexed_at=datetime.now(UTC).isoformat(), stats=stats)
        write_manifest(settings, manifest)
        return stats
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-reindex", action="store_true", help="Rebuild in maintenance mode")
    parser.add_argument("--skip-download", action="store_true", help="Use existing clean checkout")
    parser.add_argument("--offline", action="store_true", help="No model or repository downloads")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    settings = replace(Settings.from_env(), offline=args.offline)
    settings.prepare()
    try:
        with FileLock(str(settings.lock_path), timeout=1):
            commit = sync_repository(settings, update=not (args.skip_download or args.offline))
            stats = index_documents(
                settings, settings.raw_dir / "content", commit, force=args.force_reindex
            )
        print(json.dumps(stats, indent=2))
    except Exception as exc:
        # CLI tracebacks on stderr are useful; never swallow or report a partial index as ready.
        log.error("Ingest failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
