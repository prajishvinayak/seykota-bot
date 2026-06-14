#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Set up the Seykota bot in its OWN isolated virtualenv on an Ubuntu droplet.
# Safe to run alongside another bot — it touches only this folder + its .venv.
# ---------------------------------------------------------------------------
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BOT_DIR"
echo ">> Seykota bot dir: $BOT_DIR"

echo ">> Installing system packages (python venv/pip)…"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

echo ">> Creating isolated virtualenv (.venv)…"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p logs state output

echo ""
echo ">> Done. This bot is fully isolated in: $BOT_DIR/.venv"
echo ">> Next:"
echo "   1) Create $BOT_DIR/.env with your Capital.com demo creds (do NOT commit it)."
echo "   2) Test:   $BOT_DIR/.venv/bin/python check_connection.py"
echo "   3) Dry-run:$BOT_DIR/.venv/bin/python run_paper.py --dry-run"
echo "   4) Schedule with cron (see DEPLOY.md)."
