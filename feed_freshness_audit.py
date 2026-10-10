#!/usr/bin/env python3
"""Read-only DOGE feed freshness check; a passing check is NOT scheduler/writeback proof."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

FEEDS = {
 "market": ("data/latest.json", "collected_at_utc", 3),
 "onchain": ("data/onchain/latest.json", "observed_at_utc", 36),
 "whales": ("data/whales/latest.json", "observed_to_utc", 8),
 "liquidations": ("data/liquidations/latest.json", "ended_at_utc", 3),
 "liquidity": ("data/liquidity/latest.json", "collected_at_utc", 3),
 "etf": ("data/etf/latest.json", "ended_at_utc", 96),
 "macro": ("data/macro/latest.json", "ended_at_utc", 48),
}
def check(base=None, now=None):
 base=Path(base) if base is not None else Path(__file__).resolve().parent
 now=now or datetime.now(timezone.utc)
 failures=[]
 for name,(path,field,limit) in FEEDS.items():
  try:
   obj=json.loads((base/path).read_text(encoding="utf-8"))
   if obj.get("status")!="ok": raise ValueError("status="+str(obj.get("status")))
   timestamp=obj.get(field)
   if not isinstance(timestamp,str):raise ValueError("timestamp missing")
   stamp=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
   if stamp.tzinfo is None:raise ValueError("timestamp missing timezone")
   age=(now-stamp).total_seconds()/3600
   if age < -0.25:raise ValueError("future timestamp")
   if age > limit:raise ValueError(f"stale {age:.1f} hours (limit {limit})")
   print(f"PASS {name}: age={age:.1f}h")
  except (OSError,ValueError,TypeError,KeyError) as error:
   failures.append(name)
   print(f"FAIL {name}: {error}")
 print("LIMIT: freshness only. Does not verify 24/7 continuity, completeness, exchange flows or Sheets sync.")
 return failures
if __name__=="__main__":sys.exit(1 if check() else 0)
