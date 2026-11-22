---
name: github-actions-triage
description: Diagnose failed GitHub Actions CI/CD workflows, job errors, Docker build issues, and OIDC AWS deploy failures.
triggers: [github, actions, workflow, ci, cd, pipeline, build failed, deployment failed, runner, oidc]
allowed_mcp_tools: [github_list_workflow_runs, github_get_failed_job_logs, github_rerun_workflow]
---
# GitHub Actions CI/CD Triage Skill

## Troubleshooting Procedure
1. Identify the repository and recent workflow runs using `github_list_workflow_runs`.
2. Inspect the failing job logs with `github_get_failed_job_logs` to pinpoint the exact failing step and exit code.
3. Cross-check internal CI/CD runbooks in RAG (e.g., OIDC role assumptions, ECR push permissions, test flakiness).
4. Provide:
   - Root cause summary (exact failing step + error)
   - Concrete fix (YAML/IAM/test fix)
   - Security note: never print raw secrets or `GITHUB_TOKEN`; `github_rerun_workflow` remains disabled unless `ALLOW_CI_MUTATIONS=true` and approved.
