from dataclasses import replace

import pytest

from src.ingest import index_documents
from src.retriever import Retriever
from src.status import status
from src.storage import IndexNotReady, check_manifest, read_manifest


def test_incremental_update_delete_and_force(settings, corpus, embedder):
    first = index_documents(settings, corpus, "abc", embedder=embedder)
    assert first["changed"] == 3
    assert status(settings)["chunks"] == first["chunks"]
    calls = embedder.calls
    second = index_documents(settings, corpus, "abc", embedder=embedder)
    assert second["changed"] == 0 and second["skipped"] == 3
    assert embedder.calls == calls
    path = corpus / "developer/hooks.rst"
    path.write_text("Hooks\n=====\n\nNew OWL useAutofocus documentation.\n")
    (corpus / "applications/inventory_and_mrp/inventory/warehouses.md").unlink()
    third = index_documents(settings, corpus, "def", embedder=embedder)
    assert third["changed"] == 1 and third["deleted"] == 1
    assert "useBus" not in "\n".join(
        hit.text for hit in Retriever(settings, embedder).search("useBus")
    )
    forced = index_documents(settings, corpus, "def", force=True, embedder=embedder)
    assert forced["changed"] == 2
    assert status(settings)["files"] == 2


def test_filters_citations_validation_and_literal_query(settings, corpus, embedder):
    index_documents(settings, corpus, "abc123", embedder=embedder)
    retriever = Retriever(settings, embedder)
    hits = retriever.search("customer invoice payment", category="accounting")
    assert hits and all(h.metadata["category"] == "accounting" for h in hits)
    assert (
        retriever.search("stock warehouse", category="inventario")[0].metadata["category"]
        == "inventory"
    )
    assert (
        retriever.search("useBus", category="developer")[0]
        .metadata["source_url"]
        .startswith("https://github.com/odoo/documentation/blob/abc123/")
    )
    assert retriever.search('" OR NEAR(*) : --', category="not_a_category") == []
    for query, count in [("", 4), ("x" * 2001, 4), ("x", 0), ("x", 13), ("x", True)]:
        with pytest.raises(ValueError):
            retriever.search(query, count)
    context = retriever.topic_context("useBus OWL hooks")
    assert "useBus" in context and "https://www.odoo.com/documentation/19.0/" in context


def test_failed_ingest_is_not_served_and_can_resume(settings, corpus, embedder):
    class BrokenEmbedder:
        def encode(self, texts):
            raise RuntimeError("Simulated interruption")

    with pytest.raises(RuntimeError, match="Simulated interruption"):
        index_documents(settings, corpus, "abc", embedder=BrokenEmbedder())
    assert read_manifest(settings)["state"] == "indexing"
    with pytest.raises(IndexNotReady, match="incompleta"):
        Retriever(settings, embedder).search("invoice")
    recovered = index_documents(settings, corpus, "abc", embedder=embedder)
    assert recovered["changed"] == 3
    assert check_manifest(settings)["state"] == "ready"


def test_incompatible_model_rejected_and_empty_source_protected(
    settings, corpus, embedder, tmp_path
):
    index_documents(settings, corpus, "abc", embedder=embedder)
    other = replace(settings, provider="openai")
    with pytest.raises(IndexNotReady, match="incompatible"):
        Retriever(other, embedder).search("invoice")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="Empty source"):
        index_documents(settings, empty, "abc", force=True, embedder=embedder)
    assert check_manifest(settings)["state"] == "ready"


def test_reader_requires_restart_after_maintenance(settings, corpus, embedder):
    index_documents(settings, corpus, "abc", embedder=embedder)
    reader = Retriever(settings, embedder)
    assert reader.search("invoice")
    index_documents(settings, corpus, "def", embedder=embedder)
    with pytest.raises(IndexNotReady, match="reinicie"):
        reader.search("invoice")


def test_outdated_docs_and_translated_contracts_are_excluded(settings, corpus, embedder):
    obsolete = corpus / "developer/legacy.rst"
    obsolete.write_text(
        "Legacy tutorial\n===============\n\n.. danger::\n\n"
        "   This tutorial is outdated.\n\nUnsupported legacy API example.\n"
    )
    translated = corpus / "legal/terms/i18n/contract_es.rst"
    translated.parent.mkdir(parents=True)
    translated.write_text("Contrato\n========\n\nTexto de contrato traducido.\n")
    stats = index_documents(settings, corpus, "abc", embedder=embedder)
    assert stats["source_files"] == 5 and stats["excluded_files"] == 2
    assert stats["files"] == 3
    assert len(read_manifest(settings)["excluded_files"]) == 2
