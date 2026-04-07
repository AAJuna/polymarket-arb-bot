#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git pull --ff-only

python3 -m py_compile \
  main.py \
  dashboard.py \
  scanner.py \
  arbitrage.py \
  executor.py \
  portfolio.py \
  ai_analyzer.py

pm2 restart polymarket-bot polymarket-dashboard --update-env
pm2 save

pm2 status
