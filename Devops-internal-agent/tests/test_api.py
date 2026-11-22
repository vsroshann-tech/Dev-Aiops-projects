"""End-to-end API tests for FastAPI endpoints used by Retool and Ubuntu POC UI."""

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as app_main
from app.rag.vector_store import LocalVectorStore
from app.security.audit import AuditLogger


def _setup_isolated_client(tmp_path: Path) -> TestClient:
    app_main.vector_store = LocalVectorStore(str(tmp_path / "rag.db"))
    app_main.audit_logger = AuditLogger(str(tmp_path / "audit.jsonl"))
    app_main.orchestrator.vector_store = app_main.vector_store
    app_main.orchestrator.audit_logger = app_main.audit_logger
    return TestClient(app_main.app)


def test_auth_required_and_health_open(tmp_path) -> None:
    client = _setup_isolated_client(tmp_path)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    unauth = client.post("/api/v1/chat", json={"message": "hello"})
    assert unauth.status_code == 401


def test_retool_base64_upload_chat_and_audit_flow(tmp_path) -> None:
    client = _setup_isolated_client(tmp_path)
    headers = {
        "X-API-Key": "dev-retool-poc-key-change-me",
        "X-User-Email": "sre@company.internal",
        "X-Client-Source": "retool",
    }

    doc_content = (
        "# Production MySQL & AWS Runbook\n"
        "If MySQL connections exceed 90% on prod-mysql-orders-01, check HikariCP pool limits.\n"
    ).encode("utf-8")
    b64 = base64.b64encode(doc_content).decode("ascii")

    up_resp = client.post(
        "/api/v1/docs/upload-base64",
        headers=headers,
        json={"filename": "mysql-runbook.md", "content_base64": b64, "classification": "internal"},
    )
    assert up_resp.status_code == 200
    assert up_resp.json()["filename"] == "mysql-runbook.md"

    chat_resp = client.post(
        "/api/v1/chat",
        headers=headers,
        json={"message": "Check AWS RDS and MySQL connections using token ghp_1234567890abcdefghijklmnop"},
    )
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert "ghp_1234567890abcdefghijklmnop" not in body["reply"]
    assert body["security"]["redactions_applied"]["github_token"] == 1
    assert len(body["citations"]) >= 1
    assert any(m["tool"] == "db_get_active_connections" for m in body["mcp_results"])

    audit_resp = client.get("/api/v1/audit?limit=10", headers=headers)
    assert audit_resp.status_code == 200
    events = audit_resp.json()["events"]
    assert len(events) == 2
    assert events[0]["event_type"] == "agent_chat"
    assert events[0]["user_email"] == "sre@company.internal"


def test_multiple_pdf_upload_and_rag_query(tmp_path) -> None:
    client = _setup_isolated_client(tmp_path)
    headers = {
        "X-API-Key": "dev-retool-poc-key-change-me",
        "X-User-Email": "devops@company.internal",
        "X-Client-Source": "local-poc-ui",
    }

    pdf_paths = sorted(Path("sample_docs/pdfs").glob("*.pdf"))
    assert len(pdf_paths) == 4

    files_payload = [
        ("files", (p.name, p.read_bytes(), "application/pdf"))
        for p in pdf_paths
    ]
    resp = client.post(
        "/api/v1/docs/upload-multiple",
        headers=headers,
        files=files_payload,
        data={"classification": "public-poc"},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["total_received"] == 4
    assert result["total_uploaded"] == 4
    assert result["total_failed"] == 0

    chat_resp = client.post(
        "/api/v1/chat",
        headers=headers,
        json={"message": "Where does billing-worker run on AWS and which GitHub Actions workflow deploys it?"},
    )
    assert chat_resp.status_code == 200
    citations = chat_resp.json()["citations"]
    cited_files = {c["filename"] for c in citations}
    assert cited_files & {
        "01-github-actions-live-pipelines.pdf",
        "03-live-projects-repo-to-server-map.pdf",
    }


def test_retool_batch_base64_pdf_upload(tmp_path) -> None:
    client = _setup_isolated_client(tmp_path)
    headers = {
        "X-API-Key": "dev-retool-poc-key-change-me",
        "X-User-Email": "sre@company.internal",
        "X-Client-Source": "retool",
    }

    pdf_paths = sorted(Path("sample_docs/pdfs").glob("*.pdf"))[:2]
    payload = {
        "classification": "internal",
        "files": [
            {
                "filename": p.name,
                "content_base64": base64.b64encode(p.read_bytes()).decode("ascii"),
            }
            for p in pdf_paths
        ],
    }
    resp = client.post("/api/v1/docs/upload-batch-base64", headers=headers, json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_uploaded"] == 2
    assert len(body["uploaded"]) == 2


def test_question_precision_and_clear_docs(tmp_path) -> None:
    client = _setup_isolated_client(tmp_path)
    headers = {
        "X-API-Key": "dev-retool-poc-key-change-me",
        "X-User-Email": "sre@company.internal",
        "X-Client-Source": "local-poc-ui",
    }

    # 1. Capability question should not dump unrelated RAG chunks or MCP telemetry
    cap_resp = client.post("/api/v1/chat", headers=headers, json={"message": "what you can do for me ?"})
    assert cap_resp.status_code == 200
    cap = cap_resp.json()
    assert cap["provider"] == "capability_router"
    assert cap["mcp_results"] == []
    assert "Capabilities" in cap["reply"]

    # 2. ALB 502 question should only invoke AWS MCP tools, not GitHub Actions
    alb_resp = client.post(
        "/api/v1/chat",
        headers=headers,
        json={"message": "Production ALB is returning 502 errors on AWS EKS, check compute and CloudWatch"},
    )
    assert alb_resp.status_code == 200
    alb_tools = {m["tool"] for m in alb_resp.json()["mcp_results"]}
    assert "aws_list_compute_health" in alb_tools
    assert "github_list_workflow_runs" not in alb_tools

    # 3. Pure inventory question ("how many servers are there") should answer from docs without firing error logs
    pdf_paths = sorted(Path("sample_docs/pdfs").glob("*.pdf"))
    client.post(
        "/api/v1/docs/upload-multiple",
        headers=headers,
        files=[("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdf_paths],
    )
    srv_resp = client.post("/api/v1/chat", headers=headers, json={"message": "how many servers are there"})
    assert srv_resp.status_code == 200
    srv_body = srv_resp.json()
    assert srv_body["mcp_results"] == []
    assert "02-aws-servers-and-live-infra.pdf" in {c["filename"] for c in srv_body["citations"]}

    # 4. Clear all docs endpoint works
    clr = client.delete("/api/v1/docs", headers=headers)
    assert clr.status_code == 200
    assert clr.json()["deleted_count"] == 4


