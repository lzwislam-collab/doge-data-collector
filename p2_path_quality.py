"""Read-only P2 market-path audit. Never repairs old gaps or restarts a paper cohort.

This supplemental receipt is deliberately NOT a trading P2 success gate: a clean
segment starting after a gap cannot inherit a pre-gap portfolio return or
claim prospective selection. All original gaps are preserved in the V3.1 book.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import p2_paired_execution_v3_1 as engine

ROOT = Path(__file__).resolve().parent
OUTPUT = "data/p2_path_quality/latest.json"


def clock(value):
    if not isinstance(value, str):
        raise ValueError("missing quote timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive quote timestamp")
    return dt.astimezone(timezone.utc)


def evaluate(events, now, gap_seconds=engine.GAP_SECONDS):
    """Calculate every observed book gap and contiguous suffix; do not infer missing books."""
    if now.tzinfo is None or gap_seconds <= 0:
        raise ValueError("invalid audit clock or threshold")
    now = now.astimezone(timezone.utc)
    out = {
        "schema_version": 1,
        "evaluated_at_utc": now.isoformat(),
        "path_gap_threshold_seconds": gap_seconds,
        "source": engine.EVENTS,
        "observed_books": len(events),
        "historical_gaps": [],
        "historical_gap_count": 0,
        "no_historical_reconstruction": True,
        "original_v3_1_ledger_unchanged": True,
        "clean_segment_is_not_an_economic_backtest": True,
        "p2_economic_acceptance": False,
    }
    if not events:
        out.update(status="WAIT_NO_SOURCE_BOOKS", latest_book_utc=None,
                   recent_gap_free_seconds=None, tail_age_seconds=None,
                   last_gap_ends_at_utc=None, next_validation="Collect genuine future books")
        return out
    previous = None
    seen_hashes = set()
    segment_start = None
    for position, event in enumerate(events):
        qt = clock(event.get("quote_utc"))
        recorded = clock(event.get("captured_at_utc"))
        book_id = event.get("book_sha256")
        if not isinstance(book_id, str) or len(book_id) != 64 or book_id in seen_hashes:
            raise ValueError("duplicate/invalid book ID")
        seen_hashes.add(book_id)
        if not qt <= recorded <= now or (recorded - qt).total_seconds() > 30:
            raise ValueError("invalid book captured/quote times")
        if previous is not None:
            delta = (qt - previous).total_seconds()
            if delta <= 0:
                raise ValueError("nonmonotonic genuine market-book clocks")
            if delta > gap_seconds:
                out["historical_gaps"].append({
                    "prior_quote_utc": previous.isoformat(),
                    "next_quote_utc": qt.isoformat(),
                    "missing_observation_seconds": round(delta, 3),
                    "next_book_index": position,
                })
                segment_start = qt
        else:
            segment_start = qt
        previous = qt
    tail = (now - previous).total_seconds()
    if tail < 0:
        raise ValueError("last quote is in the future")
    out["historical_gap_count"] = len(out["historical_gaps"])
    out["first_book_utc"] = clock(events[0]["quote_utc"]).isoformat()
    out["latest_book_utc"] = previous.isoformat()
    out["last_gap_ends_at_utc"] = out["historical_gaps"][-1]["next_quote_utc"] if out["historical_gaps"] else None
    out["contiguous_suffix_start_utc"] = segment_start.isoformat()
    out["recent_gap_free_seconds"] = round((previous - segment_start).total_seconds(), 3)
    out["tail_age_seconds"] = round(tail, 3)
    out["latest_mark_fresh"] = tail <= gap_seconds
    out["status"] = ("WAIT_STALE_TAIL" if tail > gap_seconds
                     else "GAPS_PRESERVED_NEW_SEGMENT_OBSERVED" if out["historical_gaps"]
                     else "NO_OBSERVED_SOURCE_GAPS")
    out["next_validation"] = ("Future gap-free observations and a separately precommitted "
                              "economic cohort; the existing V3.1 cumulative gap count remains")
    return out


def main(root=ROOT, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    try:
        # Validates the original append-only chain, including raw book snapshot hashes.
        events = engine.history_guard(root)
        audit = evaluate(events, now)
    except (ValueError, OSError, KeyError, TypeError) as error:
        audit = {
            "schema_version": 1, "status": "BLOCKED_INVALID_SOURCE",
            "evaluated_at_utc": now.isoformat(),
            "error": type(error).__name__ + ": " + str(error)[:300],
            "p2_economic_acceptance": False,
            "original_v3_1_ledger_unchanged": True,
        }
    output = root / OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "historical_gap_count": audit.get("historical_gap_count"),
                      "p2_economic_acceptance": False}))
    return 1 if audit["status"] == "BLOCKED_INVALID_SOURCE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
