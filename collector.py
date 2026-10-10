#!/usr/bin/env python3
"""OKX public market data collector, Python standard library only."""
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


def get(path, parameters):
    url = BASE + path + "?" + urllib.parse.urlencode(parameters)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "doge-research-collector/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                obj = json.load(resp)
            if obj.get("code") != "0" or not obj.get("data"):
                raise ValueError(f"OKX response: {obj.get('code')} {obj.get('msg')}")
            return obj["data"]
        except (OSError, ValueError) as e:
            if attempt == 2:
                raise RuntimeError(f"Request failed ({path}): {e}") from e
            time.sleep(2 ** attempt)


def collect():
    collected_ms = int(time.time() * 1000)
    records = []
    for symbol in MARKETS:
        ticker = get("/api/v5/market/ticker", {"instId": symbol})[0]
        candles = get("/api/v5/market/history-candles", {"instId": symbol, "bar": "1H", "limit": "5"})
        closed = next((x for x in candles if len(x) >= 9 and x[8] == "1"), None)
        if closed is None:
            raise ValueError(f"{symbol}: no confirmed 1H candle")
        ticker_ms, hour_ms = int(ticker["ts"]), int(closed[0])
        if not 0 <= collected_ms - ticker_ms <= 600_000:
            raise ValueError(f"{symbol}: stale ticker")
        if not 0 <= collected_ms - hour_ms <= 7_300_000:
            raise ValueError(f"{symbol}: stale 1H candle")
        if float(ticker["last"]) <= 0 or float(ticker["bidPx"]) <= 0 or float(ticker["askPx"]) <= 0:
            raise ValueError(f"{symbol}: invalid price")
        if float(ticker["bidPx"]) > float(ticker["askPx"]):
            raise ValueError(f"{symbol}: crossed bid-ask")
        records.append({
            "symbol": symbol,
            "ticker_ts_ms": ticker_ms,
            "last": ticker["last"], "bid": ticker["bidPx"], "ask": ticker["askPx"],
            "volume_24h_base": ticker["vol24h"], "volume_24h_quote": ticker["volCcy24h"],
            "closed_hour_start_ms": hour_ms, "hour_open": closed[1],
            "hour_high": closed[2], "hour_low": closed[3],
            "hour_close": closed[4], "hour_volume_base": closed[5],
        })
    latest = {"schema_version": 1, "source": "OKX public spot REST API", "collected_at_utc": datetime.now(timezone.utc).isoformat(), "collected_at_ms": collected_ms, "status": "ok", "markets": {r["symbol"]: r for r in records}}
    # Save historical file before latest, and never publish a partial per-symbol snapshot.
    history = OUT / "hourly.csv"
    fieldnames = ["collected_at_ms"] + list(records[0].keys())
    prior = set()
    if history.exists():
        with history.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != fieldnames:
                raise ValueError("CSV schema changed: manual migration required")
            prior = {(row["symbol"], row["closed_hour_start_ms"]) for row in reader}
    with history.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not prior and history.stat().st_size == 0:
            writer.writeheader()
        for row in records:
            if (row["symbol"], str(row["closed_hour_start_ms"])) not in prior:
                writer.writerow({"collected_at_ms": collected_ms, **row})
    tmp = OUT / "latest.json.tmp"
    tmp.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OUT / "latest.json")
    print(f"OK: {len(records)} markets @ {latest['collected_at_utc']}")


if __name__ == "__main__":
    collect()
