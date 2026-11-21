from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator, model_validator

# Load environment variables from backend/.env or project root .env
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")

try:
    from ai_analyzer import (
        AIAnalyzerBaseError,
        analyze_resources,
        is_openai_configured,
    )
    from aws_scanner import (
        AWSCLIBaseError,
        analyze_resource_group,
        check_cli_health,
        list_resource_groups,
    )
    from db import (
        DatabaseError,
        close_db,
        create_user,
        get_analyses_history,
        get_analysis_by_id,
        get_database_error,
        get_user_by_email,
        get_user_by_id,
        init_db,
        is_database_configured,
        is_database_connected,
        save_analysis,
    )
except ImportError:
    from backend.ai_analyzer import (
        AIAnalyzerBaseError,
        analyze_resources,
        is_openai_configured,
    )
    from backend.aws_scanner import (
        AWSCLIBaseError,
        analyze_resource_group,
        check_cli_health,
        list_resource_groups,
    )
    from backend.db import (
        DatabaseError,
        close_db,
        create_user,
        get_analyses_history,
        get_analysis_by_id,
        get_database_error,
        get_user_by_email,
        get_user_by_id,
        init_db,
        is_database_configured,
        is_database_connected,
        save_analysis,
    )


# ---------------------------------------------------------------------------
# Custom JWT & bcrypt Authentication Helpers
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
bearer_scheme = HTTPBearer(auto_error=False)


class AuthError(Exception):
    """Structured authentication error mapped cleanly to HTTP responses."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AUTH_ERROR",
        status_code: int = status.HTTP_401_UNAUTHORIZED,
        hint: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.hint:
            payload["error"]["hint"] = self.hint
        return payload


def _get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise AuthError(
            "JWT_SECRET environment variable is not configured.",
            code="JWT_SECRET_MISSING",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            hint="Set JWT_SECRET in backend/.env before using authentication endpoints.",
        )
    return secret


def _get_jwt_expire_hours() -> int:
    raw = os.getenv("JWT_EXPIRE_HOURS", "24").strip()
    try:
        hours = int(raw)
        return max(1, min(hours, 24 * 30))
    except ValueError:
        return 24


def is_jwt_configured() -> bool:
    return bool(os.getenv("JWT_SECRET", "").strip())


def _hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise AuthError(
            "Password must not exceed 72 bytes.",
            code="INVALID_PASSWORD",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _create_access_token(user_id: int, email: str) -> str:
    secret = _get_jwt_secret()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=_get_jwt_expire_hours())).timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    return token if isinstance(token, str) else token.decode("utf-8")


def _decode_access_token(token: str) -> dict[str, Any]:
    secret = _get_jwt_secret()
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(
            "Your session has expired. Please sign in again.",
            code="TOKEN_EXPIRED",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(
            "Invalid authentication token.",
            code="INVALID_TOKEN",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Validate Bearer JWT token and return the authenticated user record."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthError(
            "Authentication required. Provide an 'Authorization: Bearer <token>' header.",
            code="NOT_AUTHENTICATED",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    claims = _decode_access_token(credentials.credentials)
    sub = claims.get("sub")
    if not sub:
        raise AuthError(
            "Authentication token is missing user identity.",
            code="INVALID_TOKEN",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise AuthError(
            "Authentication token contains an invalid user ID.",
            code="INVALID_TOKEN",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    user = await get_user_by_id(user_id)
    if not user:
        raise AuthError(
            "Authenticated user no longer exists.",
            code="USER_NOT_FOUND",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return {
        "id": user["id"],
        "email": user["email"],
        "created_at": user["created_at"],
    }


# ---------------------------------------------------------------------------
# WebSocket Progress Manager
# ---------------------------------------------------------------------------


class ProgressManager:
    """Manages live WebSocket subscribers and replays recent progress events per analysis_id."""

    def __init__(self, max_buffered_analyses: int = 200) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._event_buffer: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._max_buffered_analyses = max_buffered_analyses

    async def connect(self, analysis_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[analysis_id].add(websocket)

        # Replay any events emitted before the client finished connecting
        for event in self._event_buffer.get(analysis_id, []):
            try:
                await websocket.send_json(event)
            except Exception:
                self.disconnect(analysis_id, websocket)
                return

    def disconnect(self, analysis_id: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(analysis_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(analysis_id, None)

    async def send_progress(
        self,
        analysis_id: str,
        *,
        step: int,
        total_steps: int,
        stage: str,
        message: str,
        progress: int,
        status: Literal["in_progress", "completed", "failed"] = "in_progress",
    ) -> dict[str, Any]:
        event = {
            "analysis_id": analysis_id,
            "step": step,
            "total_steps": total_steps,
            "stage": stage,
            "message": message,
            "progress": progress,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if analysis_id not in self._event_buffer:
            self._event_buffer[analysis_id] = []
            while len(self._event_buffer) > self._max_buffered_analyses:
                self._event_buffer.popitem(last=False)
        else:
            self._event_buffer.move_to_end(analysis_id)
        self._event_buffer[analysis_id].append(event)

        stale: list[WebSocket] = []
        for ws in list(self._connections.get(analysis_id, set())):
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.disconnect(analysis_id, ws)

        return event


progress_manager = ProgressManager()


# ---------------------------------------------------------------------------
# FastAPI Lifespan, App & CORS Configuration
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    try:
        yield
    finally:
        await close_db()


app = FastAPI(
    title="AI Cloud Cost Detective API",
    description=(
        "FastAPI backend that scans AWS Resource Groups and tagged resources via "
        "AWS CLI (subprocess), analyzes them with OpenAI (gpt-4o), persists history "
        "in Amazon RDS PostgreSQL, authenticates users with JWT, and streams live progress "
        "over WebSockets."
    ),
    version="1.3.0",
    lifespan=lifespan,
)

default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
extra_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[*default_origins, *extra_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Exception Handling
# ---------------------------------------------------------------------------


@app.exception_handler(AuthError)
async def auth_exception_handler(_request: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(AWSCLIBaseError)
async def aws_cli_exception_handler(_request: Request, exc: AWSCLIBaseError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(AIAnalyzerBaseError)
async def ai_analyzer_exception_handler(_request: Request, exc: AIAnalyzerBaseError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(DatabaseError)
async def database_exception_handler(_request: Request, exc: DatabaseError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# ---------------------------------------------------------------------------
# Pydantic Request & Response Models
# ---------------------------------------------------------------------------


class AuthCredentials(BaseModel):
    email: str = Field(..., examples=["finops@example.com"])
    password: str = Field(..., min_length=8, max_length=72, examples=["StrongPass123!"])

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Please enter a valid email address.")
        return cleaned


class LoginRequest(BaseModel):
    email: str = Field(..., examples=["finops@example.com"])
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Please enter a valid email address.")
        return cleaned


class UserPublic(BaseModel):
    id: int
    email: str
    created_at: Optional[str] = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class AnalyzeRequest(BaseModel):
    resource_group: Optional[str] = Field(
        default=None,
        description=(
            "AWS Resource Group name, or filter prefix: "
            "'tag:Key=Value', 'stack:<stack-name>', or 'service:<service>'."
        ),
        examples=["prod-web-stack", "tag:Environment=production", "stack:my-app", "service:ec2"],
    )
    analysis_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional client-generated analysis ID used to subscribe to "
            "ws://localhost:8000/ws/progress/{analysis_id} before calling POST /api/analyze."
        ),
    )
    tag_key: Optional[str] = Field(
        default=None,
        description="Optional explicit tag key filter (can be used instead of resource_group).",
    )
    tag_value: Optional[str] = Field(
        default=None,
        description="Optional explicit tag value used with tag_key.",
    )
    region: Optional[str] = Field(
        default=None,
        description="Optional AWS region override (defaults to AWS CLI configured region).",
        examples=["us-east-1"],
    )
    include_costs: bool = Field(
        default=True,
        description="Whether to include a 30-day AWS Cost Explorer breakdown.",
    )
    role_arn: Optional[str] = Field(
        default=None,
        description="Optional AWS IAM Role ARN to assume via `aws sts assume-role` before scanning.",
        examples=["arn:aws:iam::123456789012:role/CostDetectiveReadOnlyRole"],
    )
    external_id: Optional[str] = Field(
        default=None,
        description="Optional STS External ID if required by the IAM Role trust policy.",
    )

    @model_validator(mode="after")
    def validate_group_or_tag(self) -> AnalyzeRequest:
        has_group = bool(self.resource_group and self.resource_group.strip())
        has_tag = bool(self.tag_key and self.tag_key.strip())
        if not has_group and not has_tag:
            raise ValueError("Either 'resource_group' or 'tag_key' must be provided.")
        return self


class ResourceGroupItem(BaseModel):
    name: str
    arn: Optional[str] = None
    description: Optional[str] = None
    source: str
    region: str
    resource_count: Optional[int] = None


class ResourceGroupsResponse(BaseModel):
    account_id: str
    region: str
    count: int
    resource_groups: list[ResourceGroupItem]
    warnings: list[str] = Field(default_factory=list)


class ResourceItem(BaseModel):
    arn: str
    resource_type: str
    service: str
    name: str
    resource_id: str
    region: str
    location: str
    instance_type: Optional[str] = None
    sku: Optional[str] = None
    state: Optional[str] = None
    tags: dict[str, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class ServiceCostItem(BaseModel):
    service: str
    amount: float
    unit: str = "USD"


class CostSummary(BaseModel):
    period_start: str
    period_end: str
    total_cost: float
    currency: str = "USD"
    by_service: list[ServiceCostItem] = Field(default_factory=list)
    filter_applied: Optional[str] = None
    warning: Optional[str] = None


class CostIssue(BaseModel):
    title: str
    resource_name: str
    resource_id: str
    resource_arn: Optional[str] = None
    resource_type: str
    region: str
    issue_type: str = "misconfigured"
    category: str
    severity: Literal["high", "medium", "low"]
    description: str
    cost_impact: Optional[str] = None
    recommendation: str
    estimated_monthly_savings_usd: float = 0.0
    fix_commands: list[str] = Field(default_factory=list)


class EstimatedSavings(BaseModel):
    monthly_usd: float
    annual_usd: float
    currency: str = "USD"
    display: str
    notes: str


class FixCommand(BaseModel):
    command: str
    description: str
    resource_id: str
    severity: Literal["high", "medium", "low"]
    requires_downtime: bool = False


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AIAnalysisResult(BaseModel):
    summary: str
    issue_count: int
    severity_counts: dict[str, int]
    issues: list[CostIssue] = Field(default_factory=list)
    estimated_savings: EstimatedSavings
    actionable_fix_commands: list[FixCommand] = Field(default_factory=list)
    model: str
    token_usage: Optional[TokenUsage] = None


class AnalyzeResponse(BaseModel):
    analysis_id: str
    status: str = "completed"
    stored_in_db: bool = False
    resource_group: str
    filter_type: str
    account_id: str
    region: str
    resource_count: int
    resources: list[ResourceItem]
    summary_by_service: dict[str, int]
    cost_summary: Optional[CostSummary] = None
    analysis: AIAnalysisResult
    warnings: list[str] = Field(default_factory=list)
    scanned_at: str


class HistoryItem(BaseModel):
    id: str
    user_id: Optional[int] = None
    resource_group: str
    resources_scanned: int
    issues_found: int
    estimated_savings: str
    analysis_result: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: Optional[str] = None


class HistoryResponse(BaseModel):
    count: int
    analyses: list[HistoryItem]


# ---------------------------------------------------------------------------
# WebSocket Endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws/progress/{analysis_id}")
async def websocket_progress(
    websocket: WebSocket,
    analysis_id: str,
    token: Optional[str] = Query(default=None),
) -> None:
    """Stream live analysis progress updates for `analysis_id`."""
    if token:
        try:
            _decode_access_token(token)
        except AuthError:
            await websocket.close(code=1008, reason="Invalid or expired token")
            return

    await progress_manager.connect(analysis_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        progress_manager.disconnect(analysis_id, websocket)
    except Exception:
        progress_manager.disconnect(analysis_id, websocket)


# ---------------------------------------------------------------------------
# Public Routes (Root, Health, Auth)
# ---------------------------------------------------------------------------


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "AI Cloud Cost Detective Backend",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/health")
def health(
    region: Optional[str] = Query(default=None, description="Optional AWS region override"),
) -> dict[str, Any]:
    """Check AWS CLI, AWS credentials, OpenAI API key, JWT secret, and RDS PostgreSQL status."""
    cli_status = check_cli_health(region=region)
    openai_ready = is_openai_configured()
    db_configured = is_database_configured()
    db_connected = is_database_connected()
    jwt_ready = is_jwt_configured()
    all_ready = (
        cli_status["aws_cli_installed"]
        and cli_status["authenticated"]
        and openai_ready
        and db_connected
        and jwt_ready
    )
    return {
        "status": "ok" if all_ready else "degraded",
        "openai_configured": openai_ready,
        "jwt_configured": jwt_ready,
        "database_configured": db_configured,
        "database_connected": db_connected,
        "database_error": get_database_error(),
        **cli_status,
    }


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: AuthCredentials) -> dict[str, Any]:
    """Register a new user, hash password with bcrypt, persist in `users`, and return a JWT."""
    existing = await get_user_by_email(payload.email)
    if existing:
        raise AuthError(
            "An account with this email already exists.",
            code="EMAIL_ALREADY_REGISTERED",
            status_code=status.HTTP_409_CONFLICT,
            hint="Sign in using POST /api/auth/login or choose a different email.",
        )

    password_hash = await run_in_threadpool(_hash_password, payload.password)
    user = await create_user(payload.email, password_hash)
    access_token = _create_access_token(user["id"], user["email"])

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "created_at": user["created_at"],
        },
    }


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(payload: LoginRequest) -> dict[str, Any]:
    """Validate email + bcrypt password against `users` table and return a JWT."""
    user = await get_user_by_email(payload.email)
    if not user:
        raise AuthError(
            "Invalid email or password.",
            code="INVALID_CREDENTIALS",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    valid = await run_in_threadpool(_verify_password, payload.password, user["password_hash"])
    if not valid:
        raise AuthError(
            "Invalid email or password.",
            code="INVALID_CREDENTIALS",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    access_token = _create_access_token(user["id"], user["email"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "created_at": user["created_at"],
        },
    }


@app.get("/api/auth/me", response_model=UserPublic)
async def me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Return the currently authenticated user profile."""
    return current_user


# ---------------------------------------------------------------------------
# Protected API Routes (Bearer JWT Required)
# ---------------------------------------------------------------------------


@app.get("/api/resource-groups", response_model=ResourceGroupsResponse)
async def get_resource_groups(
    region: Optional[str] = Query(
        default=None,
        description="Optional AWS region override (e.g. us-east-1).",
    ),
    include_tag_groups: bool = Query(
        default=True,
        description=(
            "Include CloudFormation stacks (stack:<name>) and common tag groups "
            "(tag:Key=Value) discovered via AWS Resource Groups Tagging API."
        ),
    ),
    role_arn: Optional[str] = Query(
        default=None,
        description="Optional AWS IAM Role ARN to assume via `aws sts assume-role`.",
    ),
    external_id: Optional[str] = Query(
        default=None,
        description="Optional STS External ID for the IAM Role trust policy.",
    ),
    _current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return available AWS resource groups and tag/stack-based groupings."""
    return await run_in_threadpool(
        list_resource_groups,
        region=region,
        include_tag_groups=include_tag_groups,
        role_arn=role_arn,
        external_id=external_id,
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Execute the full cost detective pipeline while streaming WebSocket progress:
      1. Emit "Fetching resource groups..."
      2. Emit "Scanning resources in <rg>..." and scan via `aws_scanner`
      3. Emit "Analyzing costs with AI..." and analyze via `ai_analyzer`
      4. Emit "Storing results..." and persist to Amazon RDS PostgreSQL (`analyses`)
      5. Emit "Analysis complete" and return the final payload
    """
    analysis_id = (payload.analysis_id or "").strip() or str(uuid.uuid4())
    rg_label = (
        payload.resource_group.strip()
        if payload.resource_group and payload.resource_group.strip()
        else (
            f"tag:{payload.tag_key.strip()}={payload.tag_value.strip()}"
            if payload.tag_value and payload.tag_value.strip()
            else f"tag:{(payload.tag_key or '').strip()}"
        )
    )

    total_steps = 5
    current_step = 1

    try:
        # Stage 1: Fetching resource groups
        current_step = 1
        await progress_manager.send_progress(
            analysis_id,
            step=1,
            total_steps=total_steps,
            stage="fetching_resource_groups",
            message="Fetching resource groups...",
            progress=15,
        )
        await asyncio.sleep(0.05)

        # Stage 2: Scanning resources in the selected resource group
        current_step = 2
        await progress_manager.send_progress(
            analysis_id,
            step=2,
            total_steps=total_steps,
            stage="scanning_resources",
            message=f"Scanning resources in {rg_label}...",
            progress=35,
        )

        scan_result = await run_in_threadpool(
            analyze_resource_group,
            payload.resource_group,
            tag_key=payload.tag_key,
            tag_value=payload.tag_value,
            region=payload.region,
            include_costs=payload.include_costs,
            role_arn=payload.role_arn,
            external_id=payload.external_id,
        )

        # Stage 3: Analyzing costs with OpenAI
        current_step = 3
        await progress_manager.send_progress(
            analysis_id,
            step=3,
            total_steps=total_steps,
            stage="analyzing_costs",
            message="Analyzing costs with AI...",
            progress=65,
        )

        ai_result = await run_in_threadpool(
            analyze_resources,
            scan_result["resources"],
            resource_group=scan_result["resource_group"],
            region=scan_result["region"],
            cost_summary=scan_result.get("cost_summary"),
        )

        # Stage 4: Storing results in Amazon RDS PostgreSQL
        current_step = 4
        await progress_manager.send_progress(
            analysis_id,
            step=4,
            total_steps=total_steps,
            stage="storing_results",
            message="Storing results...",
            progress=85,
        )

        warnings = list(scan_result.get("warnings") or [])
        stored_in_db = False

        final_payload: dict[str, Any] = {
            **scan_result,
            "analysis_id": analysis_id,
            "status": "completed",
            "stored_in_db": stored_in_db,
            "analysis": ai_result,
            "warnings": warnings,
        }

        if is_database_configured():
            final_payload["stored_in_db"] = True
            await save_analysis(
                analysis_id=analysis_id,
                user_id=current_user["id"],
                resource_group=scan_result["resource_group"],
                resources_scanned=scan_result["resource_count"],
                issues_found=ai_result["issue_count"],
                estimated_savings=ai_result["estimated_savings"]["display"],
                analysis_result=final_payload,
                status="completed",
            )
            stored_in_db = True
        else:
            warnings.append(
                "DATABASE_URL is not configured; analysis results were not persisted to Amazon RDS PostgreSQL."
            )

        # Stage 5: Analysis complete
        current_step = 5
        await progress_manager.send_progress(
            analysis_id,
            step=5,
            total_steps=total_steps,
            stage="completed",
            message="Analysis complete",
            progress=100,
            status="completed",
        )

        return final_payload

    except Exception as exc:
        err_msg = getattr(exc, "message", str(exc))
        await progress_manager.send_progress(
            analysis_id,
            step=current_step,
            total_steps=total_steps,
            stage="failed",
            message=f"Analysis failed: {err_msg}",
            progress=100,
            status="failed",
        )
        raise


@app.get("/api/history", response_model=HistoryResponse)
async def get_history(
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of analyses to return."),
    offset: int = Query(default=0, ge=0, description="Pagination offset."),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return past analyses from Amazon RDS PostgreSQL for the authenticated user."""
    records = await get_analyses_history(
        user_id=current_user["id"],
        limit=limit,
        offset=offset,
    )
    return {
        "count": len(records),
        "analyses": records,
    }


@app.get("/api/history/{analysis_id}", response_model=HistoryItem)
async def get_history_item(
    analysis_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a single stored analysis by ID for the authenticated user."""
    record = await get_analysis_by_id(analysis_id, user_id=current_user["id"])
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"Analysis '{analysis_id}' was not found.",
        )
    return record


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
