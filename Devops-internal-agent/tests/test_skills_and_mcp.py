"""Tests for Skills registry and MCP servers (including real stdio JSON-RPC)."""

from app.mcp.client import MCPClientManager
from app.skills.loader import SkillRegistry


def test_skill_registry_loads_and_matches() -> None:
    registry = SkillRegistry("skills")
    assert len(registry.skills) >= 4

    gh_matches = registry.match("GitHub Actions deploy pipeline failed on OIDC")
    assert any(s.name == "github-actions-triage" for s in gh_matches)

    db_matches = registry.match("Postgres lock contention and MySQL slow query")
    assert any(s.name == "database-health-check" for s in db_matches)

    alb_matches = registry.match("Production ALB is returning 502 errors on AWS EKS, check compute and CloudWatch")
    alb_names = [s.name for s in alb_matches]
    assert "aws-infra-troubleshooting" in alb_names
    assert "github-actions-triage" not in alb_names


def test_mcp_tools_inprocess_and_stdio_roundtrip() -> None:
    client = MCPClientManager()
    tools = client.list_tools()
    tool_names = {t["name"] for t in tools}
    assert {
        "github_list_workflow_runs",
        "github_get_failed_job_logs",
        "aws_list_compute_health",
        "aws_compliance_scan",
        "db_get_active_connections",
        "db_run_readonly_sql",
    }.issubset(tool_names)

    # Guardrail test via MCP tool
    blocked = client.call_tool(
        "db_run_readonly_sql",
        {"engine": "postgres", "sql": "DELETE FROM deployment_events;"},
    )
    assert blocked["blocked"] is True

    # Genuine MCP JSON-RPC 2.0 stdio subprocess test
    stdio_res = client.call_tool(
        "github_list_workflow_runs",
        {"branch": "main"},
        use_stdio=True,
    )
    assert stdio_res["mode"] == "mock"
    assert len(stdio_res["runs"]) >= 1
