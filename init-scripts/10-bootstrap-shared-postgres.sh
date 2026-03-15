#!/bin/sh
set -eu

: "${POSTGRES_HOST:=}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=postgres}"
: "${POSTGRES_USER:=postgres}"
: "${DATABASE_USER:=neomagi}"
: "${DATABASE_PASSWORD:=neomagi}"
: "${DATABASE_NAME:=neomagi}"
: "${LANGFUSE_POSTGRES_USER:=langfuse}"
: "${LANGFUSE_POSTGRES_PASSWORD:=langfuse}"
: "${LANGFUSE_POSTGRES_DB:=langfuse}"

psql_admin() {
  if [ -n "${POSTGRES_HOST}" ]; then
    psql \
      -v ON_ERROR_STOP=1 \
      -h "${POSTGRES_HOST}" \
      -p "${POSTGRES_PORT}" \
      -U "${POSTGRES_USER}" \
      -d "${POSTGRES_DB}" \
      -v neomagi_user="${DATABASE_USER}" \
      -v neomagi_password="${DATABASE_PASSWORD}" \
      -v neomagi_db="${DATABASE_NAME}" \
      -v langfuse_user="${LANGFUSE_POSTGRES_USER}" \
      -v langfuse_password="${LANGFUSE_POSTGRES_PASSWORD}" \
      -v langfuse_db="${LANGFUSE_POSTGRES_DB}" \
      "$@"
    return
  fi

  psql \
    -v ON_ERROR_STOP=1 \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -v neomagi_user="${DATABASE_USER}" \
    -v neomagi_password="${DATABASE_PASSWORD}" \
    -v neomagi_db="${DATABASE_NAME}" \
    -v langfuse_user="${LANGFUSE_POSTGRES_USER}" \
    -v langfuse_password="${LANGFUSE_POSTGRES_PASSWORD}" \
    -v langfuse_db="${LANGFUSE_POSTGRES_DB}" \
    "$@"
}

psql_admin <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'neomagi_user', :'neomagi_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'neomagi_user')\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'neomagi_user', :'neomagi_password')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'neomagi_db', :'neomagi_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'neomagi_db')\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'neomagi_db', :'neomagi_user')
WHERE EXISTS (SELECT 1 FROM pg_database WHERE datname = :'neomagi_db')\gexec

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'langfuse_user', :'langfuse_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'langfuse_user')\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'langfuse_user', :'langfuse_password')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'langfuse_db', :'langfuse_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'langfuse_db')\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'langfuse_db', :'langfuse_user')
WHERE EXISTS (SELECT 1 FROM pg_database WHERE datname = :'langfuse_db')\gexec
SQL

if [ -n "${POSTGRES_HOST}" ]; then
  psql \
    -v ON_ERROR_STOP=1 \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d "${DATABASE_NAME}" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;
SQL
  exit 0
fi

psql \
  -v ON_ERROR_STOP=1 \
  -U "${POSTGRES_USER}" \
  -d "${DATABASE_NAME}" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;
SQL
