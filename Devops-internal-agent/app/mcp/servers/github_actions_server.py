"""GitHub Actions MCP Server for CI/CD pipeline troubleshooting."""

from typing import Any, Dict, List

import httpx

from app.config import settings
from app.mcp.stdio_protocol import MCPToolDef, run_stdio_server
from app.security.guardrails import validate_ci_mutation
from app.security.redactor import redact_secrets


def github_list_workflow_runs(
    repo: str = "", branch: str = "", status: str = ""
) -> Dict[str, Any]:
    """List recent GitHub Actions workflow runs for troubleshooting."""
    target_repo = repo or f"{settings.github_default_org}/{settings.github_default_repo}"
    if settings.poc_mode == "live" and settings.github_token:
        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        }
        params: Dict[str, Any] = {"per_page": 10}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"https://api.github.com/repos/{target_repo}/actions/runs",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            runs = resp.json().get("workflow_runs", [])
            return {
                "mode": "live",
                "repo": target_repo,
                "runs": [
                    {
                        "id": str(r["id"]),
                        "name": r.get("name"),
                        "branch": r.get("head_branch"),
                        "status": r.get("status"),
                        "conclusion": r.get("conclusion"),
                        "created_at": r.get("created_at"),
                    }
                    for r in runs[:10]
                ],
            }

    mock_runs = [
        {
            "id": "9842101",
            "name": "deploy-eks-prod",
            "branch": "main",
            "status": "completed",
            "conclusion": "failure",
            "failed_step": "Configure AWS Credentials (OIDC) / Push to ECR",
            "created_at": "2026-11-22T12:40:00Z",
        },
        {
            "id": "9842098",
            "name": "db-migration-verify",
            "branch": "release/v2.14",
            "status": "completed",
            "conclusion": "failure",
            "failed_step": "Run Alembic Dry-Run against Staging Postgres",
            "created_at": "2026-11-22T11:55:00Z",
        },
        {
            "id": "9842050",
            "name": "security-trivy-scan",
            "branch": "main",
            "status": "completed",
            "conclusion": "success",
            "failed_step": None,
            "created_at": "2026-11-22T10:15:00Z",
        },
    ]
    if branch:
        mock_runs = [r for r in mock_runs if r["branch"] == branch]
    if status:
        mock_runs = [r for r in mock_runs if r["conclusion"] == status or r["status"] == status]

    return {"mode": "mock", "repo": target_repo, "runs": mock_runs}


def github_get_failed_job_logs(run_id: str, repo: str = "") -> Dict[str, Any]:
    """Fetch redacted error log snippet for a failed GitHub Actions workflow run."""
    target_repo = repo or f"{settings.github_default_org}/{settings.github_default_repo}"
    if settings.poc_mode == "live" and settings.github_token:
        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        }
        with httpx.Client(timeout=15.0) as client:
            jobs_resp = client.get(
                f"https://api.github.com/repos/{target_repo}/actions/runs/{run_id}/jobs",
                headers=headers,
            )
            jobs_resp.raise_for_status()
            jobs = jobs_resp.json().get("jobs", [])
            failed = [
                {
                    "job_id": j["id"],
                    "name": j["name"],
                    "conclusion": j.get("conclusion"),
                    "failed_steps": [
                        s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"
                    ],
                }
                for j in jobs
                if j.get("conclusion") == "failure"
            ]
            return {"mode": "live", "repo": target_repo, "run_id": run_id, "failed_jobs": failed}

    sample_logs = {
        "9842101": (
            "Step: Push Docker image to Amazon ECR\n"
            "Error: denied: User arn:aws:sts::123456789012:assumed-role/github-actions-deploy "
            "is not authorized to perform: ecr:InitiateLayerUpload on resource "
            "arn:aws:ecr:us-east-1:123456789012:repository/orders-api because no identity-based "
            "policy allows the ecr:InitiateLayerUpload action (Exit code 1)."
        ),
        "9842098": (
            "Step: Run Alembic Dry-Run\n"
            "psycopg.errors.LockNotAvailable: canceling statement due to lock timeout on table 'orders'\n"
            "Hint: Check active long-running SELECT transactions on Postgres before DDL migration."
        ),
    }
    raw_log = sample_logs.get(
        str(run_id),
        f"No failure log found for run_id={run_id}. Try run_id 9842101 or 9842098 in POC mock mode.",
    )
    cleaned_log, _ = redact_secrets(raw_log)
    return {"mode": "mock", "repo": target_repo, "run_id": str(run_id), "log_excerpt": cleaned_log}


def github_rerun_workflow(run_id: str, repo: str = "", approved: bool = False) -> Dict[str, Any]:
    """Attempt to rerun a workflow (blocked unless ALLOW_CI_MUTATIONS=true and approved=true)."""
    allowed, reason = validate_ci_mutation(
        action=f"rerun_workflow:{run_id}",
        allow_mutations=settings.allow_ci_mutations,
        approved=approved,
    )
    if not allowed:
        return {"blocked": True, "reason": reason, "run_id": str(run_id)}

    target_repo = repo or f"{settings.github_default_org}/{settings.github_default_repo}"
    if settings.poc_mode == "live" and settings.github_token:
        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://api.github.com/repos/{target_repo}/actions/runs/{run_id}/rerun",
                headers=headers,
            )
            resp.raise_for_status()
            return {"mode": "live", "status": "rerun_triggered", "run_id": str(run_id)}

    return {"mode": "mock", "status": "rerun_simulated", "repo": target_repo, "run_id": str(run_id)}


TOOLS: List[MCPToolDef] = [
    MCPToolDef(
        name="github_list_workflow_runs",
        description="List recent GitHub Actions workflow runs and their conclusions.",
        input_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo (optional)"},
                "branch": {"type": "string", "description": "Filter by branch"},
                "status": {"type": "string", "description": "Filter by status/conclusion"},
            },
        },
        handler=github_list_workflow_runs,
    ),
    MCPToolDef(
        name="github_get_failed_job_logs",
        description="Retrieve redacted logs for a failed GitHub Actions workflow run.",
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Workflow run ID"},
                "repo": {"type": "string", "description": "owner/repo (optional)"},
            },
            "required": ["run_id"],
        },
        handler=github_get_failed_job_logs,
    ),
    MCPToolDef(
        name="github_rerun_workflow",
        description="Rerun a failed GitHub Actions workflow (policy & approval gated).",
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "repo": {"type": "string"},
                "approved": {"type": "boolean"},
            },
            "required": ["run_id"],
        },
        handler=github_rerun_workflow,
    ),
]


if __name__ == "__main__":
    run_stdio_server("github-actions-mcp", TOOLS)
