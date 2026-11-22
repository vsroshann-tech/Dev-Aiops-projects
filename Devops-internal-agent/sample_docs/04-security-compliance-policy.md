# Internal Security & Compliance Policy (Sample POC Doc)

## 1. Secret & Credential Hygiene
- Static AWS IAM keys (`AKIA...`), GitHub PATs (`ghp_...`), private keys, and database passwords must NEVER be pasted into prompts or stored unredacted in knowledge bases.
- All prompts and ingested documents pass through automatic secret redaction before LLM inference.

## 2. Database & Infrastructure Guardrails
- AI Agents are restricted to **read-only** database accounts (`devops_ro`) and **read-only** SQL (`SELECT`, `SHOW`, `EXPLAIN`).
- Mutating CI/CD workflows (`github_rerun_workflow`) require `ALLOW_CI_MUTATIONS=true` plus explicit change-ticket approval.
- All RDS instances and S3 buckets containing internal data must have encryption at rest enabled and `PubliclyAccessible=false`.
- Every chat query, document upload, and MCP tool invocation is logged in `audit_logs.jsonl` for SOC2/ISO27001 review.
