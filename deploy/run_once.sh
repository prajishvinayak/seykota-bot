#!/usr/bin/env bash
# Cron wrapper: run ONE Seykota cycle using this bot's own venv, with logging.
# Cron entry (UTC on DigitalOcean), weekdays — staggered 5 min after the hour so it
# never collides with the other bot's account switch:
#
#   5 15 * * 1-5  /opt/seykota-bot/deploy/run_once.sh
#
set -euo pipefail
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BOT_DIR"
mkdir -p logs
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "===== cycle $TS =====" >> logs/cron.log
"$BOT_DIR/.venv/bin/python" run_paper.py --once >> logs/cron.log 2>&1
echo "----- done $TS -----" >> logs/cron.log
