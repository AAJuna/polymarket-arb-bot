# Polymarket Sports Arbitrage Bot

[![CI](https://github.com/AAJuna/polymarket-arb-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/AAJuna/polymarket-arb-bot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Mode](https://img.shields.io/badge/default-paper%20trading-1f883d)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Opinionated Polymarket sports trading bot focused on paper-first execution, risk controls, and observable performance.

It scans sports markets, compares Polymarket pricing against sportsbook odds, gates trades with AI, and exposes a live dashboard for bankroll, open positions, strategy expectancy, and shadow-fill reporting.

## Highlights

- Paper trading by default
- Odds-comparison detector with stricter team matching
- Optional same-market arbitrage detection
- Claude-based AI gate for higher-quality entries
- Streamlit dashboard for portfolio, AI usage, expectancy, and shadow reports
- PM2-friendly runtime for VPS deployment

## Strategy Surface

The bot currently supports:

- `odds_comparison`: compare Polymarket implied probability against sportsbook probability
- `same_market`: detect underpriced YES/NO combinations inside one market

Execution is intentionally conservative:

- `PAPER_TRADING=true` by default
- `ENABLE_SAME_MARKET_EXECUTION=false` by default because paired execution is not atomic yet
- AI can run in `gate` mode so weak candidates do not become paper trades

## Quick Start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with your keys, then start with paper mode first:

```bash
python main.py
```

Inspection-only scan:

```bash
python main.py --opportunity-report --expiry-hours 24 --report-limit 10
```

Dashboard:

```bash
streamlit run dashboard.py
```

## Dashboard Output

The dashboard reads runtime state from `data/` and shows:

- bankroll and drawdown
- open positions and trade history
- AI usage and queue behavior
- per-strategy expectancy
- shadow-fill report for signals that were detected but not traded

## Project Layout

- [`main.py`](main.py): trading loop and orchestration
- [`scanner.py`](scanner.py): Polymarket market discovery
- [`arbitrage.py`](arbitrage.py): opportunity detection
- [`executor.py`](executor.py): paper/live execution layer
- [`portfolio.py`](portfolio.py): bankroll, positions, expectancy
- [`shadow_tracker.py`](shadow_tracker.py): shadow-fill tracking
- [`dashboard.py`](dashboard.py): Streamlit monitoring UI

## Safety Notes

- This repository is built for controlled experimentation, not blind automation.
- Do not switch to live trading before validating expectancy after fees, spread, slippage, and missed fills.
- Keep `PAPER_TRADING=true` until your shadow report and resolved-trade data justify otherwise.

## Contributing

Open an issue before large changes. Keep patches narrow, explain the behavioral impact, and include a quick verification note.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the expected workflow.

## License

This project is available under the [`MIT License`](LICENSE).
