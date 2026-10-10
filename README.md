# DOGE data collector

Automated **hourly** market snapshots from OKX's public spot API for BTC-USDT, ETH-USDT, DOGE-USDT. No API key is required.

## Outputs

- `data/latest.json`: last **successfully validated** spot snapshot (not a live feed).
- `data/hourly.csv`: one row per confirmed 1-hour candle per market, deduplicated by symbol + candle start.
- GitHub Actions log: execution and error visibility.

## Run

GitHub → Actions → Collect OKX public market data → Run workflow. A scheduled run is also requested at minute 7 of every UTC hour (actual start may be delayed or skipped by GitHub). Actions must be enabled and token must have **Contents: read and write** permission. If the first run fails on `git push`, check Settings → Actions → General → Workflow permissions.

No GitHub-hosted cron workflow is a guarantee of 24/7 real-time data collection. A report must check `collected_at_ms` and the individual OKX market timestamps before presenting figures as current; if stale, fail closed.

## Coverage and limits

Phase 1 collects **spot prices, bid/ask, 24h volume, and confirmed 1H OHLCV**. It does **not** collect funding, open interest, liquidations, ETF flows, chain activity, macro series, or news; those require additional validated data sources and independent monitors.

This is a **research input** only, not a trading or forecasting system. Do not claim that GitHub storage automatically gives ChatGPT direct access to private account data.
