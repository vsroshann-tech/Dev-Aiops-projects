# Internal GitHub Actions CI/CD Runbook (Sample POC Doc)

## 1. OIDC & Amazon ECR Push Failures
Our GitHub Actions pipelines authenticate to AWS using short-lived OIDC tokens (`aws-actions/configure-aws-credentials@v4`) instead of static `AKIA` keys.
If `deploy-eks-prod` fails with `ecr:InitiateLayerUpload` AccessDenied:
- Verify the IAM role `arn:aws:iam::<ACCOUNT_ID>:role/github-actions-deploy` includes `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, and `ecr:CompleteLayerUpload` for the target repository.
- Confirm the OIDC trust policy restricts `sub` to `repo:my-company/*:ref:refs/heads/main`.

## 2. Database Migration CI Failures
If `db-migration-verify` fails with `LockNotAvailable` on Postgres:
- Do not force-rerun migrations blindly.
- Check `pg_stat_activity` for long-running reporting queries holding `AccessShareLock`.
