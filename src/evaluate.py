"""Small explicit relevance smoke set and cold/warm latency measurements, not a global benchmark."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.config import Settings
from src.retriever import Retriever
from src.status import status

CASES = [
    ("OWL hooks useBus event listener", "developer", ["frontend/hooks.rst"]),
    (
        "computed fields dependencies api.depends",
        "developer",
        ["backend/orm.rst", "08_compute_onchange.rst"],
    ),
    ("record rules domain_force security", "developer", ["backend/security.rst"]),
    ("QWeb templates t-foreach t-if", "developer", ["frontend/qweb.rst"]),
    ("module manifest depends data installable", "developer", ["backend/module.rst"]),
    ("customer invoices payment terms installments", "accounting", ["payment_terms.rst"]),
    ("bank reconciliation matching transactions", "accounting", ["reconciliation"]),
    ("inventory reordering rules min max quantity", "inventory", ["reordering_rules.rst"]),
    ("Studio approval rules button", "studio", ["approval_rules.rst"]),
    ("create purchase request for quotation vendor", "purchase", ["rfq.rst"]),
]


def evaluate(settings: Settings) -> dict:
    info = status(settings)
    retriever = Retriever(settings)
    results = []
    for query, category, expected in CASES:
        start = time.perf_counter()
        hits = retriever.search(query, 4, category)
        elapsed = (time.perf_counter() - start) * 1000
        paths = [hit.metadata["file_path"] for hit in hits]
        results.append(
            {
                "query": query,
                "category": category,
                "hit_at_4": any(part in path for part in expected for path in paths),
                "expected_path_contains": expected,
                "retrieved_paths": paths,
                "milliseconds": round(elapsed, 2),
            }
        )
    warm = []
    for _ in range(3):
        for query, category, _ in CASES:
            start = time.perf_counter()
            retriever.search(query, 4, category)
            warm.append((time.perf_counter() - start) * 1000)
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "source_commit": info["source_commit"],
        "files": info["files"],
        "chunks": info["chunks"],
        "provider": settings.provider,
        "method": "10 hand-selected English cases; path-level hit@4, not answer accuracy",
        "hit_at_4": round(sum(r["hit_at_4"] for r in results) / len(results), 3),
        "first_search_ms_including_model_load": results[0]["milliseconds"],
        "uncached_query_p50_ms_excluding_first": round(
            float(np.median([r["milliseconds"] for r in results[1:]])), 2
        ),
        "cached_embedding_query_p50_ms": round(float(np.median(warm)), 2),
        "cached_embedding_query_p95_ms": round(float(np.percentile(warm, 95)), 2),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(Settings.from_env())
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    if report["hit_at_4"] < 0.8:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
