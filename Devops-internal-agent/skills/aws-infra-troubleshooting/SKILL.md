---
name: aws-infra-troubleshooting
description: Troubleshoot AWS EC2, ECS, EKS, ALB 502/504 errors, RDS connectivity, and CloudWatch logs safely in read-only mode.
triggers: [aws, ec2, ecs, eks, alb, 502, 504, cloudwatch, rds, vpc, security group, s3, server, cpu, memory]
allowed_mcp_tools: [aws_list_compute_health, aws_query_cloudwatch_logs, aws_check_rds_status]
---
# AWS Infrastructure Troubleshooting Skill

## Troubleshooting Procedure
1. Check compute health (`aws_list_compute_health`) for stopped/unhealthy EC2 instances or ECS/EKS tasks.
2. Query recent error logs (`aws_query_cloudwatch_logs`) for OOMKills, target group health check failures, or IAM/VPC timeouts.
3. If database connectivity is suspected, check `aws_check_rds_status`.
4. Correlate findings with internal AWS architecture runbooks retrieved via RAG.
5. Never suggest opening `0.0.0.0/0` security groups or disabling encryption; enforce least-privilege compliance.
