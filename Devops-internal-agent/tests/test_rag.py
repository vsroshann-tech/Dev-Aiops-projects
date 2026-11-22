"""Tests for document chunking and local RAG vector store retrieval."""

from app.rag.vector_store import LocalVectorStore


def test_ingest_redact_search_and_delete(tmp_path) -> None:
    store = LocalVectorStore(str(tmp_path / "rag.db"))
    doc_bytes = (
        b"# EKS 502 Runbook\n"
        b"When prod ALB returns 502 Bad Gateway, check OOMKilled exit code 137.\n"
        b"Never use static key AKIAIOSFODNN7EXAMPLE in pipelines.\n\n"
        b"# Postgres Locks\n"
        b"Inspect pg_stat_activity for AccessExclusiveLock blocked queries.\n"
    )

    info = store.ingest_bytes(
        filename="eks-runbook.md",
        raw_bytes=doc_bytes,
        uploaded_by="devops@company.internal",
        classification="confidential",
        redact_before_index=True,
    )
    assert info["chunk_count"] >= 2
    assert info["redactions_applied"]["aws_access_key"] == 1

    hits = store.search("ALB returning 502 OOMKilled", top_k=2)
    assert len(hits) >= 1
    assert hits[0].filename == "eks-runbook.md"
    assert "AKIAIOSFODNN7EXAMPLE" not in hits[0].content
    assert "[REDACTED_AWS_ACCESS_KEY]" in hits[0].content

    docs = store.list_documents()
    assert len(docs) == 1
    assert store.delete_document(info["doc_id"]) is True
    assert store.list_documents() == []
