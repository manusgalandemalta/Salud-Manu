#!/bin/bash
# Wrapper para correr garmin_sync.py desde cron con las credenciales del .env local.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a
  source .env
  set +a
else
  echo "ERROR: no existe .env en $PROJECT_DIR" >&2
  exit 1
fi

DAYS="${1:-1}"

mkdir -p logs
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — corrida --days $DAYS ==="
  ./venv/bin/python garmin_sync.py --days "$DAYS"
  echo "=== fin OK ==="
} >> logs/sync.log 2>&1
