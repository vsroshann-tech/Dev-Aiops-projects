"""Execution guardrails ensuring read-only diagnostics and compliance enforcement."""

import re
from typing import Tuple

_COMMENT_BLOCK_RE = re.compile(r"/\*[\s\S]*?\*/")
_COMMENT_LINE_RE = re.compile(r"--[^\r\n]*")
_HASH_LINE_RE = re.compile(r"#[^\r\n]*")

_ALLOWED_PREFIXES = ("SELECT", "SHOW", "EXPLAIN", "DESCRIBE", "DESC", "WITH")

_FORBIDDEN_KEYWORDS_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|RENAME|"
    r"GRANT|REVOKE|LOCK|UNLOCK|CALL|EXEC|EXECUTE|MERGE|COPY|"
    r"LOAD_FILE|OUTFILE|DUMPFILE|SHUTDOWN|KILL|RESET|FLUSH|"
    r"PG_TERMINATE_BACKEND|PG_CANCEL_BACKEND|SET\s+GLOBAL|SET\s+SESSION"
    r")\b",
    re.IGNORECASE,
)


def validate_readonly_sql(sql: str) -> Tuple[bool, str]:
    """Validate that a SQL query is strictly single-statement and read-only."""
    if not sql or not sql.strip():
        return False, "Empty SQL query is not allowed."

    stripped = _COMMENT_BLOCK_RE.sub(" ", sql)
    stripped = _COMMENT_LINE_RE.sub(" ", stripped)
    stripped = _HASH_LINE_RE.sub(" ", stripped).strip()

    # Allow a single trailing semicolon, disallow stacked multi-statements
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()

    if ";" in stripped:
        return False, "Blocked: multi-statement SQL queries are prohibited for compliance."

    if not stripped:
        return False, "Blocked: SQL contains only comments."

    first_word = stripped.split(None, 1)[0].upper()
    if first_word not in _ALLOWED_PREFIXES:
        return (
            False,
            f"Blocked: '{first_word}' statements are not allowed. Only read-only queries "
            f"({', '.join(_ALLOWED_PREFIXES)}) are permitted.",
        )

    forbidden_match = _FORBIDDEN_KEYWORDS_RE.search(stripped)
    if forbidden_match:
        return (
            False,
            f"Blocked: forbidden keyword '{forbidden_match.group(1).upper()}' detected in SQL query.",
        )

    return True, "OK"


def validate_ci_mutation(action: str, allow_mutations: bool, approved: bool = False) -> Tuple[bool, str]:
    """Gate mutating CI/CD actions behind policy configuration and explicit approval."""
    if not allow_mutations:
        return (
            False,
            f"Blocked by security policy: CI/CD action '{action}' is read-only disabled "
            "(ALLOW_CI_MUTATIONS=false).",
        )
    if not approved:
        return (
            False,
            f"Approval required: CI/CD action '{action}' requires explicit human approval.",
        )
    return True, "OK"
