from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

# Load .env from backend/ or project root if present
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")

DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 60
VALID_SEVERITIES = {"high", "medium", "low"}


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class AIAnalyzerBaseError(Exception):
    """Base exception for OpenAI cost analysis failures with HTTP-friendly metadata."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AI_ANALYZER_ERROR",
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


class OpenAIKeyMissingError(AIAnalyzerBaseError):
    def __init__(self) -> None:
        super().__init__(
            "OPENAI_API_KEY environment variable is not set.",
            code="OPENAI_API_KEY_MISSING",
            status_code=503,
            hint=(
                "Copy backend/.env.example to backend/.env and set OPENAI_API_KEY, "
                "or export OPENAI_API_KEY in your shell before starting the server."
            ),
        )


class OpenAIAuthError(AIAnalyzerBaseError):
    def __init__(self, details: Optional[str] = None) -> None:
        super().__init__(
            "OpenAI API authentication failed. The API key may be invalid or revoked.",
            code="OPENAI_AUTH_ERROR",
            status_code=401,
            hint="Verify your OPENAI_API_KEY in backend/.env or environment variables.",
            details=details,
        )


class OpenAIRateLimitError(AIAnalyzerBaseError):
    def __init__(self, details: Optional[str] = None) -> None:
        super().__init__(
            "OpenAI API rate limit or quota exceeded.",
            code="OPENAI_RATE_LIMIT",
            status_code=429,
            hint="Wait briefly and retry, or check your OpenAI billing quota and rate limits.",
            details=details,
        )


# ---------------------------------------------------------------------------
# Prompt Construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert AWS Cloud FinOps Engineer and Solutions Architect ("AI Cloud Cost Detective").
Your job is to inspect a JSON inventory of AWS resources (and optional recent Cost Explorer data)
from a user's AWS resource group and produce a concrete, accurate, actionable cost optimization audit.

Analyze the resources thoroughly for:
1. Over-provisioning: oversized EC2 instances, RDS DB classes, DynamoDB provisioned capacity,
   or Lambda memory allocations relative to typical workloads and environment tags (e.g., dev/staging/test).
2. Unused or idle resources: unattached EBS volumes (`state: "available"` or empty `attached_instances`),
   stopped EC2 instances holding paid EBS storage, idle/unused NAT Gateways, unassociated Elastic IPs,
   unused Load Balancers, or forgotten dev/test RDS instances.
3. Misconfigurations impacting cost: Multi-AZ enabled on non-production RDS, overly generous IOPS/throughput,
   missing lifecycle policies on S3 buckets, missing cost-allocation tags (e.g., `Environment`, `Owner`, `CostCenter`).
4. Wrong pricing tiers / legacy instance families: previous-generation instances (t2, m4, m5, c4, r4, db.t2, db.m4)
   that should move to newer generation or Graviton (t3/t4g, m6i/m7g, c7g, r7g), gp2 EBS volumes that should
   migrate to gp3, or DynamoDB PROVISIONED vs PAY_PER_REQUEST mismatches.
5. Cost optimization opportunities: Right-Sizing, Compute Savings Plans / EC2 Instance Savings Plans /
   RDS Reserved Instances, gp3 migration, S3 Intelligent-Tiering / Lifecycle rules, and scheduling
   non-production resources to stop outside business hours.

Rules:
- Base every finding strictly on the provided resource inventory—never fabricate resource IDs or ARNs.
- Every `fix_commands` entry must be a valid, realistic AWS CLI v2 command targeting the actual
  resource ID/name and `--region` from the input.
- Clearly note when a command is destructive or causes downtime (e.g., modifying/stopping/deleting resources).
- Provide conservative, realistic monthly USD savings estimates based on standard AWS on-demand pricing.
- Sort issues by severity: "high" first, then "medium", then "low".
- Return ONLY a single valid JSON object matching the exact schema requested—no markdown fences or prose outside JSON.
"""


HIGH_COST_RESOURCE_TYPES = (
    "AWS::EC2::Instance",
    "AWS::EC2::Volume",
    "AWS::EC2::NatGateway",
    "AWS::EC2::EIP",
    "AWS::RDS::DBInstance",
    "AWS::RDS::DBCluster",
    "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::ElastiCache::CacheCluster",
    "AWS::DynamoDB::Table",
    "AWS::Lambda::Function",
    "AWS::S3::Bucket",
    "AWS::ECS::Service",
    "AWS::EKS::Cluster",
)


def _compact_resources_for_prompt(resources: list[dict[str, Any]], max_items: int = 45) -> list[dict[str, Any]]:
    """Prioritize billable AWS resources and trim verbose fields to stay well within LLM token limits."""
    priority = {rtype: idx for idx, rtype in enumerate(HIGH_COST_RESOURCE_TYPES)}
    ordered = sorted(
        resources,
        key=lambda r: (
            priority.get(str(r.get("resource_type", "")), 999),
            0 if r.get("sku") or r.get("instance_type") else 1,
            str(r.get("service", "")),
        ),
    )
    compact: list[dict[str, Any]] = []
    for r in ordered[:max_items]:
        entry: dict[str, Any] = {
            "arn": r.get("arn"),
            "resource_type": r.get("resource_type"),
            "service": r.get("service"),
            "name": r.get("name"),
            "resource_id": r.get("resource_id"),
            "region": r.get("region"),
            "location": r.get("location"),
        }
        if r.get("instance_type"):
            entry["instance_type"] = r["instance_type"]
        if r.get("sku"):
            entry["sku"] = r["sku"]
        if r.get("state"):
            entry["state"] = r["state"]
        if r.get("tags"):
            entry["tags"] = r["tags"]
        if r.get("details"):
            entry["details"] = r["details"]
        compact.append(entry)
    return compact


def _build_user_prompt(
    resources: list[dict[str, Any]],
    *,
    resource_group: Optional[str] = None,
    region: Optional[str] = None,
    cost_summary: Optional[dict[str, Any]] = None,
) -> str:
    compact_resources = _compact_resources_for_prompt(resources)
    context: dict[str, Any] = {
        "resource_group": resource_group or "unknown",
        "region": region or "default",
        "total_resources_scanned": len(resources),
        "resources_included_in_prompt": len(compact_resources),
        "resources": compact_resources,
    }
    if cost_summary:
        context["cost_summary_last_30_days"] = cost_summary

    inventory_json = (
        json.dumps(context, separators=(",", ":"), default=str)
        if len(compact_resources) > 20
        else json.dumps(context, indent=2, default=str)
    )

    return (
        "Analyze the following AWS resource inventory for cost waste, over-provisioning, "
        "idle resources, misconfigurations, pricing-tier inefficiencies, and optimization opportunities.\n\n"
        "Respond with a JSON object matching this exact schema:\n"
        "{\n"
        '  "summary": "2-4 sentence executive summary of the findings and top cost drivers.",\n'
        '  "issues": [\n'
        "    {\n"
        '      "title": "Short descriptive title of the issue",\n'
        '      "resource_name": "Human-readable name of the affected resource",\n'
        '      "resource_id": "Exact resource ID from the input",\n'
        '      "resource_arn": "Exact ARN from the input",\n'
        '      "resource_type": "e.g. AWS::EC2::Instance",\n'
        '      "region": "e.g. us-east-1",\n'
        '      "issue_type": "over-provisioned | unused | misconfigured | wrong-pricing-tier",\n'
        '      "category": "over_provisioning | idle_resource | misconfiguration | pricing_tier | storage_tiering | right_sizing | savings_plan | tagging",\n'
        '      "severity": "high | medium | low",\n'
        '      "description": "Clear explanation of the problem observed in the resource metadata.",\n'
        '      "cost_impact": "Concise explanation of the cost waste and financial impact (e.g. ~$45/month for an unattached 450 GiB gp2 volume).",\n'
        '      "recommendation": "Specific engineering/FinOps recommendation.",\n'
        '      "estimated_monthly_savings_usd": 45.0,\n'
        '      "fix_commands": [\n'
        '        "aws ec2 modify-volume --volume-id vol-xxxx --volume-type gp3 --region us-east-1"\n'
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "estimated_savings": {\n'
        '    "monthly_usd": 120.0,\n'
        '    "annual_usd": 1440.0,\n'
        '    "currency": "USD",\n'
        '    "notes": "Brief explanation of pricing assumptions."\n'
        "  },\n"
        '  "actionable_fix_commands": [\n'
        "    {\n"
        '      "command": "aws ...",\n'
        '      "description": "What this command does",\n'
        '      "resource_id": "Exact resource ID",\n'
        '      "severity": "high | medium | low",\n'
        '      "requires_downtime": false\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "AWS Inventory JSON:\n"
        f"{inventory_json}"
    )


# ---------------------------------------------------------------------------
# Response Normalization
# ---------------------------------------------------------------------------


def _normalize_severity(value: Any) -> str:
    text = str(value or "medium").strip().lower()
    return text if text in VALID_SEVERITIES else "medium"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
        return round(max(0.0, num), 2)
    except (TypeError, ValueError):
        return default


def _format_currency_summary(monthly_usd: float, annual_usd: float, currency: str = "USD") -> str:
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    return f"{symbol}{monthly_usd:,.2f}/month ({symbol}{annual_usd:,.2f}/year)"


CATEGORY_TO_ISSUE_TYPE: dict[str, str] = {
    "over_provisioning": "over-provisioned",
    "over-provisioned": "over-provisioned",
    "right_sizing": "over-provisioned",
    "idle_resource": "unused",
    "unused": "unused",
    "misconfiguration": "misconfigured",
    "misconfigured": "misconfigured",
    "tagging": "misconfigured",
    "pricing_tier": "wrong-pricing-tier",
    "wrong-pricing-tier": "wrong-pricing-tier",
    "storage_tiering": "wrong-pricing-tier",
    "savings_plan": "wrong-pricing-tier",
}


def _normalize_issue_type(raw_issue_type: Any, category: str) -> str:
    candidate = str(raw_issue_type or "").strip().lower().replace("_", "-")
    if candidate in ("over-provisioned", "unused", "misconfigured", "wrong-pricing-tier"):
        return candidate
    return CATEGORY_TO_ISSUE_TYPE.get(category, candidate or "misconfigured")


def _normalize_analysis_payload(
    raw: dict[str, Any],
    *,
    model: str,
    resources: Optional[list[dict[str, Any]]] = None,
    token_usage: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Ensure the AI output conforms cleanly to the backend's structured schema."""
    summary = str(raw.get("summary") or "Cost analysis completed.").strip()

    arn_by_id: dict[str, str] = {}
    for res in resources or []:
        rid = str(res.get("resource_id") or "").strip()
        arn = str(res.get("arn") or "").strip()
        if rid and arn:
            arn_by_id[rid] = arn

    normalized_issues: list[dict[str, Any]] = []
    raw_issues = raw.get("issues")
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            severity = _normalize_severity(item.get("severity"))
            savings = _safe_float(
                item.get("estimated_monthly_savings_usd", item.get("estimated_savings_usd"))
            )
            raw_cmds = item.get("fix_commands") or []
            fix_commands = (
                [str(c).strip() for c in raw_cmds if str(c).strip()]
                if isinstance(raw_cmds, list)
                else []
            )
            resource_id = str(item.get("resource_id") or "N/A").strip()
            resource_arn = str(item.get("resource_arn") or "").strip() or arn_by_id.get(resource_id)
            if not resource_arn and resource_id.startswith("arn:"):
                resource_arn = resource_id
            category = str(item.get("category") or "cost_optimization").strip().lower()
            issue_type = _normalize_issue_type(item.get("issue_type"), category)
            cost_impact = str(item.get("cost_impact") or "").strip()
            if not cost_impact:
                cost_impact = (
                    f"Estimated ~${savings:,.2f}/month (~${savings * 12:,.2f}/year) in avoidable AWS spend."
                    if savings > 0
                    else "Indirect or governance cost impact; review configuration to prevent runaway spend."
                )

            normalized_issues.append(
                {
                    "title": str(item.get("title") or "Cost Optimization Opportunity").strip(),
                    "resource_name": str(item.get("resource_name") or resource_id or "N/A").strip(),
                    "resource_id": resource_id,
                    "resource_arn": resource_arn,
                    "resource_type": str(item.get("resource_type") or "AWS::Resource").strip(),
                    "region": str(item.get("region") or "default").strip(),
                    "issue_type": issue_type,
                    "category": category,
                    "severity": severity,
                    "description": str(item.get("description") or "").strip(),
                    "cost_impact": cost_impact,
                    "recommendation": str(item.get("recommendation") or "").strip(),
                    "estimated_monthly_savings_usd": savings,
                    "fix_commands": fix_commands,
                }
            )

    # Sort issues high -> medium -> low
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    normalized_issues.sort(key=lambda i: (severity_rank.get(i["severity"], 9), -i["estimated_monthly_savings_usd"]))

    # Estimated savings (fall back to sum of issue estimates if omitted)
    raw_savings = raw.get("estimated_savings") if isinstance(raw.get("estimated_savings"), dict) else {}
    sum_issue_monthly = round(sum(i["estimated_monthly_savings_usd"] for i in normalized_issues), 2)
    monthly_usd = _safe_float(raw_savings.get("monthly_usd"), default=sum_issue_monthly)
    if monthly_usd == 0.0 and sum_issue_monthly > 0.0:
        monthly_usd = sum_issue_monthly
    annual_usd = _safe_float(raw_savings.get("annual_usd"), default=round(monthly_usd * 12, 2))
    if annual_usd == 0.0 and monthly_usd > 0.0:
        annual_usd = round(monthly_usd * 12, 2)
    currency = str(raw_savings.get("currency") or "USD").upper()
    notes = str(
        raw_savings.get("notes")
        or "Estimates are approximate and based on standard AWS On-Demand pricing."
    ).strip()

    # Actionable fix commands (use explicit list, or derive from issues if empty)
    normalized_commands: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    raw_fix_cmds = raw.get("actionable_fix_commands")
    if isinstance(raw_fix_cmds, list):
        for entry in raw_fix_cmds:
            if isinstance(entry, str) and entry.strip():
                cmd_str = entry.strip()
                if cmd_str not in seen_commands:
                    seen_commands.add(cmd_str)
                    normalized_commands.append(
                        {
                            "command": cmd_str,
                            "description": "Suggested AWS CLI remediation command.",
                            "resource_id": "N/A",
                            "severity": "medium",
                            "requires_downtime": False,
                        }
                    )
            elif isinstance(entry, dict) and str(entry.get("command") or "").strip():
                cmd_str = str(entry["command"]).strip()
                if cmd_str not in seen_commands:
                    seen_commands.add(cmd_str)
                    normalized_commands.append(
                        {
                            "command": cmd_str,
                            "description": str(entry.get("description") or "").strip(),
                            "resource_id": str(entry.get("resource_id") or "N/A").strip(),
                            "severity": _normalize_severity(entry.get("severity")),
                            "requires_downtime": bool(entry.get("requires_downtime", False)),
                        }
                    )

    # Ensure every command listed on individual issues is also represented
    for issue in normalized_issues:
        for cmd_str in issue["fix_commands"]:
            if cmd_str not in seen_commands:
                seen_commands.add(cmd_str)
                normalized_commands.append(
                    {
                        "command": cmd_str,
                        "description": issue["title"],
                        "resource_id": issue["resource_id"],
                        "severity": issue["severity"],
                        "requires_downtime": False,
                    }
                )

    severity_counts = {
        "high": sum(1 for i in normalized_issues if i["severity"] == "high"),
        "medium": sum(1 for i in normalized_issues if i["severity"] == "medium"),
        "low": sum(1 for i in normalized_issues if i["severity"] == "low"),
    }

    return {
        "summary": summary,
        "issue_count": len(normalized_issues),
        "severity_counts": severity_counts,
        "issues": normalized_issues,
        "estimated_savings": {
            "monthly_usd": monthly_usd,
            "annual_usd": annual_usd,
            "currency": currency,
            "display": _format_currency_summary(monthly_usd, annual_usd, currency),
            "notes": notes,
        },
        "actionable_fix_commands": normalized_commands,
        "model": model,
        "token_usage": token_usage,
    }


# ---------------------------------------------------------------------------
# Public Analyzer API
# ---------------------------------------------------------------------------


def _resolve_llm_config(model_override: Optional[str] = None) -> tuple[str, Optional[str], str]:
    """
    Resolve (api_key, base_url, model) supporting:
      1. Groq free tier (GROQ_API_KEY)
      2. Google Gemini free tier (GEMINI_API_KEY)
      3. OpenAI or any OpenAI-compatible endpoint (OPENAI_API_KEY + optional OPENAI_BASE_URL)
    """
    env_model = (os.getenv("OPENAI_MODEL") or "").strip()
    explicit_model = (model_override or "").strip()

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key and not groq_key.startswith("gsk_your-"):
        # Ignore leftover OPENAI_MODEL=gpt-* when switching to Groq
        non_openai_env_model = env_model if env_model and not env_model.startswith("gpt-") else ""
        return (
            groq_key,
            os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip(),
            explicit_model
            or os.getenv("GROQ_MODEL", "").strip()
            or non_openai_env_model
            or "openai/gpt-oss-120b",
        )

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key and not gemini_key.startswith("AIzaYour"):
        non_openai_env_model = env_model if env_model and not env_model.startswith("gpt-") else ""
        return (
            gemini_key,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            explicit_model
            or os.getenv("GEMINI_MODEL", "").strip()
            or non_openai_env_model
            or "gemini-2.5-flash",
        )

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_key or openai_key == "sk-your-openai-api-key-here":
        raise OpenAIKeyMissingError()

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    return (openai_key, base_url, explicit_model or env_model or DEFAULT_OPENAI_MODEL)


def is_openai_configured() -> bool:
    """Return True if any supported AI provider key (OpenAI, Groq, or Gemini) is configured."""
    try:
        _resolve_llm_config()
        return True
    except OpenAIKeyMissingError:
        return False


def _parse_json_content(content: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating markdown code fences or surrounding prose."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            parsed = json.loads(cleaned[start : end + 1])
        else:
            raise

    if not isinstance(parsed, dict):
        raise AIAnalyzerBaseError(
            "AI analysis payload was not a JSON object.",
            code="AI_INVALID_PAYLOAD",
            status_code=502,
        )
    return parsed


def _call_chat_completions(
    client: OpenAI,
    *,
    model: str,
    user_prompt: str,
) -> Any:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        return client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=messages,
        )
    except BadRequestError:
        # Some OpenAI-compatible providers/models reject or fail strict json_object mode;
        # retry once without response_format and parse JSON from the text output.
        return client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=messages,
        )


def analyze_resources(
    resources: list[dict[str, Any]],
    *,
    resource_group: Optional[str] = None,
    region: Optional[str] = None,
    cost_summary: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """
    Analyze scanned AWS resources using an OpenAI-compatible Chat Completions API
    (OpenAI `gpt-4o`, Groq `llama-3.3-70b-versatile`, or Gemini `gemini-2.5-flash`)
    and return a structured cost optimization report.
    """
    # Short-circuit cleanly when the resource group has no resources
    if not resources:
        target_model = (model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()
        return {
            "summary": (
                f"No AWS resources were found in '{resource_group or 'the selected group'}'. "
                "There are no cost optimization issues to report for this scope."
            ),
            "issue_count": 0,
            "severity_counts": {"high": 0, "medium": 0, "low": 0},
            "issues": [],
            "estimated_savings": {
                "monthly_usd": 0.0,
                "annual_usd": 0.0,
                "currency": "USD",
                "display": "$0.00/month ($0.00/year)",
                "notes": "No resources were discovered in the selected group.",
            },
            "actionable_fix_commands": [],
            "model": target_model,
            "token_usage": None,
        }

    api_key, base_url, target_model = _resolve_llm_config(model)
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=DEFAULT_OPENAI_TIMEOUT_SECONDS,
    )
    user_prompt = _build_user_prompt(
        resources,
        resource_group=resource_group,
        region=region,
        cost_summary=cost_summary,
    )

    try:
        response = _call_chat_completions(
            client,
            model=target_model,
            user_prompt=user_prompt,
        )
    except AuthenticationError as exc:
        raise OpenAIAuthError(details=str(exc)) from exc
    except RateLimitError as exc:
        raise OpenAIRateLimitError(details=str(exc)) from exc
    except APITimeoutError as exc:
        raise AIAnalyzerBaseError(
            "AI API request timed out while analyzing AWS resources.",
            code="OPENAI_TIMEOUT",
            status_code=504,
            hint="Retry the analysis or scan a smaller resource group.",
            details=str(exc),
        ) from exc
    except APIConnectionError as exc:
        raise AIAnalyzerBaseError(
            "Could not connect to the AI API.",
            code="OPENAI_CONNECTION_ERROR",
            status_code=502,
            hint="Check your internet connectivity and proxy/firewall settings.",
            details=str(exc),
        ) from exc
    except APIError as exc:
        raise AIAnalyzerBaseError(
            f"AI API returned an error: {exc.message if hasattr(exc, 'message') else exc}",
            code="OPENAI_API_ERROR",
            status_code=502,
            details=str(exc),
        ) from exc

    choices = getattr(response, "choices", None) or []
    content = choices[0].message.content if choices and choices[0].message else None
    if not content:
        raise AIAnalyzerBaseError(
            "AI provider returned an empty analysis response.",
            code="AI_EMPTY_RESPONSE",
            status_code=502,
        )

    try:
        parsed = _parse_json_content(content)
    except json.JSONDecodeError as exc:
        raise AIAnalyzerBaseError(
            "Failed to parse structured JSON from AI response.",
            code="AI_INVALID_JSON",
            status_code=502,
            details=f"{exc}: {content[:300]}",
        ) from exc

    usage_obj = getattr(response, "usage", None)
    token_usage = (
        {
            "prompt_tokens": int(usage_obj.prompt_tokens),
            "completion_tokens": int(usage_obj.completion_tokens),
            "total_tokens": int(usage_obj.total_tokens),
        }
        if usage_obj is not None
        else None
    )

    return _normalize_analysis_payload(
        parsed,
        model=target_model,
        resources=resources,
        token_usage=token_usage,
    )
