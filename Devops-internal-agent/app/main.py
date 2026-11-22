"""FastAPI server for the Security-Compliant Internal DevOps AI Agent."""

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.agent.orchestrator import DevOpsAgentOrchestrator
from app.config import settings
from app.mcp.client import MCPClientManager
from app.rag.vector_store import LocalVectorStore
from app.security.audit import AuditLogger
from app.security.auth import CallerIdentity, require_auth
from app.skills.loader import SkillRegistry

settings.ensure_directories()

vector_store = LocalVectorStore(settings.vector_db_path)
skill_registry = SkillRegistry(settings.skills_dir)
mcp_client = MCPClientManager()
audit_logger = AuditLogger(settings.audit_log_path)
orchestrator = DevOpsAgentOrchestrator(
    vector_store=vector_store,
    skill_registry=skill_registry,
    mcp_client=mcp_client,
    audit_logger=audit_logger,
)

app = FastAPI(
    title="Internal DevOps AI Agent (RAG + MCP + Skills)",
    version="0.1.0",
    description="Security-compliant DevOps troubleshooting agent designed for Retool integration.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    history: List[Dict[str, str]] = Field(default_factory=list)
    classification_filter: Optional[str] = None
    explicit_sql: Optional[str] = None
    db_engine: str = "postgres"


class Base64FileItem(BaseModel):
    filename: str = Field(..., min_length=1)
    content_base64: str = Field(..., min_length=1)


class Base64UploadRequest(BaseModel):
    filename: str = Field(..., min_length=1)
    content_base64: str = Field(..., min_length=1)
    classification: str = "internal"


class BatchBase64UploadRequest(BaseModel):
    files: List[Base64FileItem] = Field(..., min_length=1)
    classification: str = "internal"


class MCPCallRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    use_stdio: bool = False


@app.get("/", response_class=HTMLResponse)
async def serve_local_poc_ui() -> HTMLResponse:
    """Serve the built-in local POC Chat & Document Upload UI."""
    ui_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(ui_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Return service health, security guardrails, and loaded component counts."""
    return {
        "status": "ok",
        "poc_mode": settings.poc_mode,
        "llm_provider": settings.llm_provider,
        "security": {
            "require_auth": settings.require_auth,
            "secret_redaction": settings.enable_secret_redaction,
            "read_only_db_enforced": settings.read_only_db_enforced,
            "allow_ci_mutations": settings.allow_ci_mutations,
        },
        "documents_indexed": len(vector_store.list_documents()),
        "skills_loaded": len(skill_registry.skills),
        "mcp_tools_available": len(mcp_client.list_tools()),
    }


@app.post("/api/v1/chat")
async def chat_endpoint(
    payload: ChatRequest,
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Main Retool Chat endpoint combining RAG, Skills, MCP tools, and Audit logging."""
    return orchestrator.handle_chat(
        message=payload.message,
        caller=caller,
        history=payload.history,
        classification_filter=payload.classification_filter,
        explicit_sql=payload.explicit_sql,
        db_engine=payload.db_engine,
    )


@app.post("/api/v1/docs/upload")
async def upload_document(
    file: Optional[UploadFile] = File(default=None),
    files: Optional[List[UploadFile]] = File(default=None),
    classification: str = Form(default="internal"),
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Upload and index one or multiple internal documents (.pdf, .md, .txt, .yaml, .json)."""
    selected: List[UploadFile] = []
    if files:
        selected.extend(files)
    if file is not None:
        selected.append(file)
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one file via 'file' or 'files'.",
        )

    if len(selected) == 1 and not files:
        raw = await selected[0].read()
        return _ingest_and_audit(
            filename=selected[0].filename or "untitled.txt",
            raw_bytes=raw,
            classification=classification,
            caller=caller,
        )

    items = [(f.filename or "untitled.txt", await f.read()) for f in selected]
    return _ingest_batch(items=items, classification=classification, caller=caller)


@app.post("/api/v1/docs/upload-multiple")
async def upload_multiple_documents(
    files: List[UploadFile] = File(...),
    classification: str = Form(default="internal"),
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Upload and index multiple PDF/Markdown/text internal documents in a single request."""
    items = [(f.filename or "untitled.txt", await f.read()) for f in files]
    return _ingest_batch(items=items, classification=classification, caller=caller)


@app.post("/api/v1/docs/upload-base64")
async def upload_document_base64(
    payload: Base64UploadRequest,
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Retool-friendly base64 document upload endpoint (single File Dropzone)."""
    raw = _decode_base64_payload(payload.content_base64)
    return _ingest_and_audit(
        filename=payload.filename,
        raw_bytes=raw,
        classification=payload.classification,
        caller=caller,
    )


@app.post("/api/v1/docs/upload-batch-base64")
async def upload_documents_batch_base64(
    payload: BatchBase64UploadRequest,
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Retool-friendly multi-file base64 batch upload endpoint (multi-PDF Dropzone)."""
    items = [
        (item.filename, _decode_base64_payload(item.content_base64))
        for item in payload.files
    ]
    return _ingest_batch(items=items, classification=payload.classification, caller=caller)


def _decode_base64_payload(content_base64: str) -> bytes:
    raw_b64 = content_base64.split(",", 1)[-1]
    try:
        return base64.b64decode(raw_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64 payload.",
        ) from exc


def _ingest_batch(
    items: List[tuple[str, bytes]],
    classification: str,
    caller: CallerIdentity,
) -> Dict[str, Any]:
    uploaded: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    for filename, raw_bytes in items:
        try:
            uploaded.append(
                _ingest_and_audit(
                    filename=filename,
                    raw_bytes=raw_bytes,
                    classification=classification,
                    caller=caller,
                )
            )
        except HTTPException as exc:
            failed.append({"filename": filename, "error": str(exc.detail)})

    if not uploaded and failed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"All uploads failed: {failed}",
        )

    return {
        "total_received": len(items),
        "total_uploaded": len(uploaded),
        "total_failed": len(failed),
        "uploaded": uploaded,
        "failed": failed,
    }


def _ingest_and_audit(
    filename: str,
    raw_bytes: bytes,
    classification: str,
    caller: CallerIdentity,
) -> Dict[str, Any]:
    try:
        result = vector_store.ingest_bytes(
            filename=filename,
            raw_bytes=raw_bytes,
            uploaded_by=caller.user_email,
            classification=classification,
            redact_before_index=settings.enable_secret_redaction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_logger.log_event(
        event_type="doc_upload",
        user_email=caller.user_email,
        role=caller.role,
        source=caller.source,
        details=result,
    )
    return result


@app.get("/api/v1/docs")
async def list_documents(
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """List all indexed internal documents."""
    return {"documents": vector_store.list_documents()}


@app.delete("/api/v1/docs")
async def clear_all_documents(
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Delete all documents from the RAG vector store."""
    count = vector_store.clear_all_documents()
    audit_logger.log_event(
        event_type="docs_clear_all",
        user_email=caller.user_email,
        role=caller.role,
        source=caller.source,
        details={"deleted_count": count},
    )
    return {"cleared": True, "deleted_count": count}


@app.delete("/api/v1/docs/{doc_id}")
async def delete_document(
    doc_id: str,
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Remove an internal document from the RAG vector store."""
    deleted = vector_store.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found.")
    audit_logger.log_event(
        event_type="doc_delete",
        user_email=caller.user_email,
        role=caller.role,
        source=caller.source,
        details={"doc_id": doc_id},
    )
    return {"deleted": True, "doc_id": doc_id}


@app.get("/api/v1/skills")
async def list_skills(
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """List available DevOps Skills loaded from skills/*/SKILL.md."""
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "allowed_mcp_tools": s.allowed_mcp_tools,
            }
            for s in skill_registry.skills
        ]
    }


@app.post("/api/v1/skills/reload")
async def reload_skills(
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Hot-reload SKILL.md definitions from disk."""
    skill_registry.reload()
    return {"reloaded": True, "count": len(skill_registry.skills)}


@app.get("/api/v1/mcp/tools")
async def list_mcp_tools(
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """List all registered MCP servers and tools."""
    return {"tools": mcp_client.list_tools()}


@app.post("/api/v1/mcp/call")
async def call_mcp_tool(
    payload: MCPCallRequest,
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Execute an MCP tool directly (supports optional stdio JSON-RPC transport)."""
    output = mcp_client.call_tool(
        tool_name=payload.tool_name,
        arguments=payload.arguments,
        use_stdio=payload.use_stdio,
    )
    audit_logger.log_event(
        event_type="mcp_direct_call",
        user_email=caller.user_email,
        role=caller.role,
        source=caller.source,
        details={"tool_name": payload.tool_name, "arguments": payload.arguments},
    )
    return {"tool": payload.tool_name, "output": output}


@app.get("/api/v1/audit")
async def get_audit_logs(
    limit: int = 50,
    caller: CallerIdentity = Depends(require_auth),
) -> Dict[str, Any]:
    """Retrieve recent compliance audit log entries for Retool dashboards."""
    return {"events": audit_logger.read_recent(limit=limit)}
