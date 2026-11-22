"""Secret and credential redaction for prompts, documents, and tool outputs."""

import re
from typing import Dict, List, Tuple


_PATTERNS: List[Tuple[str, re.Pattern[str], str]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_ACCESS_KEY]",
    ),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED_JWT]",
    ),
    (
        "db_connection_password",
        re.compile(r"((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)://[^:\s/]+:)([^@\s]+)(@)"),
        r"\1[REDACTED_DB_PASSWORD]\3",
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\b(Authorization\s*:\s*Bearer\s+)([A-Za-z0-9._\-]{12,})"),
        r"\1[REDACTED_BEARER_TOKEN]",
    ),
    (
        "key_value_secret",
        re.compile(
            r"(?i)\b((?:aws_secret_access_key|secret_key|api_key|apikey|client_secret|db_password|password|passwd|token|private_key)\s*[=:]\s*['\"]?)([^\s'\",;}{]{6,})(['\"]?)"
        ),
        r"\1[REDACTED_SECRET]\3",
    ),
]


def redact_secrets(text: str) -> Tuple[str, Dict[str, int]]:
    """Redact sensitive credentials from text and return (cleaned_text, category_counts)."""
    if not text:
        return text, {}

    cleaned = text
    counts: Dict[str, int] = {}

    for category, pattern, replacement in _PATTERNS:
        cleaned, num_subs = pattern.subn(replacement, cleaned)
        if num_subs > 0:
            counts[category] = counts.get(category, 0) + num_subs

    return cleaned, counts
