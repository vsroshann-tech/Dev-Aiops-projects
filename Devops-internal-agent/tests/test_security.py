"""Tests for security guardrails, secret redaction, and compliance audit logging."""

from app.security.audit import AuditLogger
from app.security.guardrails import validate_ci_mutation, validate_readonly_sql
from app.security.redactor import redact_secrets


def test_secret_redactor_masks_credentials() -> None:
    sample = (
        "Deploy failed with AKIAIOSFODNN7EXAMPLE and ghp_abcdefghijklmnopqrstuvwxyz123456 "
        "and postgresql://admin:SuperSecret99@prod-db:5432/app "
        "and api_key=sk_live_9876543210abcdef"
    )
    cleaned, counts = redact_secrets(sample)
    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in cleaned
    assert "SuperSecret99" not in cleaned
    assert "sk_live_9876543210abcdef" not in cleaned
    assert counts["aws_access_key"] == 1
    assert counts["github_token"] == 1
    assert counts["db_connection_password"] == 1
    assert counts["key_value_secret"] == 1


def test_readonly_sql_allows_safe_queries() -> None:
    for sql in [
        "SELECT id, status FROM orders LIMIT 10;",
        "SHOW FULL PROCESSLIST",
        "EXPLAIN ANALYZE SELECT * FROM deployment_events",
        "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
    ]:
        ok, msg = validate_readonly_sql(sql)
        assert ok is True, f"Expected safe SQL to pass, got: {msg}"


def test_readonly_sql_blocks_mutations_and_stacked_queries() -> None:
    blocked_queries = [
        "DROP TABLE orders;",
        "DELETE FROM orders WHERE id = 1",
        "UPDATE orders SET status = 'ok'",
        "INSERT INTO orders (order_ref) VALUES ('x')",
        "SELECT 1; DROP TABLE orders;",
        "SELECT pg_terminate_backend(1234)",
        "/* comment */ ALTER TABLE orders ADD COLUMN x INT",
    ]
    for sql in blocked_queries:
        ok, _ = validate_readonly_sql(sql)
        assert ok is False, f"Expected dangerous SQL to be blocked: {sql}"


def test_ci_mutation_gate() -> None:
    ok, _ = validate_ci_mutation("rerun_workflow:123", allow_mutations=False, approved=True)
    assert ok is False
    ok, _ = validate_ci_mutation("rerun_workflow:123", allow_mutations=True, approved=False)
    assert ok is False
    ok, _ = validate_ci_mutation("rerun_workflow:123", allow_mutations=True, approved=True)
    assert ok is True


def test_audit_logger_roundtrip(tmp_path) -> None:
    logger = AuditLogger(str(tmp_path / "audit.jsonl"))
    logger.log_event(
        event_type="agent_chat",
        user_email="alice@company.internal",
        role="devops",
        source="retool",
        details={"prompt": "check alb 502"},
    )
    events = logger.read_recent(limit=10)
    assert len(events) == 1
    assert events[0]["user_email"] == "alice@company.internal"
    assert events[0]["details"]["prompt"] == "check alb 502"
