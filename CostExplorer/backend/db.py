from __future__ import annotations

import json
import logging
import os
import ssl
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg
from dotenv import load_dotenv

# Load .env from backend/ or project root if present
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")

logger = logging.getLogger("cost_detective.db")

_pool: Optional[asyncpg.Pool] = None
_last_init_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Custom Database Exception
# ---------------------------------------------------------------------------


class DatabaseError(Exception):
    """Structured database exception mapped to HTTP responses in FastAPI."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "DATABASE_ERROR",
        status_code: int = 503,
        hint: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.hint = hint
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.hint:
            payload["error"]["hint"] = self.hint
        if self.details:
            payload["error"]["details"] = self.details
        return payload


# ---------------------------------------------------------------------------
# SSL / DSN Helpers for Amazon RDS PostgreSQL
# ---------------------------------------------------------------------------


def _get_raw_database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def is_database_configured() -> bool:
    """Return True if DATABASE_URL is configured with a non-placeholder value."""
    url = _get_raw_database_url()
    if not url:
        return False
    placeholder_markers = ("your-rds-endpoint", "your-password", "user:password@host")
    return not any(marker in url for marker in placeholder_markers)


def is_database_connected() -> bool:
    return _pool is not None


def get_database_error() -> Optional[str]:
    return _last_init_error


def _prepare_dsn_and_ssl(database_url: str) -> tuple[str, Any]:
    """
    Parse DATABASE_URL and build an SSLContext appropriate for Amazon RDS PostgreSQL.

    - Supports `sslmode` in the URL query string or `DB_SSL_MODE` env var (defaults to `require`).
    - Supports `sslrootcert` in the URL query string or `DB_SSL_ROOT_CERT` env var (e.g. RDS global-bundle.pem).
    - Strips `sslmode`/`sslrootcert` query params from the DSN because asyncpg receives SSL config via `ssl=`.
    """
    parsed = urlparse(database_url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    url_sslmode = (query_params.pop("sslmode", [None])[0] or "").strip().lower()
    url_rootcert = (query_params.pop("sslrootcert", [None])[0] or "").strip()

    ssl_mode = url_sslmode or os.getenv("DB_SSL_MODE", "require").strip().lower()
    root_cert = url_rootcert or os.getenv("DB_SSL_ROOT_CERT", "").strip()

    clean_query = urlencode(query_params, doseq=True)
    clean_dsn = urlunparse(parsed._replace(query=clean_query))

    if ssl_mode == "disable":
        return clean_dsn, False

    if root_cert:
        ctx = ssl.create_default_context(cafile=root_cert)
        ctx.check_hostname = ssl_mode == "verify-full"
        ctx.verify_mode = ssl.CERT_REQUIRED
        return clean_dsn, ctx

    if ssl_mode in ("verify-ca", "verify-full"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = ssl_mode == "verify-full"
        ctx.verify_mode = ssl.CERT_REQUIRED
        return clean_dsn, ctx

    # Default for Amazon RDS (`require` / `prefer`): enforce TLS encryption without
    # requiring the user to manually download the AWS RDS CA bundle first.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return clean_dsn, ctx


# ---------------------------------------------------------------------------
# Schema Initialization & Pool Lifecycle
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    resource_group TEXT NOT NULL,
    resources_scanned INTEGER NOT NULL DEFAULT 0,
    issues_found INTEGER NOT NULL DEFAULT 0,
    estimated_savings TEXT NOT NULL DEFAULT '$0.00/month ($0.00/year)',
    analysis_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyses_user_created
    ON analyses (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analyses_created
    ON analyses (created_at DESC);
"""


async def init_db() -> bool:
    """
    Initialize the asyncpg connection pool to Amazon RDS PostgreSQL (TLS enabled)
    and create the `users` and `analyses` tables if they do not exist.

    Returns True when connected and initialized, False when DATABASE_URL is unset
    or connection fails (error details are captured for `/api/health`).
    """
    global _pool, _last_init_error
    _last_init_error = None

    if not is_database_configured():
        _last_init_error = "DATABASE_URL is not configured."
        logger.warning(
            "DATABASE_URL is not configured; skipping Amazon RDS PostgreSQL initialization."
        )
        return False

    database_url = _get_raw_database_url()
    dsn, ssl_context = _prepare_dsn_and_ssl(database_url)

    try:
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            ssl=ssl_context,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        logger.info("Connected to Amazon RDS PostgreSQL and verified schema (users, analyses).")
        return True
    except Exception as exc:
        _last_init_error = str(exc)
        logger.error("Failed to initialize Amazon RDS PostgreSQL connection: %s", exc)
        if _pool is not None:
            await _pool.close()
            _pool = None
        return False


async def close_db() -> None:
    """Gracefully close the PostgreSQL connection pool on application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _require_pool() -> asyncpg.Pool:
    """Return the active pool, lazily attempting initialization if configured."""
    global _pool
    if _pool is None and is_database_configured():
        await init_db()

    if _pool is None:
        raise DatabaseError(
            "Amazon RDS PostgreSQL database is not connected.",
            code="DATABASE_UNAVAILABLE",
            status_code=503,
            hint=(
                "Set DATABASE_URL in backend/.env to your Amazon RDS PostgreSQL connection string "
                "and ensure the RDS security group allows inbound TCP port 5432."
            ),
            details=_last_init_error,
        )
    return _pool


# ---------------------------------------------------------------------------
# Row Serialization Helpers
# ---------------------------------------------------------------------------


def _serialize_user(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def _serialize_analysis(row: asyncpg.Record) -> dict[str, Any]:
    raw_result = row["analysis_result"]
    if isinstance(raw_result, str):
        try:
            parsed_result = json.loads(raw_result)
        except json.JSONDecodeError:
            parsed_result = {}
    else:
        parsed_result = raw_result if isinstance(raw_result, dict) else {}

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "resource_group": row["resource_group"],
        "resources_scanned": row["resources_scanned"],
        "issues_found": row["issues_found"],
        "estimated_savings": row["estimated_savings"],
        "analysis_result": parsed_result,
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


# ---------------------------------------------------------------------------
# User Queries (`users` table)
# ---------------------------------------------------------------------------


async def create_user(email: str, password_hash: str) -> dict[str, Any]:
    pool = await _require_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, password_hash)
                VALUES ($1, $2)
                RETURNING id, email, password_hash, created_at
                """,
                email.strip().lower(),
                password_hash,
            )
        return _serialize_user(row)
    except asyncpg.UniqueViolationError as exc:
        raise DatabaseError(
            f"A user with email '{email}' already exists.",
            code="USER_ALREADY_EXISTS",
            status_code=409,
        ) from exc
    except Exception as exc:
        raise DatabaseError(
            "Failed to create user record in PostgreSQL.",
            details=str(exc),
        ) from exc


async def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    pool = await _require_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, password_hash, created_at
                FROM users
                WHERE email = $1
                """,
                email.strip().lower(),
            )
        return _serialize_user(row) if row else None
    except Exception as exc:
        raise DatabaseError("Failed to query user by email.", details=str(exc)) from exc


async def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    pool = await _require_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, password_hash, created_at
                FROM users
                WHERE id = $1
                """,
                user_id,
            )
        return _serialize_user(row) if row else None
    except Exception as exc:
        raise DatabaseError("Failed to query user by ID.", details=str(exc)) from exc


# ---------------------------------------------------------------------------
# Analysis Queries (`analyses` table)
# ---------------------------------------------------------------------------


async def save_analysis(
    *,
    analysis_id: str,
    user_id: Optional[int],
    resource_group: str,
    resources_scanned: int,
    issues_found: int,
    estimated_savings: str,
    analysis_result: dict[str, Any],
    status: str = "completed",
) -> dict[str, Any]:
    """Insert or update a completed analysis record in the `analyses` table."""
    pool = await _require_pool()
    payload_json = json.dumps(analysis_result, default=str)

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO analyses (
                    id,
                    user_id,
                    resource_group,
                    resources_scanned,
                    issues_found,
                    estimated_savings,
                    analysis_result,
                    status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                ON CONFLICT (id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    resource_group = EXCLUDED.resource_group,
                    resources_scanned = EXCLUDED.resources_scanned,
                    issues_found = EXCLUDED.issues_found,
                    estimated_savings = EXCLUDED.estimated_savings,
                    analysis_result = EXCLUDED.analysis_result,
                    status = EXCLUDED.status
                RETURNING
                    id,
                    user_id,
                    resource_group,
                    resources_scanned,
                    issues_found,
                    estimated_savings,
                    analysis_result,
                    status,
                    created_at
                """,
                analysis_id,
                user_id,
                resource_group,
                resources_scanned,
                issues_found,
                estimated_savings,
                payload_json,
                status,
            )
        return _serialize_analysis(row)
    except Exception as exc:
        raise DatabaseError(
            "Failed to store analysis results in Amazon RDS PostgreSQL.",
            details=str(exc),
        ) from exc


async def get_analyses_history(
    *,
    user_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return past analyses ordered newest-first, scoped by `user_id` when authenticated."""
    pool = await _require_pool()
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    try:
        async with pool.acquire() as conn:
            if user_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT
                        id,
                        user_id,
                        resource_group,
                        resources_scanned,
                        issues_found,
                        estimated_savings,
                        analysis_result,
                        status,
                        created_at
                    FROM analyses
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    safe_limit,
                    safe_offset,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        id,
                        user_id,
                        resource_group,
                        resources_scanned,
                        issues_found,
                        estimated_savings,
                        analysis_result,
                        status,
                        created_at
                    FROM analyses
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    safe_limit,
                    safe_offset,
                )
        return [_serialize_analysis(row) for row in rows]
    except Exception as exc:
        raise DatabaseError(
            "Failed to retrieve analysis history from Amazon RDS PostgreSQL.",
            details=str(exc),
        ) from exc


async def get_analysis_by_id(
    analysis_id: str,
    *,
    user_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Fetch a single stored analysis by ID (optionally scoped to `user_id`)."""
    pool = await _require_pool()
    try:
        async with pool.acquire() as conn:
            if user_id is not None:
                row = await conn.fetchrow(
                    """
                    SELECT
                        id,
                        user_id,
                        resource_group,
                        resources_scanned,
                        issues_found,
                        estimated_savings,
                        analysis_result,
                        status,
                        created_at
                    FROM analyses
                    WHERE id = $1 AND user_id = $2
                    """,
                    analysis_id,
                    user_id,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT
                        id,
                        user_id,
                        resource_group,
                        resources_scanned,
                        issues_found,
                        estimated_savings,
                        analysis_result,
                        status,
                        created_at
                    FROM analyses
                    WHERE id = $1
                    """,
                    analysis_id,
                )
        return _serialize_analysis(row) if row else None
    except Exception as exc:
        raise DatabaseError(
            "Failed to retrieve analysis record from Amazon RDS PostgreSQL.",
            details=str(exc),
        ) from exc
