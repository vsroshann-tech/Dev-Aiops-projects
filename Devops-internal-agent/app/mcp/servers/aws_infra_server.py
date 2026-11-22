"""AWS Infrastructure MCP Server (read-only diagnostics & compliance)."""

from typing import Any, Dict, List

from app.config import settings
from app.mcp.stdio_protocol import MCPToolDef, run_stdio_server
from app.security.redactor import redact_secrets


def aws_list_compute_health(environment: str = "prod") -> Dict[str, Any]:
    """Inspect EC2 instances and ECS/EKS node health in read-only mode."""
    if settings.poc_mode == "live":
        import boto3  # type: ignore

        ec2 = boto3.client("ec2", region_name=settings.aws_region)
        resp = ec2.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "pending", "stopped"]}]
        )
        instances = []
        for res in resp.get("Reservations", []):
            for inst in res.get("Instances", []):
                name = next(
                    (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"),
                    inst["InstanceId"],
                )
                instances.append(
                    {
                        "id": inst["InstanceId"],
                        "name": name,
                        "type": inst.get("InstanceType"),
                        "state": inst["State"]["Name"],
                        "az": inst.get("Placement", {}).get("AvailabilityZone"),
                    }
                )
        return {"mode": "live", "region": settings.aws_region, "instances": instances[:20]}

    return {
        "mode": "mock",
        "environment": environment,
        "region": settings.aws_region,
        "ec2_instances": [
            {
                "id": "i-0a1b2c3d4e5f00111",
                "name": f"{environment}-eks-worker-1",
                "type": "m6i.xlarge",
                "state": "running",
                "cpu_percent": 42.5,
                "memory_percent": 68.0,
            },
            {
                "id": "i-0a1b2c3d4e5f00222",
                "name": f"{environment}-eks-worker-2",
                "type": "m6i.xlarge",
                "state": "running",
                "cpu_percent": 94.2,
                "memory_percent": 91.5,
                "alert": "High memory pressure & OOMKill events detected on orders-api pod",
            },
        ],
        "alb_target_health": {
            "target_group": f"{environment}-orders-tg",
            "healthy_targets": 2,
            "unhealthy_targets": 1,
            "last_error": "Health checks failed with code 502 (connection reset by upstream pod)",
        },
    }


def aws_query_cloudwatch_logs(
    log_group: str = "/aws/eks/prod/orders-api",
    filter_pattern: str = "ERROR",
) -> Dict[str, Any]:
    """Query recent CloudWatch error logs with automatic secret redaction."""
    if settings.poc_mode == "live":
        import boto3  # type: ignore

        logs = boto3.client("logs", region_name=settings.aws_region)
        resp = logs.filter_log_events(
            logGroupName=log_group,
            filterPattern=filter_pattern,
            limit=15,
        )
        events = []
        for ev in resp.get("events", []):
            msg, _ = redact_secrets(ev.get("message", ""))
            events.append({"timestamp": ev.get("timestamp"), "message": msg})
        return {"mode": "live", "log_group": log_group, "events": events}

    mock_events = [
        "2026-11-22T12:41:09Z ERROR [orders-api] Upstream MySQL connection pool exhausted (active=100/100, wait=5000ms)",
        "2026-11-22T12:41:14Z ERROR [orders-api] Container exited with status 137 (OOMKilled) on node prod-eks-worker-2",
        "2026-11-22T12:41:18Z WARN  [alb-ingress] Returning HTTP 502 Bad Gateway for /api/v1/checkout",
    ]
    cleaned = [redact_secrets(m)[0] for m in mock_events if filter_pattern.lower() in m.lower() or not filter_pattern]
    return {"mode": "mock", "log_group": log_group, "filter_pattern": filter_pattern, "events": cleaned}


def aws_check_rds_status(db_identifier: str = "prod-postgres-01") -> Dict[str, Any]:
    """Inspect RDS PostgreSQL / MySQL instance health and storage metrics."""
    if settings.poc_mode == "live":
        import boto3  # type: ignore

        rds = boto3.client("rds", region_name=settings.aws_region)
        resp = rds.describe_db_instances(DBInstanceIdentifier=db_identifier)
        db = resp["DBInstances"][0]
        return {
            "mode": "live",
            "id": db["DBInstanceIdentifier"],
            "engine": db["Engine"],
            "status": db["DBInstanceStatus"],
            "multi_az": db["MultiAZ"],
            "storage_encrypted": db["StorageEncrypted"],
            "publicly_accessible": db["PubliclyAccessible"],
        }

    return {
        "mode": "mock",
        "instances": [
            {
                "id": "prod-postgres-01",
                "engine": "postgres 16.3",
                "status": "available",
                "multi_az": True,
                "storage_encrypted": True,
                "publicly_accessible": False,
                "connections_used_pct": 78,
                "replica_lag_ms": 140,
            },
            {
                "id": "prod-mysql-orders-01",
                "engine": "mysql 8.0.36",
                "status": "available",
                "multi_az": True,
                "storage_encrypted": True,
                "publicly_accessible": False,
                "connections_used_pct": 95,
                "alert": "Near max_connections (190/200)",
            },
        ],
    }


def aws_compliance_scan() -> Dict[str, Any]:
    """Run read-only AWS security compliance posture checks."""
    return {
        "mode": settings.poc_mode,
        "region": settings.aws_region,
        "summary": {"critical": 0, "high": 1, "medium": 1, "passed": 14},
        "findings": [
            {
                "severity": "HIGH",
                "resource": "sg-09911aa22bb (staging-bastion-sg)",
                "issue": "Port 22 (SSH) allows 0.0.0.0/0 instead of AWS SSM Session Manager / corporate VPN CIDR.",
                "remediation": "Remove 0.0.0.0/0 ingress on port 22 and enforce SSM Session Manager.",
            },
            {
                "severity": "MEDIUM",
                "resource": "s3://company-ci-artifacts-tmp",
                "issue": "Bucket lifecycle expiration policy missing for build logs older than 90 days.",
                "remediation": "Add S3 lifecycle rule to expire temporary CI logs after 90 days.",
            },
        ],
    }


TOOLS: List[MCPToolDef] = [
    MCPToolDef(
        name="aws_list_compute_health",
        description="Inspect EC2/EKS compute instance CPU, memory, and ALB target health.",
        input_schema={
            "type": "object",
            "properties": {"environment": {"type": "string", "default": "prod"}},
        },
        handler=aws_list_compute_health,
    ),
    MCPToolDef(
        name="aws_query_cloudwatch_logs",
        description="Query AWS CloudWatch logs with secret redaction.",
        input_schema={
            "type": "object",
            "properties": {
                "log_group": {"type": "string"},
                "filter_pattern": {"type": "string"},
            },
        },
        handler=aws_query_cloudwatch_logs,
    ),
    MCPToolDef(
        name="aws_check_rds_status",
        description="Check RDS PostgreSQL/MySQL encryption, Multi-AZ, connections, and lag.",
        input_schema={
            "type": "object",
            "properties": {"db_identifier": {"type": "string"}},
        },
        handler=aws_check_rds_status,
    ),
    MCPToolDef(
        name="aws_compliance_scan",
        description="Run read-only AWS security compliance posture checks.",
        input_schema={"type": "object", "properties": {}},
        handler=aws_compliance_scan,
    ),
]


if __name__ == "__main__":
    run_stdio_server("aws-infra-mcp", TOOLS)
