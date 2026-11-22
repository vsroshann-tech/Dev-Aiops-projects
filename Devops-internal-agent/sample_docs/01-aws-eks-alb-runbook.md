# Internal AWS EKS & ALB Troubleshooting Runbook (Sample POC Doc)

## 1. Handling ALB 502 Bad Gateway Errors
When the production Application Load Balancer (`prod-orders-tg`) returns HTTP `502`:
- Check EKS worker node memory pressure (`aws_list_compute_health`).
- Look for `Exit code 137 (OOMKilled)` in CloudWatch log group `/aws/eks/prod/orders-api`.
- Verify MySQL connection pool limits; pool exhaustion causes health check `/healthz` timeouts.

## 2. Standard Remediation Steps
1. Increase `orders-api` pod memory limit in Helm values (`512Mi -> 1024Mi`) via PR + GitHub Actions.
2. Confirm `RDS` Multi-AZ failover status before restarting pods.
3. Never open `0.0.0.0/0` on EC2/EKS security groups during debugging; use AWS SSM Session Manager.
