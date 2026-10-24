#!/usr/bin/env bash
set -euo pipefail

cd /opt/itms

BACKUP_DIR="/opt/itms/backups"
ENV_FILE="/opt/itms/.env"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/itms_db_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
chown www-data:www-data "${BACKUP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Error: ${ENV_FILE} not found." >&2
    exit 1
fi

# Extract DB credentials safely
DB_USER=$(grep -E '^POSTGRES_USER=' "${ENV_FILE}" | cut -d '=' -f2- | tr -d '"\r')
DB_PASS=$(grep -E '^POSTGRES_PASSWORD=' "${ENV_FILE}" | cut -d '=' -f2- | tr -d '"\r')
DB_NAME=$(grep -E '^POSTGRES_DB=' "${ENV_FILE}" | cut -d '=' -f2- | tr -d '"\r')
DB_HOST=$(grep -E '^POSTGRES_HOST=' "${ENV_FILE}" | cut -d '=' -f2- | tr -d '"\r')

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_NAME="${DB_NAME:-itms_db}"
DB_USER="${DB_USER:-admin}"

echo "Starting automated database backup for ${DB_NAME} at ${TIMESTAMP}..."
PGPASSWORD="${DB_PASS}" pg_dump -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" | gzip > "${BACKUP_FILE}"

chmod 600 "${BACKUP_FILE}"
chown www-data:www-data "${BACKUP_FILE}"
echo "Backup successfully created: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"

# Retention policy: Purge backups older than 14 days
find "${BACKUP_DIR}" -type f -name "itms_db_*.sql.gz" -mtime +14 -delete
echo "Retention policy applied (14 days retention)."
