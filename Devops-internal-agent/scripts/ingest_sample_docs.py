"""Preload non-secret sample_docs/ into the local RAG vector store for POC testing."""

from pathlib import Path

from app.config import settings
from app.rag.vector_store import LocalVectorStore


def main() -> None:
    settings.ensure_directories()
    store = LocalVectorStore(settings.vector_db_path)
    sample_dir = Path("sample_docs")
    if not sample_dir.exists():
        print("No sample_docs directory found.")
        return

    for doc_path in sorted(sample_dir.glob("*")):
        if doc_path.is_file():
            res = store.ingest_bytes(
                filename=doc_path.name,
                raw_bytes=doc_path.read_bytes(),
                uploaded_by="poc-bootstrap@company.internal",
                classification="public-poc",
                redact_before_index=settings.enable_secret_redaction,
            )
            print(f"Indexed {res['filename']} -> {res['chunk_count']} chunks (doc_id={res['doc_id']})")


if __name__ == "__main__":
    main()
