#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: RESTORE_CONFIRM=yes $0 <backup-file.dump>" >&2
  exit 2
fi

if [[ "${RESTORE_CONFIRM:-}" != "yes" ]]; then
  echo "Restore refused. Set RESTORE_CONFIRM=yes to overwrite target database state." >&2
  exit 3
fi

backup_path="$1"
if [[ ! -f "${backup_path}" ]]; then
  echo "Backup file not found: ${backup_path}" >&2
  exit 4
fi

# Intended to run from the VDS host against the localhost-only Postgres binding.
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:?POSTGRES_DB is required}"
POSTGRES_USER="${POSTGRES_USER:?POSTGRES_USER is required}"

echo "WARNING: restoring ${backup_path} into ${POSTGRES_DB} on ${POSTGRES_HOST}:${POSTGRES_PORT}."
echo "Target database state will be overwritten where objects are restored."

pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  "${backup_path}"

echo "PostgreSQL restore completed from: ${backup_path}"
