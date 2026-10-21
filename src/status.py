"""Readiness probe, including vector/lexical counts and configuration compatibility."""

from __future__ import annotations

import json

from filelock import FileLock

from src.config import Settings
from src.storage import check_manifest, chroma_client, get_collection, lexical_connection


def status(settings: Settings) -> dict:
    settings.prepare()
    with FileLock(str(settings.lock_path), timeout=2):
        manifest = check_manifest(settings)
        count = get_collection(chroma_client(settings)).count()
        connection = lexical_connection(settings, readonly=True)
        try:
            lexical = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            categories = dict(
                connection.execute(
                    "SELECT category, count(*) FROM chunks GROUP BY category ORDER BY category"
                )
            )
        finally:
            connection.close()
        expected = sum(file["chunks"] for file in manifest["files"].values())
        if not count or count != lexical or count != expected or integrity != "ok":
            raise RuntimeError("Index integrity check failed")
        return {
            "ready": True,
            "source_commit": manifest["source_commit"],
            "indexed_at": manifest["indexed_at"],
            "provider": settings.provider,
            "files": len(manifest["files"]),
            "source_policy": manifest["source_policy"],
            "excluded_files": manifest.get("excluded_files", {}),
            "chunks": count,
            "categories": categories,
            "database": str(settings.db_dir),
        }


def main() -> None:
    try:
        print(json.dumps(status(Settings.from_env()), indent=2, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
