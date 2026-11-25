-- CivicBridge AI - Phase 1 database bootstrap
-- Run as a PostgreSQL superuser:
--   psql -U postgres -h localhost -f backend/scripts/init_db.sql
--
-- Creates a dedicated application role with least-privilege access and the
-- civicbridge database. Safe to re-run (idempotent).

-- ---------------------------------------------------------------------------
-- 1. Application role (least privilege: can log in, cannot create databases
--    or roles, cannot bypass row level security).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'civicbridge_app') THEN
        CREATE ROLE civicbridge_app WITH
            LOGIN
            PASSWORD 'civicbridge_dev_password'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS
            CONNECTION LIMIT 50;
    END IF;
END
$$;

-- Development only: the app role needs to create tables/migrations.
-- Revoke CREATE on the public schema from every other role so the database
-- is not writable by unrelated logins.
ALTER ROLE civicbridge_app SET search_path = public;

-- ---------------------------------------------------------------------------
-- 2. Database
-- ---------------------------------------------------------------------------
SELECT 'CREATE DATABASE civicbridge OWNER civicbridge_app ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'civicbridge')
\gexec

-- ---------------------------------------------------------------------------
-- 3. Database-scoped grants (must run inside the civicbridge database)
-- ---------------------------------------------------------------------------
\connect civicbridge

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE civicbridge TO civicbridge_app;
GRANT USAGE, CREATE ON SCHEMA public TO civicbridge_app;
ALTER SCHEMA public OWNER TO civicbridge_app;

-- Extension support is validated by the health check; these two ship with a
-- stock PostgreSQL 17 Windows install. pg_trgm powers fuzzy duplicate text
-- matching, uuid-ossp is created for portability even though PostgreSQL 17
-- provides gen_random_uuid() natively.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Let the app role use the extensions' objects.
GRANT USAGE ON SCHEMA public TO civicbridge_app;

SELECT version() AS server_version,
       current_setting('server_version_num') AS version_num;
SELECT datname AS database, pg_get_userbyid(datdba) AS owner
FROM pg_database WHERE datname = 'civicbridge';
SELECT extname AS extension FROM pg_extension ORDER BY extname;
