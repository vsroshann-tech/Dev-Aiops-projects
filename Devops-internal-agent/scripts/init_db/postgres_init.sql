-- Local Ubuntu POC Postgres initialization with strict read-only role
CREATE USER devops_ro WITH PASSWORD 'devops_ro_pass';
CREATE DATABASE retool_internal;

GRANT CONNECT ON DATABASE app_prod_ro TO devops_ro;
GRANT USAGE ON SCHEMA public TO devops_ro;

CREATE TABLE IF NOT EXISTS deployment_events (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(100) NOT NULL,
    environment VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO deployment_events (service_name, environment, status) VALUES
    ('orders-api', 'prod', 'degraded'),
    ('billing-worker', 'prod', 'healthy'),
    ('auth-gateway', 'staging', 'healthy');

GRANT SELECT ON ALL TABLES IN SCHEMA public TO devops_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO devops_ro;
