# P2 paired execution shadow V2 plan

User authorized: connect paired precommitted paper decisions to independent prospective spot economics. Inline implementation, full tests, independent review, system registration and real deployment readback. No actual orders or DCA changes.

1. Fixed protocol: separate 1h/24h portfolios, 1000 USDT each, existing PAIRED V1 source, activation 2026-10-13 00:00 UTC; no old quotes, forecasts or returns imported. Earliest nonoverlap reservation precedes action/outcome; 10-minute entry limit after public witness, fixed target window25minutes. Missing entry/exit blocks the continuous prefix.
2. Fresh raw OKX books only after actual request and after publication. Visible depth sweep within1% of best; known bid/ask incorporates spread, plus10bp each-side fee and5bp extra slippage. Stress doubles fees/slippage and adds one observed half-spread each side. Insufficient depth never invents fills.
3. Append immutable hash-linked observations with raw book hash and per-ID precommitted decisions. Replay independent strategy, continuous buy-and-hold, cash benchmark and stress. NO_TRADE stays cash but still requires benchmark coverage; do not compound overlapping forecasts or skip blocking interval. Mark open positions; cost components reconcile, sampled drawdown and missing-path flags are explicit.
4. Read only original paired public proofs; register exact audited code/protocol in independent system version. Use existing hourly workflow after paired audit; capture before further nonessential diagnostics, archive before explicit failure, no newcron.
5. Tests first for chronology, quality, fees, depth, benchmarks, missingprefix, replay/tamper, no-trade, horizons and version guard. Independent review, repair, fullregression, production live-book smoke and readback. Live intervals WAIT until future activation.

Ruling: P2 old V1 ledger remains frozen. This V2 judges selective shadow decisions, not replacing official forecast accuracy. Actual account fees, fills and continuous drawdown remain unverified; manual review gates never grant real trading or automatic profitability claims. Cash benchmark has no interest.
