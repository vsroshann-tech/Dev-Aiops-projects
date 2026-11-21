from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

# Common tag keys surfaced as discoverable tag groups when scanning via Tagging API
COMMON_GROUP_TAG_KEYS = (
    "ResourceGroup",
    "resource-group",
    "Project",
    "Application",
    "Environment",
    "Service",
    "App",
    "Stack",
)

CFN_STACK_TAG_KEY = "aws:cloudformation:stack-name"

# Services whose resources are global (ARN region segment is empty)
GLOBAL_SERVICES = {"s3", "iam", "cloudfront", "route53", "waf", "organizations", "budgets"}

# Canonical CloudFormation-style type names when fetched via Tagging API
SERVICE_SUBTYPE_TO_CFN: dict[tuple[str, str], str] = {
    ("ec2", "instance"): "AWS::EC2::Instance",
    ("ec2", "volume"): "AWS::EC2::Volume",
    ("ec2", "natgateway"): "AWS::EC2::NatGateway",
    ("ec2", "vpc"): "AWS::EC2::VPC",
    ("ec2", "subnet"): "AWS::EC2::Subnet",
    ("ec2", "security-group"): "AWS::EC2::SecurityGroup",
    ("ec2", "elastic-ip"): "AWS::EC2::EIP",
    ("ec2", "snapshot"): "AWS::EC2::Snapshot",
    ("rds", "db"): "AWS::RDS::DBInstance",
    ("rds", "cluster"): "AWS::RDS::DBCluster",
    ("s3", "bucket"): "AWS::S3::Bucket",
    ("lambda", "function"): "AWS::Lambda::Function",
    ("dynamodb", "table"): "AWS::DynamoDB::Table",
    ("elasticloadbalancing", "loadbalancer"): "AWS::ElasticLoadBalancingV2::LoadBalancer",
    ("elasticache", "cluster"): "AWS::ElastiCache::CacheCluster",
    ("ecs", "cluster"): "AWS::ECS::Cluster",
    ("ecs", "service"): "AWS::ECS::Service",
    ("eks", "cluster"): "AWS::EKS::Cluster",
    ("sqs", "queue"): "AWS::SQS::Queue",
    ("sns", "topic"): "AWS::SNS::Topic",
    ("logs", "log-group"): "AWS::Logs::LogGroup",
    ("cloudformation", "stack"): "AWS::CloudFormation::Stack",
}

DEFAULT_CLI_TIMEOUT_SECONDS = 30
MAX_PER_RESOURCE_DESCRIBE_CALLS = 25
ROLE_ARN_REGEX = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/.+$")

_aws_env_override: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "_aws_env_override", default=None
)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class AWSCLIBaseError(Exception):
    """Base exception for AWS CLI / scanner failures with HTTP-friendly metadata."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AWS_CLI_ERROR",
        status_code: int = 502,
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


class AWSCLINotInstalledError(AWSCLIBaseError):
    def __init__(self, details: Optional[str] = None) -> None:
        super().__init__(
            "AWS CLI ('aws') is not installed or not available on the system PATH.",
            code="AWS_CLI_NOT_INSTALLED",
            status_code=503,
            hint=(
                "Install AWS CLI v2 from "
                "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html "
                "and restart the backend server."
            ),
            details=details,
        )


class AWSCredentialsError(AWSCLIBaseError):
    def __init__(self, message: str, details: Optional[str] = None) -> None:
        super().__init__(
            message,
            code="AWS_AUTH_ERROR",
            status_code=401,
            hint=(
                "Configure or refresh your AWS credentials via 'aws configure', "
                "'aws sso login', or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables."
            ),
            details=details,
        )


class AWSIAMAccessDeniedError(AWSCLIBaseError):
    def __init__(self, message: str, details: Optional[str] = None) -> None:
        super().__init__(
            message,
            code="AWS_PERMISSION_DENIED",
            status_code=403,
            hint=(
                "Verify your IAM user/role has read permissions such as "
                "resource-groups:ListGroups, resource-groups:ListGroupResources, "
                "tag:GetResources, ec2:DescribeInstances, rds:DescribeDBInstances, "
                "and ce:GetCostAndUsage."
            ),
            details=details,
        )


class AWSResourceGroupNotFoundError(AWSCLIBaseError):
    def __init__(self, group_name: str, details: Optional[str] = None) -> None:
        super().__init__(
            f"AWS resource group '{group_name}' was not found.",
            code="RESOURCE_GROUP_NOT_FOUND",
            status_code=404,
            hint=(
                "Check available groups via GET /api/resource-groups, verify the spelling, "
                "or confirm you are querying the right AWS region."
            ),
            details=details,
        )


class AWSRegionConfigError(AWSCLIBaseError):
    def __init__(self, details: Optional[str] = None) -> None:
        super().__init__(
            "No AWS region is configured for the current profile.",
            code="AWS_REGION_REQUIRED",
            status_code=400,
            hint=(
                "Set a default region with 'aws configure set region us-east-1', "
                "export AWS_REGION, or pass 'region' in the API request."
            ),
            details=details,
        )


class InvalidFilterError(AWSCLIBaseError):
    def __init__(self, message: str, hint: Optional[str] = None) -> None:
        super().__init__(
            message,
            code="INVALID_FILTER",
            status_code=400,
            hint=hint
            or "Provide a valid resource group name, or use 'tag:Key=Value', 'stack:<name>', or 'service:<service>'.",
        )


class AWSCLIExecutionError(AWSCLIBaseError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "AWS_CLI_ERROR",
        status_code: int = 502,
        hint: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            status_code=status_code,
            hint=hint,
            details=details,
        )


# ---------------------------------------------------------------------------
# Subprocess Execution & Error Classification
# ---------------------------------------------------------------------------


def _find_aws_executable() -> str:
    """Resolve the AWS CLI executable path without using a shell."""
    aws_path = shutil.which("aws")
    if not aws_path:
        raise AWSCLINotInstalledError()
    return aws_path


def _extract_group_name_from_args(args: list[str]) -> Optional[str]:
    for idx, token in enumerate(args):
        if token in ("--group-name", "--group") and idx + 1 < len(args):
            return args[idx + 1]
    return None


def _classify_aws_error(stderr: str, args: list[str], returncode: int) -> AWSCLIBaseError:
    """Map raw AWS CLI stderr output into a typed exception."""
    err_text = (stderr or "").strip()
    lower = err_text.lower()

    # 1. Missing / expired / invalid credentials or SSO session
    credential_markers = (
        "unable to locate credentials",
        "expiredtoken",
        "expiredtokenexception",
        "invalidclienttokenid",
        "signaturedoesnotmatch",
        "unrecognizedclientexception",
        "tokenrefreshrequired",
        "error loading sso token",
        "sso session associated with this profile has expired",
        "the sso session",
        "nocredentialproviders",
        "authfailure",
        "the security token included in the request is invalid",
        "the security token included in the request is expired",
        "could not be found",  # e.g., The config profile (foo) could not be found
        "partial credentials found",
    )
    if any(marker in lower for marker in credential_markers):
        return AWSCredentialsError(
            "AWS credentials are missing, invalid, or expired.",
            details=err_text,
        )

    # 2. Missing region configuration
    if "you must specify a region" in lower or "could not connect to the endpoint url" in lower:
        if "you must specify a region" in lower:
            return AWSRegionConfigError(details=err_text)

    # 3. Insufficient IAM permissions
    permission_markers = (
        "accessdenied",
        "accessdeniedexception",
        "unauthorizedoperation",
        "unauthorizedexception",
        "is not authorized to perform",
        "not authorized to access",
    )
    if any(marker in lower for marker in permission_markers):
        return AWSIAMAccessDeniedError(
            "Insufficient IAM permissions to execute the requested AWS CLI operation.",
            details=err_text,
        )

    # 4. Non-existent resource group
    not_found_markers = (
        "notfoundexception",
        "resourcenotfoundexception",
        "group not found",
        "does not exist",
    )
    if any(marker in lower for marker in not_found_markers):
        group_name = _extract_group_name_from_args(args)
        if group_name:
            return AWSResourceGroupNotFoundError(group_name, details=err_text)

    # 5. Invalid parameters / validation errors from AWS
    if any(
        marker in lower
        for marker in ("badrequestexception", "invalidparameterexception", "validationexception")
    ):
        return InvalidFilterError(
            f"AWS rejected the request parameters: {err_text.splitlines()[0] if err_text else 'Bad request'}",
            hint="Verify your resource group name, tag filter syntax, or AWS region.",
        )

    # 6. Generic CLI failure
    cmd_summary = " ".join(["aws", *args[:2]])
    return AWSCLIExecutionError(
        f"AWS CLI command '{cmd_summary}' failed with exit code {returncode}.",
        details=err_text or None,
    )


def run_aws_cli(
    args: list[str],
    *,
    region: Optional[str] = None,
    timeout: int = DEFAULT_CLI_TIMEOUT_SECONDS,
    parse_json: bool = True,
) -> Any:
    """
    Execute an AWS CLI command safely via Python's subprocess module (shell=False).

    Automatically appends `--output json` when `parse_json=True` and `--region <region>`
    when a region override is provided.
    """
    aws_exe = _find_aws_executable()
    cmd = [aws_exe, *args]

    if region and "--region" not in args:
        cmd.extend(["--region", region])
    if parse_json and "--output" not in args:
        cmd.extend(["--output", "json"])

    extra_env = _aws_env_override.get()
    subprocess_env = {**os.environ, **extra_env} if extra_env else None

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=subprocess_env,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AWSCLINotInstalledError(details=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        cmd_summary = " ".join(["aws", *args[:2]])
        raise AWSCLIExecutionError(
            f"AWS CLI command '{cmd_summary}' timed out after {timeout} seconds.",
            code="AWS_CLI_TIMEOUT",
            status_code=504,
            hint="Check your network connectivity to AWS endpoints or narrow the resource group scope.",
        ) from exc

    if completed.returncode != 0:
        raise _classify_aws_error(completed.stderr, args, completed.returncode)

    stdout = (completed.stdout or "").strip()
    if not parse_json:
        return stdout

    if not stdout:
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AWSCLIExecutionError(
            "Failed to parse JSON output returned by AWS CLI.",
            code="AWS_CLI_INVALID_JSON",
            status_code=502,
            details=f"{exc}: {stdout[:300]}",
        ) from exc


# ---------------------------------------------------------------------------
# AWS Identity & Region Helpers
# ---------------------------------------------------------------------------


def assume_iam_role(
    role_arn: str,
    *,
    external_id: Optional[str] = None,
    region: Optional[str] = None,
) -> dict[str, str]:
    """
    Assume an AWS IAM Role via `aws sts assume-role` and return temporary
    environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN).

    Requires valid base caller credentials on the host with `sts:AssumeRole` permission
    and a matching trust policy on the target role.
    """
    cleaned_arn = (role_arn or "").strip()
    if not ROLE_ARN_REGEX.match(cleaned_arn):
        raise InvalidFilterError(
            f"Invalid IAM Role ARN '{cleaned_arn}'.",
            hint="Expected format: arn:aws:iam::<12-digit-account-id>:role/<role-name>",
        )

    args = [
        "sts",
        "assume-role",
        "--role-arn",
        cleaned_arn,
        "--role-session-name",
        "ai-cloud-cost-detective",
    ]
    if external_id and external_id.strip():
        args.extend(["--external-id", external_id.strip()])

    try:
        resp = run_aws_cli(args, region=region, timeout=20)
    except AWSCredentialsError as exc:
        raise AWSCredentialsError(
            "Cannot call aws sts assume-role: no caller credentials found on this host.",
            details=(
                "AWS STS AssumeRole requires a caller identity (e.g. an EC2/ECS IAM instance role "
                "in production, or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in backend/.env for local dev). "
                f"Original error: {exc.details or exc.message}"
            ),
        ) from exc
    except AWSIAMAccessDeniedError as exc:
        raise AWSIAMAccessDeniedError(
            f"Not authorized to assume IAM role '{cleaned_arn}'.",
            details=exc.details or exc.message,
        ) from exc

    creds = resp.get("Credentials") or {}
    access_key = creds.get("AccessKeyId")
    secret_key = creds.get("SecretAccessKey")
    session_token = creds.get("SessionToken")

    if not access_key or not secret_key or not session_token:
        raise AWSCredentialsError(
            f"STS assume-role for '{cleaned_arn}' did not return temporary credentials.",
            details=str(resp)[:300],
        )

    return {
        "AWS_ACCESS_KEY_ID": str(access_key),
        "AWS_SECRET_ACCESS_KEY": str(secret_key),
        "AWS_SESSION_TOKEN": str(session_token),
    }


@contextlib.contextmanager
def _assumed_role_context(
    role_arn: Optional[str],
    external_id: Optional[str] = None,
    region: Optional[str] = None,
):
    """Temporarily scope all subprocess AWS CLI calls to an assumed IAM Role."""
    cleaned = (role_arn or "").strip()
    if not cleaned:
        yield
        return

    role_env = assume_iam_role(cleaned, external_id=external_id, region=region)
    token = _aws_env_override.set(role_env)
    try:
        yield
    finally:
        _aws_env_override.reset(token)


def verify_aws_identity(region: Optional[str] = None) -> dict[str, Any]:
    """
    Verify AWS CLI installation and active credentials via `aws sts get-caller-identity`.
    Raises AWSCLINotInstalledError or AWSCredentialsError on failure.
    """
    try:
        identity = run_aws_cli(["sts", "get-caller-identity"], region=region, timeout=15)
    except (AWSCLINotInstalledError, AWSCredentialsError, AWSIAMAccessDeniedError):
        raise
    except AWSCLIBaseError as exc:
        raise AWSCredentialsError(
            "Failed to verify AWS caller identity (not logged in or invalid credentials).",
            details=exc.details or exc.message,
        ) from exc

    if not isinstance(identity, dict) or not identity.get("Account"):
        raise AWSCredentialsError(
            "Unexpected response from 'aws sts get-caller-identity'.",
            details=str(identity)[:300],
        )
    return identity


def get_configured_region(requested_region: Optional[str] = None) -> Optional[str]:
    """Determine the target AWS region from request, environment, or AWS CLI config."""
    if requested_region and requested_region.strip():
        return requested_region.strip()

    for env_key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        val = os.getenv(env_key, "").strip()
        if val:
            return val

    try:
        region = run_aws_cli(["configure", "get", "region"], parse_json=False, timeout=10)
        if region:
            return region.strip()
    except AWSCLIBaseError:
        pass

    return None


def is_demo_mode() -> bool:
    """Return True when DEMO_MODE=true is enabled to simulate AWS CLI responses."""
    return os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _demo_resource_groups(region: str) -> dict[str, Any]:
    return {
        "account_id": "123456789012",
        "region": region,
        "count": 3,
        "resource_groups": [
            {
                "name": "prod-web-stack",
                "arn": f"arn:aws:resource-groups:{region}:123456789012:group/prod-web-stack",
                "description": "Demo: Production web application tier (EC2, RDS, EBS, NAT, S3)",
                "source": "aws_resource_groups",
                "region": region,
                "resource_count": 6,
            },
            {
                "name": "tag:Environment=staging",
                "arn": None,
                "description": "Demo: Resources tagged Environment=staging (4 resources)",
                "source": "tag_filter",
                "region": region,
                "resource_count": 4,
            },
            {
                "name": "stack:data-pipeline",
                "arn": None,
                "description": "Demo: CloudFormation stack 'data-pipeline' (4 tagged resources)",
                "source": "cloudformation_stack",
                "region": region,
                "resource_count": 4,
            },
        ],
        "warnings": [
            "DEMO_MODE=true is active: showing simulated AWS resource groups. Set DEMO_MODE=false in backend/.env to scan a real AWS account."
        ],
    }


def _demo_scan_result(
    resource_group: str,
    filter_type: str,
    region: str,
    include_costs: bool,
) -> dict[str, Any]:
    target_region = region if region and region != "default" else "us-east-1"

    all_demo_resources: list[dict[str, Any]] = [
        {
            "arn": f"arn:aws:ec2:{target_region}:123456789012:instance/i-0demo91a2b3c4d5e01",
            "resource_type": "AWS::EC2::Instance",
            "service": "ec2",
            "name": "web-api-legacy-node-01",
            "resource_id": "i-0demo91a2b3c4d5e01",
            "region": target_region,
            "location": f"{target_region}a",
            "instance_type": "m5.2xlarge",
            "sku": "m5.2xlarge",
            "state": "running",
            "tags": {"Name": "web-api-legacy-node-01", "Environment": "staging", "Owner": "platform"},
            "details": {
                "availability_zone": f"{target_region}a",
                "architecture": "x86_64",
                "platform": "Linux/UNIX",
                "ebs_optimized": True,
                "avg_cpu_utilization_14d": "6.4%",
            },
        },
        {
            "arn": f"arn:aws:ec2:{target_region}:123456789012:volume/vol-0demo88f7e6d5c4002",
            "resource_type": "AWS::EC2::Volume",
            "service": "ec2",
            "name": "orphaned-analytics-scratch",
            "resource_id": "vol-0demo88f7e6d5c4002",
            "region": target_region,
            "location": f"{target_region}a",
            "instance_type": "gp2",
            "sku": "gp2 (500 GiB)",
            "state": "available",
            "tags": {"Name": "orphaned-analytics-scratch", "Environment": "staging"},
            "details": {
                "volume_type": "gp2",
                "size_gib": 500,
                "iops": 1500,
                "encrypted": True,
                "attached_instances": [],
            },
        },
        {
            "arn": f"arn:aws:rds:{target_region}:123456789012:db:staging-orders-db",
            "resource_type": "AWS::RDS::DBInstance",
            "service": "rds",
            "name": "staging-orders-db",
            "resource_id": "staging-orders-db",
            "region": target_region,
            "location": f"{target_region}b",
            "instance_type": "db.r5.xlarge",
            "sku": "db.r5.xlarge / postgres / Multi-AZ",
            "state": "available",
            "tags": {"Environment": "staging", "Service": "orders"},
            "details": {
                "db_instance_class": "db.r5.xlarge",
                "engine": "postgres",
                "engine_version": "15.4",
                "allocated_storage_gib": 400,
                "storage_type": "gp2",
                "multi_az": True,
                "publicly_accessible": False,
            },
        },
        {
            "arn": f"arn:aws:ec2:{target_region}:123456789012:natgateway/nat-0demo123456789abc3",
            "resource_type": "AWS::EC2::NatGateway",
            "service": "ec2",
            "name": "idle-dev-nat-gw",
            "resource_id": "nat-0demo123456789abc3",
            "region": target_region,
            "location": target_region,
            "instance_type": "nat-gateway-public",
            "sku": "NAT Gateway (public)",
            "state": "available",
            "tags": {"Name": "idle-dev-nat-gw", "Environment": "staging"},
            "details": {
                "connectivity_type": "public",
                "bytes_out_to_destination_30d": 0,
            },
        },
        {
            "arn": f"arn:aws:dynamodb:{target_region}:123456789012:table/session-events-archive",
            "resource_type": "AWS::DynamoDB::Table",
            "service": "dynamodb",
            "name": "session-events-archive",
            "resource_id": "session-events-archive",
            "region": target_region,
            "location": target_region,
            "instance_type": "PROVISIONED",
            "sku": "PROVISIONED (250 RCU / 250 WCU)",
            "state": "ACTIVE",
            "tags": {"Project": "web-platform"},
            "details": {
                "billing_mode": "PROVISIONED",
                "read_capacity_units": 250,
                "write_capacity_units": 250,
                "item_count": 18400,
            },
        },
        {
            "arn": "arn:aws:s3:::demo-raw-logs-bucket-2026",
            "resource_type": "AWS::S3::Bucket",
            "service": "s3",
            "name": "demo-raw-logs-bucket-2026",
            "resource_id": "demo-raw-logs-bucket-2026",
            "region": target_region,
            "location": target_region,
            "instance_type": None,
            "sku": "S3 Standard / Bucket",
            "state": "active",
            "tags": {"Environment": "prod"},
            "details": {
                "storage_class": "STANDARD",
                "lifecycle_rules_configured": False,
                "approx_size_tb": 4.2,
            },
        },
    ]

    if "staging" in resource_group.lower():
        selected = all_demo_resources[:4]
    elif "data-pipeline" in resource_group.lower():
        selected = [all_demo_resources[1], all_demo_resources[3], all_demo_resources[4], all_demo_resources[5]]
    else:
        selected = all_demo_resources

    service_counts: Counter[str] = Counter(r["service"] for r in selected)
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=30)

    cost_summary = (
        {
            "period_start": start_dt.isoformat(),
            "period_end": end_dt.isoformat(),
            "total_cost": 1248.60,
            "currency": "USD",
            "by_service": [
                {"service": "Amazon Relational Database Service", "amount": 542.40, "unit": "USD"},
                {"service": "Amazon Elastic Compute Cloud - Compute", "amount": 418.20, "unit": "USD"},
                {"service": "Amazon DynamoDB", "amount": 135.80, "unit": "USD"},
                {"service": "Amazon Simple Storage Service", "amount": 96.60, "unit": "USD"},
                {"service": "Amazon VPC (NAT Gateway)", "amount": 55.60, "unit": "USD"},
            ],
            "filter_applied": resource_group,
            "warning": None,
        }
        if include_costs
        else None
    )

    return {
        "resource_group": resource_group,
        "filter_type": filter_type,
        "account_id": "123456789012",
        "region": target_region,
        "resource_count": len(selected),
        "resources": selected,
        "summary_by_service": dict(sorted(service_counts.items())),
        "cost_summary": cost_summary,
        "warnings": [
            "DEMO_MODE=true is active: AWS inventory is simulated, while OpenAI GPT-4o analysis, WebSocket progress, and Amazon RDS history are live."
        ],
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def check_cli_health(region: Optional[str] = None) -> dict[str, Any]:
    """Non-throwing health check reporting AWS CLI availability and authentication status."""
    if is_demo_mode():
        active_region = get_configured_region(region) or "us-east-1"
        return {
            "aws_cli_installed": True,
            "aws_cli_version": "demo-mode",
            "authenticated": True,
            "account_id": "123456789012",
            "caller_arn": "arn:aws:iam::123456789012:user/demo-finops",
            "region": active_region,
            "demo_mode": True,
            "error": None,
        }

    result: dict[str, Any] = {
        "aws_cli_installed": False,
        "aws_cli_version": None,
        "authenticated": False,
        "account_id": None,
        "caller_arn": None,
        "region": None,
        "error": None,
    }

    try:
        version_out = run_aws_cli(["--version"], parse_json=False, timeout=10)
        result["aws_cli_installed"] = True
        result["aws_cli_version"] = version_out.splitlines()[0] if version_out else None
    except AWSCLINotInstalledError as exc:
        result["error"] = exc.to_dict()["error"]
        return result
    except AWSCLIBaseError as exc:
        result["aws_cli_installed"] = True
        result["error"] = exc.to_dict()["error"]

    try:
        identity = verify_aws_identity(region=region)
        result["authenticated"] = True
        result["account_id"] = identity.get("Account")
        result["caller_arn"] = identity.get("Arn")
        result["region"] = get_configured_region(region) or "default"
    except AWSCLIBaseError as exc:
        result["error"] = exc.to_dict()["error"]

    return result


# ---------------------------------------------------------------------------
# ARN & Tag Parsing Utilities
# ---------------------------------------------------------------------------


def parse_arn(arn: str, default_region: str = "global") -> dict[str, str]:
    """
    Parse an AWS ARN into structured components.

    Supports formats:
      - arn:aws:s3:::my-bucket
      - arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0
      - arn:aws:rds:us-east-1:123456789012:db:my-database
      - arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-alb/1234567890abcdef
    """
    parts = arn.split(":", 5)
    if len(parts) < 6 or parts[0] != "arn":
        return {
            "arn": arn,
            "partition": "aws",
            "service": "unknown",
            "region": default_region,
            "account_id": "",
            "sub_type": "resource",
            "resource_id": arn,
        }

    _, partition, service, region, account_id, resource_part = parts

    if not region:
        region = "global" if service in GLOBAL_SERVICES else default_region

    if service == "s3":
        sub_type = "bucket"
        resource_id = resource_part.split("/", 1)[0]
    elif "/" in resource_part and (":" not in resource_part or resource_part.index("/") < resource_part.index(":")):
        sub_type, resource_id = resource_part.split("/", 1)
    elif ":" in resource_part:
        sub_type, resource_id = resource_part.split(":", 1)
    else:
        sub_type = "resource"
        resource_id = resource_part

    return {
        "arn": arn,
        "partition": partition,
        "service": service,
        "region": region,
        "account_id": account_id,
        "sub_type": sub_type,
        "resource_id": resource_id,
    }


def _tags_list_to_dict(raw_tags: Optional[list[dict[str, Any]]]) -> dict[str, str]:
    tags: dict[str, str] = {}
    if not raw_tags:
        return tags
    for entry in raw_tags:
        if not isinstance(entry, dict):
            continue
        key = entry.get("Key") if "Key" in entry else entry.get("key")
        value = entry.get("Value") if "Value" in entry else entry.get("value")
        if key is not None:
            tags[str(key)] = "" if value is None else str(value)
    return tags


def _infer_cfn_resource_type(
    service: str,
    sub_type: str,
    existing_type: Optional[str] = None,
) -> str:
    if existing_type:
        return existing_type
    mapped = SERVICE_SUBTYPE_TO_CFN.get((service.lower(), sub_type.lower()))
    if mapped:
        return mapped
    return f"{service}:{sub_type}" if sub_type else service


def _derive_display_name(parsed_arn: dict[str, str], tags: dict[str, str]) -> str:
    for key in ("Name", "name"):
        if tags.get(key):
            return tags[key]
    resource_id = parsed_arn["resource_id"]
    # For ALB ARNs like "app/my-alb/abc123", extract the readable name segment
    if parsed_arn["service"] == "elasticloadbalancing" and "/" in resource_id:
        segments = resource_id.split("/")
        if len(segments) >= 2:
            return segments[1]
    return resource_id


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Resource Group Discovery (GET /api/resource-groups)
# ---------------------------------------------------------------------------


def list_resource_groups(
    *,
    region: Optional[str] = None,
    include_tag_groups: bool = True,
    role_arn: Optional[str] = None,
    external_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    List AWS Resource Groups via `aws resource-groups list-groups --output json`.

    When `include_tag_groups=True`, also inspects tagged resources via
    `aws resourcegroupstaggingapi get-resources --output json` to surface
    CloudFormation stacks (`stack:<name>`) and common tag groups (`tag:Key=Value`).
    """
    has_role = bool(role_arn and role_arn.strip())
    if not has_role:
        if is_demo_mode():
            active_region = get_configured_region(region) or "us-east-1"
            return _demo_resource_groups(active_region)
        raise InvalidFilterError(
            "An AWS IAM Role ARN is required to list resource groups.",
            hint="Enter your IAM Role ARN (e.g. arn:aws:iam::123456789012:role/CostDetectiveReadOnlyRole).",
        )

    with _assumed_role_context(role_arn, external_id, region):
        return _list_resource_groups_impl(region=region, include_tag_groups=include_tag_groups)


def _list_resource_groups_impl(
    *,
    region: Optional[str] = None,
    include_tag_groups: bool = True,
) -> dict[str, Any]:
    identity = verify_aws_identity(region=region)
    active_region = get_configured_region(region)
    display_region = active_region or "default"
    warnings: list[str] = []

    groups_by_name: dict[str, dict[str, Any]] = {}

    # 1. Formal AWS Resource Groups
    try:
        rg_response = run_aws_cli(["resource-groups", "list-groups"], region=active_region)
        for item in rg_response.get("Groups", []):
            name = item.get("Name")
            if not name:
                continue
            groups_by_name[name] = {
                "name": name,
                "arn": item.get("GroupArn"),
                "description": item.get("Description"),
                "source": "aws_resource_groups",
                "region": display_region,
                "resource_count": None,
            }
        for item in rg_response.get("GroupIdentifiers", []):
            name = item.get("GroupName")
            if name and name not in groups_by_name:
                groups_by_name[name] = {
                    "name": name,
                    "arn": item.get("GroupArn"),
                    "description": None,
                    "source": "aws_resource_groups",
                    "region": display_region,
                    "resource_count": None,
                }
    except AWSIAMAccessDeniedError as exc:
        if not include_tag_groups:
            raise
        warnings.append(
            f"Could not list formal AWS Resource Groups (missing resource-groups:ListGroups permission): {exc.details or exc.message}"
        )

    # 2. Supplemental Tag & CloudFormation Stack Groups via Resource Groups Tagging API
    if include_tag_groups:
        try:
            tag_response = run_aws_cli(
                ["resourcegroupstaggingapi", "get-resources"],
                region=active_region,
            )
            mappings = tag_response.get("ResourceTagMappingList", [])
            stack_counts: Counter[str] = Counter()
            tag_counts: Counter[tuple[str, str]] = Counter()

            for mapping in mappings:
                tags = _tags_list_to_dict(mapping.get("Tags"))
                if CFN_STACK_TAG_KEY in tags and tags[CFN_STACK_TAG_KEY]:
                    stack_counts[tags[CFN_STACK_TAG_KEY]] += 1
                for key in COMMON_GROUP_TAG_KEYS:
                    if key in tags and tags[key]:
                        tag_counts[(key, tags[key])] += 1

            for stack_name, count in sorted(stack_counts.items()):
                virtual_name = f"stack:{stack_name}"
                if virtual_name not in groups_by_name:
                    groups_by_name[virtual_name] = {
                        "name": virtual_name,
                        "arn": None,
                        "description": f"CloudFormation stack '{stack_name}' ({count} tagged resources)",
                        "source": "cloudformation_stack",
                        "region": display_region,
                        "resource_count": count,
                    }

            for (key, value), count in sorted(tag_counts.items()):
                virtual_name = f"tag:{key}={value}"
                if virtual_name not in groups_by_name:
                    groups_by_name[virtual_name] = {
                        "name": virtual_name,
                        "arn": None,
                        "description": f"Resources tagged {key}={value} ({count} resources)",
                        "source": "tag_filter",
                        "region": display_region,
                        "resource_count": count,
                    }
        except AWSIAMAccessDeniedError as exc:
            # If formal groups also failed, propagate permission error
            if not groups_by_name and warnings:
                raise
            warnings.append(
                f"Tag-based group discovery skipped (missing tag:GetResources permission): {exc.details or exc.message}"
            )
        except AWSCLIBaseError as exc:
            if not groups_by_name and warnings:
                raise
            warnings.append(f"Tag-based group discovery skipped: {exc.message}")

    # Always offer a Full Account Scan option first so users can audit all AWS services
    # (tagged and untagged) + full 30-day Cost Explorer spend in one click.
    groups_by_name["all"] = {
        "name": "all",
        "arn": None,
        "description": f"Full AWS Account Scan — all services in {display_region} + 30-day Cost Explorer",
        "source": "full_account",
        "region": display_region,
        "resource_count": None,
    }

    # Sort Full Account Scan first, then formal AWS Resource Groups, stacks, and tag filters
    source_order = {
        "full_account": 0,
        "aws_resource_groups": 1,
        "cloudformation_stack": 2,
        "tag_filter": 3,
    }
    ordered_groups = sorted(
        groups_by_name.values(),
        key=lambda g: (source_order.get(g["source"], 99), g["name"].lower()),
    )

    return {
        "account_id": identity["Account"],
        "region": display_region,
        "count": len(ordered_groups),
        "resource_groups": ordered_groups,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Resource Group Filter Parsing
# ---------------------------------------------------------------------------


def _parse_group_filter(
    resource_group: str,
    tag_key: Optional[str] = None,
    tag_value: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """
    Map request input to an AWS grouping strategy:
      - Explicit tag_key / tag_value OR "tag:Key=Value" / "tag:Key" -> Tagging API tag filter
      - "stack:<stack-name>" -> Tagging API filter on aws:cloudformation:stack-name
      - "service:<service>" -> Tagging API resource-type filter
      - "<group-name>" -> AWS Resource Groups `list-group-resources --group-name <name>`
    """
    raw = (resource_group or "").strip()

    if tag_key and tag_key.strip():
        return (
            "tag",
            {
                "key": tag_key.strip(),
                "value": tag_value.strip() if tag_value and tag_value.strip() else None,
            },
        )

    if not raw:
        raise InvalidFilterError("Field 'resource_group' must not be empty.")

    if any(ord(ch) < 32 for ch in raw):
        raise InvalidFilterError("Field 'resource_group' contains invalid control characters.")

    lower = raw.lower()

    if lower in ("all", "account:all", "*"):
        return ("all", {})

    if lower.startswith("tag:"):
        expr = raw[4:].strip()
        if not expr:
            raise InvalidFilterError(
                "Tag filter cannot be empty.",
                hint="Use format 'tag:Key=Value' or 'tag:Key'.",
            )
        if "=" in expr:
            key, val = expr.split("=", 1)
            key, val = key.strip(), val.strip()
            if not key:
                raise InvalidFilterError("Tag filter key cannot be empty.")
            return ("tag", {"key": key, "value": val or None})
        return ("tag", {"key": expr, "value": None})

    if lower.startswith("stack:"):
        stack_name = raw[6:].strip()
        if not stack_name:
            raise InvalidFilterError(
                "CloudFormation stack name cannot be empty.",
                hint="Use format 'stack:<stack-name>'.",
            )
        return ("stack", {"stack_name": stack_name})

    if lower.startswith("service:"):
        service_filter = raw[8:].strip().lower()
        if not service_filter or not re.fullmatch(r"[a-z0-9:-]+", service_filter):
            raise InvalidFilterError(
                f"Invalid service filter '{service_filter}'.",
                hint="Use format 'service:ec2', 'service:ec2:instance', or 'service:rds'.",
            )
        return ("service", {"service_filter": service_filter})

    if len(raw) > 300:
        raise InvalidFilterError("Field 'resource_group' exceeds maximum allowed length (300).")

    return ("resource_group", {"group_name": raw})


# ---------------------------------------------------------------------------
# Fetching & Enriching Resources (POST /api/analyze)
# ---------------------------------------------------------------------------


def _fetch_tags_for_arns(
    arns: list[str],
    region: Optional[str],
    warnings: list[str],
) -> dict[str, dict[str, str]]:
    """Batch-fetch tags for ARNs using `aws resourcegroupstaggingapi get-resources`."""
    tags_by_arn: dict[str, dict[str, str]] = {}
    if not arns:
        return tags_by_arn

    for batch in _chunked(arns, 100):
        try:
            resp = run_aws_cli(
                ["resourcegroupstaggingapi", "get-resources", "--resource-arn-list", *batch],
                region=region,
            )
            for mapping in resp.get("ResourceTagMappingList", []):
                arn = mapping.get("ResourceARN")
                if arn:
                    tags_by_arn[arn] = _tags_list_to_dict(mapping.get("Tags"))
        except AWSIAMAccessDeniedError as exc:
            warnings.append(
                f"Unable to fetch resource tags (missing tag:GetResources permission): {exc.details or exc.message}"
            )
            break
        except AWSCLIBaseError as exc:
            warnings.append(f"Partial tag lookup failure: {exc.message}")
            break

    return tags_by_arn


def _fetch_full_account_resources(
    region: Optional[str],
    display_region: str,
    account_id: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """
    Scan all major AWS services in the active region (tagged AND untagged) plus
    all tagged resources from Resource Groups Tagging API, deduplicated by ARN.
    """
    by_arn: dict[str, dict[str, Any]] = {}
    arn_region = display_region if display_region != "default" else "us-east-1"

    def _upsert(item: dict[str, Any]) -> None:
        existing = by_arn.get(item["arn"])
        if not existing:
            by_arn[item["arn"]] = item
            return
        # Merge richer details/tags if already discovered via Tagging API
        if item.get("tags"):
            existing["tags"] = {**existing.get("tags", {}), **item["tags"]}
            if existing["tags"].get("Name"):
                existing["name"] = existing["tags"]["Name"]
        for field in ("instance_type", "sku", "state", "location"):
            if item.get(field):
                existing[field] = item[field]
        if item.get("details"):
            existing["details"].update(item["details"])

    # 1. All tagged resources across all AWS services (Lambda, DynamoDB, S3, SQS, SNS, VPC, etc.)
    try:
        tag_resp = run_aws_cli(["resourcegroupstaggingapi", "get-resources"], region=region)
        for mapping in tag_resp.get("ResourceTagMappingList", []):
            arn = mapping.get("ResourceARN")
            if not arn:
                continue
            parsed = parse_arn(arn, default_region=display_region)
            tags = _tags_list_to_dict(mapping.get("Tags"))
            _upsert(
                {
                    "arn": arn,
                    "resource_type": _infer_cfn_resource_type(parsed["service"], parsed["sub_type"]),
                    "service": parsed["service"],
                    "sub_type": parsed["sub_type"],
                    "name": _derive_display_name(parsed, tags),
                    "resource_id": parsed["resource_id"],
                    "region": parsed["region"],
                    "location": parsed["region"],
                    "instance_type": None,
                    "sku": None,
                    "state": None,
                    "tags": tags,
                    "details": {},
                }
            )
    except AWSCLIBaseError as exc:
        warnings.append(f"Tagging API full-account scan skipped: {exc.message}")

    # 2. All EC2 instances (tagged + untagged)
    try:
        ec2_resp = run_aws_cli(["ec2", "describe-instances"], region=region)
        for reservation in ec2_resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                iid = inst.get("InstanceId")
                if not iid:
                    continue
                az = (inst.get("Placement") or {}).get("AvailabilityZone")
                inst_region = az[:-1] if az and len(az) > 1 else arn_region
                arn = f"arn:aws:ec2:{inst_region}:{account_id}:instance/{iid}"
                tags = _tags_list_to_dict(inst.get("Tags"))
                inst_type = inst.get("InstanceType")
                _upsert(
                    {
                        "arn": arn,
                        "resource_type": "AWS::EC2::Instance",
                        "service": "ec2",
                        "sub_type": "instance",
                        "name": tags.get("Name") or iid,
                        "resource_id": iid,
                        "region": inst_region,
                        "location": az or inst_region,
                        "instance_type": inst_type,
                        "sku": inst_type,
                        "state": (inst.get("State") or {}).get("Name"),
                        "tags": tags,
                        "details": {
                            "availability_zone": az,
                            "vpc_id": inst.get("VpcId"),
                            "architecture": inst.get("Architecture"),
                            "platform": inst.get("PlatformDetails") or inst.get("Platform") or "Linux/UNIX",
                            "launch_time": inst.get("LaunchTime"),
                            "ebs_optimized": inst.get("EbsOptimized"),
                        },
                    }
                )
    except AWSCLIBaseError as exc:
        warnings.append(f"EC2 instances scan skipped: {exc.message}")

    # 3. All EBS volumes (including unattached/orphaned volumes)
    try:
        vol_resp = run_aws_cli(["ec2", "describe-volumes"], region=region)
        for vol in vol_resp.get("Volumes", []):
            vid = vol.get("VolumeId")
            if not vid:
                continue
            az = vol.get("AvailabilityZone")
            vol_region = az[:-1] if az and len(az) > 1 else arn_region
            arn = f"arn:aws:ec2:{vol_region}:{account_id}:volume/{vid}"
            tags = _tags_list_to_dict(vol.get("Tags"))
            vtype = vol.get("VolumeType")
            size = vol.get("Size")
            attachments = [
                a.get("InstanceId") for a in vol.get("Attachments", []) if a.get("InstanceId")
            ]
            _upsert(
                {
                    "arn": arn,
                    "resource_type": "AWS::EC2::Volume",
                    "service": "ec2",
                    "sub_type": "volume",
                    "name": tags.get("Name") or vid,
                    "resource_id": vid,
                    "region": vol_region,
                    "location": az or vol_region,
                    "instance_type": vtype,
                    "sku": f"{vtype} ({size} GiB)" if vtype and size is not None else vtype,
                    "state": vol.get("State"),
                    "tags": tags,
                    "details": {
                        "volume_type": vtype,
                        "size_gib": size,
                        "iops": vol.get("Iops"),
                        "encrypted": vol.get("Encrypted"),
                        "attached_instances": attachments,
                    },
                }
            )
    except AWSCLIBaseError as exc:
        warnings.append(f"EBS volumes scan skipped: {exc.message}")

    # 4. All NAT Gateways
    try:
        ngw_resp = run_aws_cli(["ec2", "describe-nat-gateways"], region=region)
        for ngw in ngw_resp.get("NatGateways", []):
            nid = ngw.get("NatGatewayId")
            if not nid:
                continue
            arn = f"arn:aws:ec2:{arn_region}:{account_id}:natgateway/{nid}"
            tags = _tags_list_to_dict(ngw.get("Tags"))
            conn_type = ngw.get("ConnectivityType", "public")
            _upsert(
                {
                    "arn": arn,
                    "resource_type": "AWS::EC2::NatGateway",
                    "service": "ec2",
                    "sub_type": "natgateway",
                    "name": tags.get("Name") or nid,
                    "resource_id": nid,
                    "region": arn_region,
                    "location": arn_region,
                    "instance_type": f"nat-gateway-{conn_type}",
                    "sku": f"NAT Gateway ({conn_type})",
                    "state": ngw.get("State"),
                    "tags": tags,
                    "details": {
                        "connectivity_type": conn_type,
                        "vpc_id": ngw.get("VpcId"),
                    },
                }
            )
    except AWSCLIBaseError as exc:
        warnings.append(f"NAT Gateways scan skipped: {exc.message}")

    # 5. All RDS DB instances
    try:
        rds_resp = run_aws_cli(["rds", "describe-db-instances"], region=region)
        for db in rds_resp.get("DBInstances", []):
            db_id = db.get("DBInstanceIdentifier")
            arn = db.get("DBInstanceArn") or (
                f"arn:aws:rds:{arn_region}:{account_id}:db:{db_id}" if db_id else None
            )
            if not arn or not db_id:
                continue
            az = db.get("AvailabilityZone")
            db_region = az[:-1] if az and len(az) > 1 else arn_region
            db_class = db.get("DBInstanceClass")
            engine = db.get("Engine")
            multi_az = db.get("MultiAZ")
            sku_parts = [p for p in (db_class, engine, "Multi-AZ" if multi_az else None) if p]
            _upsert(
                {
                    "arn": arn,
                    "resource_type": "AWS::RDS::DBInstance",
                    "service": "rds",
                    "sub_type": "db",
                    "name": db_id,
                    "resource_id": db_id,
                    "region": db_region,
                    "location": az or db_region,
                    "instance_type": db_class,
                    "sku": " / ".join(sku_parts) if sku_parts else db_class,
                    "state": db.get("DBInstanceStatus"),
                    "tags": _tags_list_to_dict(db.get("TagList")),
                    "details": {
                        "db_instance_class": db_class,
                        "engine": engine,
                        "allocated_storage_gib": db.get("AllocatedStorage"),
                        "storage_type": db.get("StorageType"),
                        "multi_az": multi_az,
                    },
                }
            )
    except AWSCLIBaseError as exc:
        warnings.append(f"RDS instances scan skipped: {exc.message}")

    # 6. All ELBv2 Load Balancers
    try:
        elb_resp = run_aws_cli(["elbv2", "describe-load-balancers"], region=region)
        for lb in elb_resp.get("LoadBalancers", []):
            arn = lb.get("LoadBalancerArn")
            if not arn:
                continue
            lb_name = lb.get("LoadBalancerName") or arn.split("/")[-2]
            lb_type = lb.get("Type")
            scheme = lb.get("Scheme")
            _upsert(
                {
                    "arn": arn,
                    "resource_type": "AWS::ElasticLoadBalancingV2::LoadBalancer",
                    "service": "elasticloadbalancing",
                    "sub_type": "loadbalancer",
                    "name": lb_name,
                    "resource_id": lb_name,
                    "region": arn_region,
                    "location": arn_region,
                    "instance_type": lb_type,
                    "sku": f"{lb_type} ({scheme})" if lb_type and scheme else lb_type,
                    "state": (lb.get("State") or {}).get("Code"),
                    "tags": {},
                    "details": {"type": lb_type, "scheme": scheme, "vpc_id": lb.get("VpcId")},
                }
            )
    except AWSCLIBaseError as exc:
        warnings.append(f"ELBv2 load balancers scan skipped: {exc.message}")

    # 7. All ElastiCache clusters
    try:
        ec_resp = run_aws_cli(["elasticache", "describe-cache-clusters"], region=region)
        for cluster in ec_resp.get("CacheClusters", []):
            cid = cluster.get("CacheClusterId")
            if not cid:
                continue
            arn = cluster.get("ARN") or f"arn:aws:elasticache:{arn_region}:{account_id}:cluster:{cid}"
            node_type = cluster.get("CacheNodeType")
            engine = cluster.get("Engine")
            num_nodes = cluster.get("NumCacheNodes")
            _upsert(
                {
                    "arn": arn,
                    "resource_type": "AWS::ElastiCache::CacheCluster",
                    "service": "elasticache",
                    "sub_type": "cluster",
                    "name": cid,
                    "resource_id": cid,
                    "region": arn_region,
                    "location": arn_region,
                    "instance_type": node_type,
                    "sku": f"{node_type} x{num_nodes} ({engine})"
                    if node_type and num_nodes and engine
                    else node_type,
                    "state": cluster.get("CacheClusterStatus"),
                    "tags": {},
                    "details": {
                        "cache_node_type": node_type,
                        "engine": engine,
                        "num_cache_nodes": num_nodes,
                    },
                }
            )
    except AWSCLIBaseError as exc:
        warnings.append(f"ElastiCache clusters scan skipped: {exc.message}")

    return list(by_arn.values())


def _fetch_base_resources(
    filter_type: str,
    filter_params: dict[str, Any],
    region: Optional[str],
    display_region: str,
    warnings: list[str],
    account_id: str = "",
) -> list[dict[str, Any]]:
    """Fetch initial resource inventory using AWS Resource Groups, Tagging API, or Full Account scan."""
    if filter_type == "all":
        return _fetch_full_account_resources(region, display_region, account_id, warnings)

    resources: list[dict[str, Any]] = []

    if filter_type == "resource_group":
        group_name = filter_params["group_name"]
        resp = run_aws_cli(
            ["resource-groups", "list-group-resources", "--group-name", group_name],
            region=region,
        )

        # Surface any AWS QueryErrors as warnings
        for q_err in resp.get("QueryErrors", []):
            err_msg = q_err.get("Message") or q_err.get("ErrorCode")
            if err_msg:
                warnings.append(f"AWS Resource Groups query notice: {err_msg}")

        seen_arns: set[str] = set()
        raw_entries: list[tuple[str, Optional[str]]] = []

        for item in resp.get("Resources", []):
            identifier = item.get("Identifier") or {}
            arn = identifier.get("ResourceArn")
            rtype = identifier.get("ResourceType")
            if arn and arn not in seen_arns:
                seen_arns.add(arn)
                raw_entries.append((arn, rtype))

        for item in resp.get("ResourceIdentifiers", []):
            arn = item.get("ResourceArn")
            rtype = item.get("ResourceType")
            if arn and arn not in seen_arns:
                seen_arns.add(arn)
                raw_entries.append((arn, rtype))

        tags_by_arn = _fetch_tags_for_arns([arn for arn, _ in raw_entries], region, warnings)

        for arn, cfn_type in raw_entries:
            parsed = parse_arn(arn, default_region=display_region)
            tags = tags_by_arn.get(arn, {})
            resources.append(
                {
                    "arn": arn,
                    "resource_type": _infer_cfn_resource_type(
                        parsed["service"], parsed["sub_type"], cfn_type
                    ),
                    "service": parsed["service"],
                    "sub_type": parsed["sub_type"],
                    "name": _derive_display_name(parsed, tags),
                    "resource_id": parsed["resource_id"],
                    "region": parsed["region"],
                    "location": parsed["region"],
                    "instance_type": None,
                    "sku": None,
                    "state": None,
                    "tags": tags,
                    "details": {},
                }
            )
        return resources

    # Tag / CloudFormation Stack / Service filters via resourcegroupstaggingapi
    cli_args = ["resourcegroupstaggingapi", "get-resources"]
    if filter_type == "tag":
        key = filter_params["key"]
        val = filter_params.get("value")
        tag_filter_payload = [{"Key": key, "Values": [val]}] if val is not None else [{"Key": key}]
        cli_args.extend(["--tag-filters", json.dumps(tag_filter_payload)])
    elif filter_type == "stack":
        stack_name = filter_params["stack_name"]
        tag_filter_payload = [{"Key": CFN_STACK_TAG_KEY, "Values": [stack_name]}]
        cli_args.extend(["--tag-filters", json.dumps(tag_filter_payload)])
    elif filter_type == "service":
        cli_args.extend(["--resource-type-filters", filter_params["service_filter"]])

    resp = run_aws_cli(cli_args, region=region)
    for mapping in resp.get("ResourceTagMappingList", []):
        arn = mapping.get("ResourceARN")
        if not arn:
            continue
        parsed = parse_arn(arn, default_region=display_region)
        tags = _tags_list_to_dict(mapping.get("Tags"))
        resources.append(
            {
                "arn": arn,
                "resource_type": _infer_cfn_resource_type(parsed["service"], parsed["sub_type"]),
                "service": parsed["service"],
                "sub_type": parsed["sub_type"],
                "name": _derive_display_name(parsed, tags),
                "resource_id": parsed["resource_id"],
                "region": parsed["region"],
                "location": parsed["region"],
                "instance_type": None,
                "sku": None,
                "state": None,
                "tags": tags,
                "details": {},
            }
        )

    return resources


def _enrich_ec2_instances(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    by_id = {item["resource_id"]: item for item in items}
    ids = list(by_id.keys())
    for batch in _chunked(ids, 100):
        try:
            resp = run_aws_cli(
                ["ec2", "describe-instances", "--instance-ids", *batch],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"EC2 instance enrichment skipped ({region or 'default'}): {exc.message}")
            return

        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                iid = inst.get("InstanceId")
                target = by_id.get(iid)
                if not target:
                    continue
                inst_type = inst.get("InstanceType")
                state = (inst.get("State") or {}).get("Name")
                az = (inst.get("Placement") or {}).get("AvailabilityZone")
                inst_tags = _tags_list_to_dict(inst.get("Tags"))
                if inst_tags:
                    target["tags"] = {**target["tags"], **inst_tags}
                    if target["tags"].get("Name"):
                        target["name"] = target["tags"]["Name"]
                target["instance_type"] = inst_type
                target["sku"] = inst_type
                target["state"] = state
                if az:
                    target["location"] = az
                target["details"].update(
                    {
                        "availability_zone": az,
                        "vpc_id": inst.get("VpcId"),
                        "subnet_id": inst.get("SubnetId"),
                        "architecture": inst.get("Architecture"),
                        "platform": inst.get("PlatformDetails") or inst.get("Platform") or "Linux/UNIX",
                        "launch_time": inst.get("LaunchTime"),
                        "ebs_optimized": inst.get("EbsOptimized"),
                        "public_ip": inst.get("PublicIpAddress"),
                    }
                )


def _enrich_ebs_volumes(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    by_id = {item["resource_id"]: item for item in items}
    ids = list(by_id.keys())
    for batch in _chunked(ids, 100):
        try:
            resp = run_aws_cli(
                ["ec2", "describe-volumes", "--volume-ids", *batch],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"EBS volume enrichment skipped ({region or 'default'}): {exc.message}")
            return

        for vol in resp.get("Volumes", []):
            vid = vol.get("VolumeId")
            target = by_id.get(vid)
            if not target:
                continue
            vtype = vol.get("VolumeType")
            size = vol.get("Size")
            az = vol.get("AvailabilityZone")
            vol_tags = _tags_list_to_dict(vol.get("Tags"))
            if vol_tags:
                target["tags"] = {**target["tags"], **vol_tags}
                if target["tags"].get("Name"):
                    target["name"] = target["tags"]["Name"]
            sku = f"{vtype} ({size} GiB)" if vtype and size is not None else vtype
            target["instance_type"] = vtype
            target["sku"] = sku
            target["state"] = vol.get("State")
            if az:
                target["location"] = az
            attachments = [
                a.get("InstanceId") for a in vol.get("Attachments", []) if a.get("InstanceId")
            ]
            target["details"].update(
                {
                    "volume_type": vtype,
                    "size_gib": size,
                    "iops": vol.get("Iops"),
                    "throughput_mbps": vol.get("Throughput"),
                    "encrypted": vol.get("Encrypted"),
                    "attached_instances": attachments,
                }
            )


def _enrich_nat_gateways(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    by_id = {item["resource_id"]: item for item in items}
    ids = list(by_id.keys())
    for batch in _chunked(ids, 100):
        try:
            resp = run_aws_cli(
                ["ec2", "describe-nat-gateways", "--nat-gateway-ids", *batch],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"NAT Gateway enrichment skipped ({region or 'default'}): {exc.message}")
            return

        for ngw in resp.get("NatGateways", []):
            nid = ngw.get("NatGatewayId")
            target = by_id.get(nid)
            if not target:
                continue
            conn_type = ngw.get("ConnectivityType", "public")
            ngw_tags = _tags_list_to_dict(ngw.get("Tags"))
            if ngw_tags:
                target["tags"] = {**target["tags"], **ngw_tags}
                if target["tags"].get("Name"):
                    target["name"] = target["tags"]["Name"]
            target["instance_type"] = f"nat-gateway-{conn_type}"
            target["sku"] = f"NAT Gateway ({conn_type})"
            target["state"] = ngw.get("State")
            target["details"].update(
                {
                    "connectivity_type": conn_type,
                    "vpc_id": ngw.get("VpcId"),
                    "subnet_id": ngw.get("SubnetId"),
                }
            )


def _enrich_rds_instances(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    by_arn = {item["arn"]: item for item in items}
    by_id = {item["resource_id"]: item for item in items}

    try:
        resp = run_aws_cli(["rds", "describe-db-instances"], region=region)
    except AWSCLIBaseError as exc:
        warnings.append(f"RDS enrichment skipped ({region or 'default'}): {exc.message}")
        return

    for db in resp.get("DBInstances", []):
        target = by_arn.get(db.get("DBInstanceArn", "")) or by_id.get(
            db.get("DBInstanceIdentifier", "")
        )
        if not target:
            continue
        db_class = db.get("DBInstanceClass")
        engine = db.get("Engine")
        storage = db.get("AllocatedStorage")
        storage_type = db.get("StorageType")
        multi_az = db.get("MultiAZ")
        az = db.get("AvailabilityZone")
        db_tags = _tags_list_to_dict(db.get("TagList"))
        if db_tags:
            target["tags"] = {**target["tags"], **db_tags}

        sku_parts = [part for part in [db_class, engine] if part]
        if multi_az:
            sku_parts.append("Multi-AZ")
        target["instance_type"] = db_class
        target["sku"] = " / ".join(sku_parts) if sku_parts else db_class
        target["state"] = db.get("DBInstanceStatus")
        if az:
            target["location"] = az
        target["details"].update(
            {
                "db_instance_class": db_class,
                "engine": engine,
                "engine_version": db.get("EngineVersion"),
                "allocated_storage_gib": storage,
                "storage_type": storage_type,
                "multi_az": multi_az,
                "publicly_accessible": db.get("PubliclyAccessible"),
            }
        )


def _enrich_lambda_functions(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    if len(items) > MAX_PER_RESOURCE_DESCRIBE_CALLS:
        warnings.append(
            f"Enriching first {MAX_PER_RESOURCE_DESCRIBE_CALLS} of {len(items)} Lambda functions to avoid CLI throttling."
        )
    for target in items[:MAX_PER_RESOURCE_DESCRIBE_CALLS]:
        func_name = target["resource_id"].split(":", 1)[0]
        try:
            cfg = run_aws_cli(
                ["lambda", "get-function-configuration", "--function-name", func_name],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"Lambda enrichment skipped for '{func_name}': {exc.message}")
            continue

        memory_mb = cfg.get("MemorySize")
        runtime = cfg.get("Runtime") or cfg.get("PackageType")
        archs = cfg.get("Architectures") or []
        arch_label = archs[0] if archs else None
        target["instance_type"] = f"{memory_mb}MB" if memory_mb else None
        sku_parts = [
            f"{memory_mb}MB" if memory_mb else None,
            runtime,
            arch_label,
        ]
        target["sku"] = " / ".join(p for p in sku_parts if p) or None
        target["state"] = cfg.get("State")
        target["details"].update(
            {
                "runtime": runtime,
                "memory_mb": memory_mb,
                "timeout_seconds": cfg.get("Timeout"),
                "architectures": archs,
                "code_size_bytes": cfg.get("CodeSize"),
            }
        )


def _enrich_dynamodb_tables(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    for target in items[:MAX_PER_RESOURCE_DESCRIBE_CALLS]:
        table_name = target["resource_id"]
        try:
            resp = run_aws_cli(
                ["dynamodb", "describe-table", "--table-name", table_name],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"DynamoDB enrichment skipped for '{table_name}': {exc.message}")
            continue

        table = resp.get("Table") or {}
        billing_mode = (table.get("BillingModeSummary") or {}).get("BillingMode") or "PROVISIONED"
        throughput = table.get("ProvisionedThroughput") or {}
        rcu = throughput.get("ReadCapacityUnits")
        wcu = throughput.get("WriteCapacityUnits")
        sku = (
            f"PROVISIONED ({rcu} RCU / {wcu} WCU)"
            if billing_mode == "PROVISIONED" and rcu is not None and wcu is not None
            else billing_mode
        )
        target["instance_type"] = billing_mode
        target["sku"] = sku
        target["state"] = table.get("TableStatus")
        target["details"].update(
            {
                "billing_mode": billing_mode,
                "read_capacity_units": rcu,
                "write_capacity_units": wcu,
                "table_size_bytes": table.get("TableSizeBytes"),
                "item_count": table.get("ItemCount"),
            }
        )


def _enrich_elbv2(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    by_arn = {item["arn"]: item for item in items}
    arns = list(by_arn.keys())
    for batch in _chunked(arns, 20):
        try:
            resp = run_aws_cli(
                ["elbv2", "describe-load-balancers", "--load-balancer-arns", *batch],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"ELBv2 enrichment skipped ({region or 'default'}): {exc.message}")
            return

        for lb in resp.get("LoadBalancers", []):
            target = by_arn.get(lb.get("LoadBalancerArn", ""))
            if not target:
                continue
            lb_type = lb.get("Type")
            scheme = lb.get("Scheme")
            target["instance_type"] = lb_type
            target["sku"] = f"{lb_type} ({scheme})" if lb_type and scheme else lb_type
            target["state"] = (lb.get("State") or {}).get("Code")
            target["details"].update(
                {
                    "type": lb_type,
                    "scheme": scheme,
                    "dns_name": lb.get("DNSName"),
                    "vpc_id": lb.get("VpcId"),
                }
            )


def _enrich_elasticache(
    items: list[dict[str, Any]],
    region: Optional[str],
    warnings: list[str],
) -> None:
    for target in items[:MAX_PER_RESOURCE_DESCRIBE_CALLS]:
        cluster_id = target["resource_id"]
        try:
            resp = run_aws_cli(
                ["elasticache", "describe-cache-clusters", "--cache-cluster-id", cluster_id],
                region=region,
            )
        except AWSCLIBaseError as exc:
            warnings.append(f"ElastiCache enrichment skipped for '{cluster_id}': {exc.message}")
            continue

        clusters = resp.get("CacheClusters") or []
        if not clusters:
            continue
        cluster = clusters[0]
        node_type = cluster.get("CacheNodeType")
        engine = cluster.get("Engine")
        num_nodes = cluster.get("NumCacheNodes")
        target["instance_type"] = node_type
        target["sku"] = (
            f"{node_type} x{num_nodes} ({engine})"
            if node_type and num_nodes and engine
            else node_type
        )
        target["state"] = cluster.get("CacheClusterStatus")
        target["details"].update(
            {
                "cache_node_type": node_type,
                "engine": engine,
                "engine_version": cluster.get("EngineVersion"),
                "num_cache_nodes": num_nodes,
            }
        )


def _enrich_s3_buckets(items: list[dict[str, Any]], warnings: list[str]) -> None:
    for target in items[:MAX_PER_RESOURCE_DESCRIBE_CALLS]:
        bucket_name = target["resource_id"]
        try:
            resp = run_aws_cli(
                ["s3api", "get-bucket-location", "--bucket", bucket_name],
            )
            # AWS returns null LocationConstraint for us-east-1
            loc = resp.get("LocationConstraint") or "us-east-1"
            target["region"] = loc
            target["location"] = loc
            target["sku"] = "S3 Standard / Bucket"
        except AWSCLIBaseError as exc:
            target["sku"] = "S3 Bucket"
            warnings.append(f"S3 bucket location lookup skipped for '{bucket_name}': {exc.message}")


def _enrich_resources(
    resources: list[dict[str, Any]],
    default_region: Optional[str],
    warnings: list[str],
) -> None:
    """Dispatch service-specific AWS CLI calls to enrich instance types, SKUs, and states."""
    buckets: dict[tuple[str, str, Optional[str]], list[dict[str, Any]]] = defaultdict(list)

    for res in resources:
        res_region = res["region"]
        cli_region = (
            res_region
            if res_region and res_region not in ("global", "default")
            else default_region
        )
        buckets[(res["service"].lower(), res["sub_type"].lower(), cli_region)].append(res)

    for (service, sub_type, cli_region), group in buckets.items():
        if service == "ec2" and sub_type == "instance":
            _enrich_ec2_instances(group, cli_region, warnings)
        elif service == "ec2" and sub_type == "volume":
            _enrich_ebs_volumes(group, cli_region, warnings)
        elif service == "ec2" and sub_type == "natgateway":
            _enrich_nat_gateways(group, cli_region, warnings)
        elif service == "rds" and sub_type == "db":
            _enrich_rds_instances(group, cli_region, warnings)
        elif service == "lambda" and sub_type == "function":
            _enrich_lambda_functions(group, cli_region, warnings)
        elif service == "dynamodb" and sub_type == "table":
            _enrich_dynamodb_tables(group, cli_region, warnings)
        elif service == "elasticloadbalancing" and sub_type == "loadbalancer":
            _enrich_elbv2(group, cli_region, warnings)
        elif service == "elasticache" and sub_type == "cluster":
            _enrich_elasticache(group, cli_region, warnings)
        elif service == "s3" and sub_type == "bucket":
            _enrich_s3_buckets(group, warnings)


# ---------------------------------------------------------------------------
# Cost Explorer Enrichment (aws ce get-cost-and-usage)
# ---------------------------------------------------------------------------


def fetch_cost_summary(
    *,
    filter_type: str,
    filter_params: dict[str, Any],
    lookback_days: int = 30,
) -> dict[str, Any]:
    """
    Query AWS Cost Explorer (`aws ce get-cost-and-usage`) for the last `lookback_days`.
    Always uses `--region us-east-1` since Cost Explorer is hosted globally in us-east-1.
    Fails gracefully with a warning if Cost Explorer is not enabled or IAM denies access.
    """
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=max(1, lookback_days))
    period_start = start_dt.isoformat()
    period_end = end_dt.isoformat()

    base_args = [
        "ce",
        "get-cost-and-usage",
        "--time-period",
        f"Start={period_start},End={period_end}",
        "--granularity",
        "MONTHLY",
        "--metrics",
        "UnblendedCost",
        "--group-by",
        "Type=DIMENSION,Key=SERVICE",
    ]

    ce_filter_json: Optional[str] = None
    filter_label: Optional[str] = None

    if filter_type == "tag" and filter_params.get("key") and filter_params.get("value"):
        ce_filter_json = json.dumps(
            {
                "Tags": {
                    "Key": filter_params["key"],
                    "Values": [filter_params["value"]],
                }
            }
        )
        filter_label = f"tag:{filter_params['key']}={filter_params['value']}"
    elif filter_type == "stack" and filter_params.get("stack_name"):
        ce_filter_json = json.dumps(
            {
                "Tags": {
                    "Key": CFN_STACK_TAG_KEY,
                    "Values": [filter_params["stack_name"]],
                }
            }
        )
        filter_label = f"stack:{filter_params['stack_name']}"

    args = [*base_args, "--filter", ce_filter_json] if ce_filter_json else base_args

    try:
        resp = run_aws_cli(args, region="us-east-1", timeout=30)
    except AWSCLIBaseError as exc:
        return {
            "period_start": period_start,
            "period_end": period_end,
            "total_cost": 0.0,
            "currency": "USD",
            "by_service": [],
            "filter_applied": filter_label,
            "warning": f"Cost Explorer lookup unavailable: {exc.message}",
        }

    service_totals: dict[str, float] = defaultdict(float)
    currency = "USD"

    for period in resp.get("ResultsByTime", []):
        for group in period.get("Groups", []):
            keys = group.get("Keys") or []
            service_name = keys[0] if keys else "Other"
            metric = (group.get("Metrics") or {}).get("UnblendedCost") or {}
            try:
                amount = float(metric.get("Amount", "0") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            if metric.get("Unit"):
                currency = metric["Unit"]
            service_totals[service_name] += amount

    by_service = [
        {"service": svc, "amount": round(amt, 4), "unit": currency}
        for svc, amt in sorted(service_totals.items(), key=lambda kv: kv[1], reverse=True)
        if abs(amt) > 0.0
    ]
    total_cost = round(sum(service_totals.values()), 4)

    warning = None
    if filter_label and total_cost == 0.0:
        warning = (
            f"No Cost Explorer spend matched '{filter_label}'. "
            "Ensure the tag key is activated as a Cost Allocation Tag in AWS Billing."
        )

    return {
        "period_start": period_start,
        "period_end": period_end,
        "total_cost": total_cost,
        "currency": currency,
        "by_service": by_service,
        "filter_applied": filter_label,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Main Analyze Entrypoint
# ---------------------------------------------------------------------------


def analyze_resource_group(
    resource_group: Optional[str] = None,
    *,
    tag_key: Optional[str] = None,
    tag_value: Optional[str] = None,
    region: Optional[str] = None,
    include_costs: bool = True,
    role_arn: Optional[str] = None,
    external_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Scan and analyze all AWS resources in the requested resource group or filter.

    Step flow:
      1. Validate filter input.
      2. If `role_arn` is provided, assume the IAM Role via `aws sts assume-role`.
      3. Verify AWS CLI + credentials via `aws sts get-caller-identity`.
      4. Fetch group members via `aws resource-groups list-group-resources` or
         `aws resourcegroupstaggingapi get-resources`.
      5. Enrich resources with service-specific AWS CLI commands (`ec2`, `rds`,
         `lambda`, `dynamodb`, `elbv2`, `elasticache`, `s3api`).
      6. Optionally attach 30-day AWS Cost Explorer summary (`aws ce get-cost-and-usage`).
    """
    filter_type, filter_params = _parse_group_filter(resource_group or "", tag_key, tag_value)
    if resource_group and resource_group.strip():
        display_group = resource_group.strip()
    elif filter_type == "tag":
        key = filter_params["key"]
        val = filter_params.get("value")
        display_group = f"tag:{key}={val}" if val is not None else f"tag:{key}"
    else:
        display_group = ""

    has_role = bool(role_arn and role_arn.strip())
    if not has_role:
        if is_demo_mode():
            active_region = get_configured_region(region) or "us-east-1"
            return _demo_scan_result(display_group, filter_type, active_region, include_costs)
        raise InvalidFilterError(
            "An AWS IAM Role ARN is required to scan AWS resources.",
            hint="Enter your IAM Role ARN (e.g. arn:aws:iam::123456789012:role/CostDetectiveReadOnlyRole).",
        )

    with _assumed_role_context(role_arn, external_id, region):
        return _analyze_resource_group_impl(
            display_group=display_group,
            filter_type=filter_type,
            filter_params=filter_params,
            region=region,
            include_costs=include_costs,
        )


def _analyze_resource_group_impl(
    *,
    display_group: str,
    filter_type: str,
    filter_params: dict[str, Any],
    region: Optional[str] = None,
    include_costs: bool = True,
) -> dict[str, Any]:
    identity = verify_aws_identity(region=region)
    active_region = get_configured_region(region)
    display_region = active_region or "default"

    warnings: list[str] = []

    raw_resources = _fetch_base_resources(
        filter_type,
        filter_params,
        active_region,
        display_region,
        warnings,
        account_id=identity["Account"],
    )

    # For full-account scans, EC2/EBS/NAT/RDS/ELBv2/ElastiCache are already enriched;
    # still enrich Lambda/DynamoDB/S3 discovered via Tagging API.
    if raw_resources:
        to_enrich = (
            [r for r in raw_resources if r["service"] not in ("ec2", "rds", "elasticloadbalancing", "elasticache")]
            if filter_type == "all"
            else raw_resources
        )
        if to_enrich:
            _enrich_resources(to_enrich, active_region, warnings)

    # Strip internal helper field `sub_type` before returning structured resources
    structured_resources: list[dict[str, Any]] = []
    service_counts: Counter[str] = Counter()

    for res in raw_resources:
        service_counts[res["service"]] += 1
        structured_resources.append(
            {
                "arn": res["arn"],
                "resource_type": res["resource_type"],
                "service": res["service"],
                "name": res["name"],
                "resource_id": res["resource_id"],
                "region": res["region"],
                "location": res["location"],
                "instance_type": res["instance_type"],
                "sku": res["sku"],
                "state": res["state"],
                "tags": res["tags"],
                "details": res["details"],
            }
        )

    cost_summary = (
        fetch_cost_summary(filter_type=filter_type, filter_params=filter_params)
        if include_costs
        else None
    )
    if cost_summary and cost_summary.get("warning"):
        warnings.append(cost_summary["warning"])

    return {
        "resource_group": display_group,
        "filter_type": filter_type,
        "account_id": identity["Account"],
        "region": display_region,
        "resource_count": len(structured_resources),
        "resources": structured_resources,
        "summary_by_service": dict(sorted(service_counts.items())),
        "cost_summary": cost_summary,
        "warnings": warnings,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
