"""Database Diagnostics MCP Server for PostgreSQL and MySQL (strict read-only)."""

from typing import Any, Dict, List

from app.config import settings
from app.mcp.stdio_protocol import MCPToolDef, run_stdio_server
from app.security.guardrails import validate_readonly_sql


def db_get_active_connections(engine: str = "postgres") -> Dict[str, Any]:
    """Inspect active connections and lock waits in PostgreSQL or MySQL."""
    eng = engine.lower().strip()
    if eng not in ("postgres", "postgresql", "mysql"):
        return {"error": f"Unsupported DB engine '{engine}'. Use 'postgres' or 'mysql'."}

    if settings.poc_mode == "live":
        if eng.startswith("postgres") and settings.postgres_dsn:
            return db_run_readonly_sql(
                engine="postgres",
                sql=(
                    "SELECT pid, usename, state, wait_event_type, wait_event, "
                    "LEFT(query, 160) AS query_snippet "
                    "FROM pg_stat_activity WHERE state <> 'idle' ORDER BY query_start ASC LIMIT 20"
                ),
            )
        if eng == "mysql" and settings.mysql_password:
            return db_run_readonly_sql(
                engine="mysql",
                sql="SHOW FULL PROCESSLIST",
            )

    if eng.startswith("postgres"):
        return {
            "mode": "mock",
            "engine": "postgres",
            "active_connections": 48,
            "max_connections": 100,
            "blocked_sessions": [
                {
                    "pid": 24189,
                    "user": "reporting_ro",
                    "state": "active",
                    "duration_sec": 312,
                    "wait_event": "Lock:relation",
                    "query": "SELECT o.id, SUM(i.amount) FROM orders o JOIN order_items i ON ...",
                },
                {
                    "pid": 24215,
                    "user": "migrator",
                    "state": "waiting",
                    "blocked_by_pid": 24189,
                    "query": "ALTER TABLE orders ADD COLUMN ... (waiting for AccessExclusiveLock)",
                },
            ],
        }

    return {
        "mode": "mock",
        "engine": "mysql",
        "threads_connected": 190,
        "max_connections": 200,
        "warning": "Connection utilization at 95% (190/200)",
        "top_processes": [
            {
                "id": 8812,
                "user": "orders_app",
                "state": "Sending data",
                "time_sec": 47,
                "info": "SELECT * FROM orders WHERE customer_email LIKE '%@corp.com' ORDER BY created_at DESC",
            }
        ],
    }


def db_get_slow_queries(engine: str = "postgres") -> Dict[str, Any]:
    """Return top slow query fingerprints for PostgreSQL or MySQL."""
    eng = engine.lower().strip()
    if eng.startswith("postgres"):
        return {
            "mode": settings.poc_mode,
            "engine": "postgres",
            "slow_queries": [
                {
                    "calls": 1420,
                    "mean_time_ms": 1840.5,
                    "query": "SELECT * FROM audit_events WHERE payload_json::text LIKE $1",
                    "recommendation": "Add GIN index or filter on indexed created_at column first.",
                }
            ],
        }
    return {
        "mode": settings.poc_mode,
        "engine": "mysql",
        "slow_queries": [
            {
                "avg_latency_ms": 2310.0,
                "rows_examined": 4800000,
                "query": "SELECT * FROM orders WHERE customer_email LIKE ?",
                "recommendation": "Leading wildcard prevents B-Tree index use; add fulltext/prefix search or pagination.",
            }
        ],
    }


def db_run_readonly_sql(engine: str = "postgres", sql: str = "SELECT 1") -> Dict[str, Any]:
    """Execute a strictly validated read-only SQL query against Postgres or MySQL."""
    allowed, reason = validate_readonly_sql(sql)
    if not allowed:
        return {"blocked": True, "engine": engine, "reason": reason}

    eng = engine.lower().strip()
    if settings.poc_mode == "live":
        if eng.startswith("postgres"):
            import psycopg  # type: ignore

            with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 5000")
                    cur.execute(sql)
                    cols = [d.name for d in cur.description] if cur.description else []
                    rows = [dict(zip(cols, r)) for r in cur.fetchmany(50)]
            return {"mode": "live", "engine": "postgres", "row_count": len(rows), "rows": rows}

        if eng == "mysql":
            import pymysql  # type: ignore

            conn = pymysql.connect(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                database=settings.mysql_database,
                read_timeout=5,
                cursorclass=pymysql.cursors.DictCursor,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchmany(50)
                return {"mode": "live", "engine": "mysql", "row_count": len(rows), "rows": rows}
            finally:
                conn.close()

    return {
        "mode": "mock",
        "engine": eng,
        "validated_readonly": True,
        "sql": sql.strip(),
        "rows": [{"status": "ok", "note": "Read-only query validated and simulated in POC mock mode."}],
    }


TOOLS: List[MCPToolDef] = [
    MCPToolDef(
        name="db_get_active_connections",
        description="Inspect active connections and lock contention in PostgreSQL or MySQL.",
        input_schema={
            "type": "object",
            "properties": {"engine": {"type": "string", "enum": ["postgres", "mysql"]}},
        },
        handler=db_get_active_connections,
    ),
    MCPToolDef(
        name="db_get_slow_queries",
        description="Get top slow queries and index recommendations for PostgreSQL or MySQL.",
        input_schema={
            "type": "object",
            "properties": {"engine": {"type": "string", "enum": ["postgres", "mysql"]}},
        },
        handler=db_get_slow_queries,
    ),
    MCPToolDef(
        name="db_run_readonly_sql",
        description="Execute a strictly read-only SQL query (SELECT/SHOW/EXPLAIN) on Postgres or MySQL.",
        input_schema={
            "type": "object",
            "properties": {
                "engine": {"type": "string", "enum": ["postgres", "mysql"]},
                "sql": {"type": "string"},
            },
            "required": ["engine", "sql"],
        },
        handler=db_run_readonly_sql,
    ),
]


if __name__ == "__main__":
    run_stdio_server("db-diagnostics-mcp", TOOLS)
