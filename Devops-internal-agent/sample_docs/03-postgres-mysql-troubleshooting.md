# Internal PostgreSQL & MySQL Runbook (Sample POC Doc)

## 1. PostgreSQL Lock Contention
- Always run read-only diagnostics against `pg_stat_activity` first.
- Long-running `reporting_ro` analytical queries (>300s) can block Alembic `ALTER TABLE` migrations waiting on `AccessExclusiveLock`.
- Mitigation: Route heavy analytics to the read replica (`prod-postgres-ro`) and set `lock_timeout = '3s'` in migrations.

## 2. MySQL High Connection Utilization
- Standard max connections on `prod-mysql-orders-01` is `200`. Alert threshold is `85%`.
- Leading-wildcard queries (`LIKE '%@corp.com'`) bypass B-Tree indexes and hold connections open.
- Verify application HikariCP / SQLAlchemy pool `max_overflow` and `pool_recycle` settings.
