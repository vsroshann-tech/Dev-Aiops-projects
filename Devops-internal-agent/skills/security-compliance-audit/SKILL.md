---
name: security-compliance-audit
description: Evaluate internal infra, CI/CD, and DB posture against company security compliance rules (encryption, secret hygiene, least privilege).
triggers: [security, compliance, audit, soc2, iso27001, iam, encryption, secret, leak, public, exposure, policy]
allowed_mcp_tools: [aws_compliance_scan, github_list_workflow_runs, db_get_active_connections]
---
# Security & Compliance Audit Skill

## Audit Procedure
1. Run `aws_compliance_scan` to detect unencrypted S3/RDS resources, public security groups, or overly permissive IAM policies.
2. Retrieve company security compliance policies from RAG to cite exact internal policy clauses.
3. Verify all diagnostic outputs pass through secret redaction before reaching the chat response or audit log.
4. Classify findings by severity (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) with remediation steps.
