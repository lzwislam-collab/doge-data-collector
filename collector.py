#!/usr/bin/env python3
"""OKX spot market collector: never present stale ticks as live prices."""
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MARKETS = ("BTC-USDT", "ETH-USDT", "DOGE-USDT")
BASE = "https://www.okx.com"
OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
FIELDNAMES = ["collected_at_ms", "symbol", "ticker_ts_ms", "last", "bid", "ask", "volume_24h_base", "volume_24h_quote", "closed_hour_start_ms", "hour_open", "hour_high", "hour_low", "hour_close", "hour_volume_base"]


def get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "doge-research-collector/1.1", "Accept": "application/json", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as response:
        obj = json.load(response)
    if obj.get("code") != "0" or not obj.get("data"):
        raise ValueError(f"OKX API error {obj.get('code')}: {obj.get('msg')}")
    return obj["data"]


def fresh_ticker(symbol):
    diagnostics = []
    for attempt in range(3):
        try:
            if attempt == 1:
                entries = get("/api/v5/market/tickers", {"instType": "SPOT"})
                candidate = next((x for x in entries if x.get("instId") == symbol), None)
                if candidate is None:
                    raise ValueError("symbol absent from SPOT tickers")
            else:
                candidate = get("/api/v5/market/ticker", {"instId": symbol})[0]
            current_ms = int(time.time() * 1000)
            age_ms = current_ms - int(candidate["ts"])
            if not 0 <= age_ms <= 600_000:
                raise ValueError(f"stale ticker: age_sec={age_ms / 1000:.0f}, ts={candidate['ts']}")
            bid, ask, last = [float(candidate[k]) for k in ("bidPx", "askPx", "last")]
            if min(bid, ask, last) <= 0 or bid > ask:
                raise ValueError("invalid ticker prices")
            return candidate
        except (OSError, KeyError, ValueError, TypeError, StopIteration) as exc:
            diagnostics.append(str(exc))
            if attempt < 2:
                time.sleep(2)
    raise ValueError(" | ".join(diagnostics))


def collect_market(symbol):
    ticker = fresh_ticker(symbol)
    candles = get("/api/v5/market/history-candles", {"instId": symbol, "bar": "1H", "limit": "5"})
    closed = next((c for c in candles if len(c) >= 9 and str(c[8]) == "1"), None)
    if closed is None:
        raise ValueError("no confirmed 1H candle")
    age_ms = int(time.time() * 1000) - int(closed[0])
    if not 0 <= age_ms <= 7_300_000:
        raise ValueError(f"stale closed 1H candle: age_sec={age_ms / 1000:.0f}")
    return {
        "symbol": symbol, "ticker_ts_ms": int(ticker["ts"]),
        "last": ticker["last"], "bid": ticker["bidPx"], "ask": ticker["askPx"],
        "volume_24h_base": ticker["vol24h"], "volume_24h_quote": ticker["volCcy24h"],
        "closed_hour_start_ms": int(closed[0]), "hour_open": closed[1],
        "hour_high": closed[2], "hour_low": closed[3], "hour_close": closed[4],
        "hour_volume_base": closed[5],
    }


def write_history(records, collected_ms):
    if not records:
        return
    history = OUT / "hourly.csv"
    prior = set()
    if history.exists():
        with history.open("r", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != FIELDNAMES:
                raise ValueError("CSV schema mismatch; migration required")
            prior = {(row["symbol"], row["closed_hour_start_ms"]) for row in reader}
    with history.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
        if history.stat().st_size == 0:
            writer.writeheader()
        for row in records:
            if (row["symbol"], str(row["closed_hour_start_ms"])) not in prior:
                writer.writerow({"collected_at_ms": collected_ms, **row})


def collect():
    records, errors = [], {}
    for symbol in MARKETS:
        try:
            records.append(collect_market(symbol))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            errors[symbol] = str(exc)
            print(f"UNAVAILABLE {symbol}: {exc}", flush=True)
    now_ms = int(time.time() * 1000)
    # Always publish health state. Only validated market records appear in markets.
    status = "ok" if not errors else ("partial" if records else "unavailable")
    result = {
        "schema_version": 2,
        "source": "OKX public spot REST API",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "collected_at_ms": now_ms,
        "status": status,
        "markets": {r["symbol"]: r for r in records},
        "unavailable_markets": errors,
    }
    write_history(records, now_ms)
    tmp = OUT / "latest.json.tmp"
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OUT / "latest.json")
    print(f"Collection complete: {len(records)}/{len(MARKETS)} valid; status={status}", flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    collect()
