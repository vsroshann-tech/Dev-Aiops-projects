---
name: database-health-check
description: Safely diagnose PostgreSQL and MySQL slow queries, lock contention, connection exhaustion, and replication lag using read-only SQL.
triggers: [postgres, postgresql, mysql, database, db, slow query, lock, deadlock, connection, pg_stat_activity, innodb, replication]
allowed_mcp_tools: [db_get_active_connections, db_get_slow_queries, db_run_readonly_sql]
---
# Database Health & Troubleshooting Skill (PostgreSQL & MySQL)

## Troubleshooting Procedure
1. Inspect active sessions and blocked/waiting queries using `db_get_active_connections`.
2. Review top slow queries via `db_get_slow_queries`.
3. If additional read-only inspection is needed (`EXPLAIN`, `SELECT`, `SHOW`), run `db_run_readonly_sql`.
4. Strictly enforce compliance: **no `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, or `PG_TERMINATE_BACKEND`**. Recommend remediation commands to the engineer to execute via approved change management.
