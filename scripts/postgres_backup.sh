#!/usr/bin/env bash
set -euo pipefail

# Intended to run from the VDS host against the localhost-only Postgres binding.
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:?POSTGRES_DB is required}"
POSTGRES_USER="${POSTGRES_USER:?POSTGRES_USER is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

mkdir -p "${BACKUP_DIR}"

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
backup_path="${BACKUP_DIR}/${POSTGRES_DB}_${timestamp}.dump"

pg_dump \
  --format=custom \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --file="${backup_path}"

echo "PostgreSQL backup created: ${backup_path}"
