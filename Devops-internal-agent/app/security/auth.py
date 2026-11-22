"""API authentication and Retool caller identity extraction."""

import hmac
from dataclasses import dataclass
from typing import Optional
from fastapi import Header, HTTPException, status

from app.config import settings


@dataclass
class CallerIdentity:
    """Authenticated caller context passed from Retool or API clients."""

    user_email: str
    role: str
    source: str


def _verify_key(candidate: str) -> bool:
    return any(hmac.compare_digest(candidate, valid) for valid in settings.valid_api_keys)


async def require_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_email: Optional[str] = Header(default="local-poc-user@company.internal", alias="X-User-Email"),
    x_user_role: Optional[str] = Header(default="devops", alias="X-User-Role"),
    x_client_source: Optional[str] = Header(default="retool", alias="X-Client-Source"),
) -> CallerIdentity:
    """Validate API key / Bearer token when REQUIRE_AUTH is enabled."""
    if settings.require_auth:
        token = x_api_key
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()

        if not token or not _verify_key(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key. Provide X-API-Key or Bearer token.",
            )

    return CallerIdentity(
        user_email=(x_user_email or "anonymous@company.internal").strip(),
        role=(x_user_role or "viewer").strip().lower(),
        source=(x_client_source or "api").strip().lower(),
    )
