#!/usr/bin/env bash
# The whole day in one command: pull analytics, produce + schedule the next day's Shorts,
# write the review page, then (optionally) stop the pod so billing stops.
#
#   bash scripts/daily_run.sh                # produces tomorrow's videos
#   bash scripts/daily_run.sh 2026-10-05     # a specific publish date
#   STOP_POD_WHEN_DONE=1 bash scripts/daily_run.sh
set -uo pipefail

WS="${WORKSPACE:-/workspace}"
cd "$(dirname "$0")/.."
DAY="${1:-tomorrow}"
export HF_HOME="${HF_HOME:-$WS/hf}"
# shellcheck disable=SC1091
[ -f "$WS/venvs/main/bin/activate" ] && source "$WS/venvs/main/bin/activate"
if [ -f "$WS/secrets.env" ]; then set -a; . "$WS/secrets.env"; set +a; fi

mkdir -p "$WS/logs"
LOG="$WS/logs/run-$(date +%F-%H%M).log"
{
  echo "=== $(date -Is) daily run for $DAY"
  shorts sync || echo "metrics sync failed (continuing)"
  shorts run --date "$DAY"
  status=$?
  shorts review --date "$DAY"
  shorts status --date "$DAY"
  echo "=== finished $(date -Is) exit=$status"
} 2>&1 | tee -a "$LOG"

if [ "${STOP_POD_WHEN_DONE:-0}" = "1" ] && [ -n "${RUNPOD_POD_ID:-}" ] && command -v runpodctl >/dev/null; then
  echo "stopping pod $RUNPOD_POD_ID"
  runpodctl stop pod "$RUNPOD_POD_ID"
fi
