-- Idempotent DB role + database setup. Run by `make db-setup`.
-- Expects psql variables: db_user, db_password, db_name

-- 1) Ensure the role exists (create it, or reset its password if it already does)

SELECT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'db_user'
) AS role_exists \gset

\if :role_exists
    ALTER ROLE :"db_user" WITH LOGIN PASSWORD :'db_password';
\else
    CREATE ROLE :"db_user" LOGIN PASSWORD :'db_password';
\endif


-- 2) Ensure the database exists

SELECT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = :'db_name'
) AS db_exists \gset

\if :db_exists
    -- already exists; nothing to do
\else
    CREATE DATABASE :"db_name" OWNER :"db_user";
\endif


-- 3) Final privileges/ownership (idempotent enough for dev)

GRANT ALL PRIVILEGES ON DATABASE :"db_name" TO :"db_user";
ALTER DATABASE :"db_name" OWNER TO :"db_user";
