"""Fail-closed Lean-v1 namespace and provenance admission checks.

This file does not issue forecasts, score frozen histories, or modify data.
A project rename or governance revision NEVER constitutes a new model version.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
POLICY = "config/lean_version_isolation_v1.json"


class VersionIsolationError(ValueError):
    """Stop cross-version publication instead of silently falling back."""


def _object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise VersionIsolationError("missing or linked policy: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VersionIsolationError("policy must be JSON object: " + str(path))
    return value


def _date(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise VersionIsolationError("missing UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise VersionIsolationError("invalid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise VersionIsolationError("timestamp must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def verify_installed(root: Path = ROOT) -> dict:
    root = Path(root)
    p = _object(root / POLICY)
    if p.get("schema") != "DOGE_LEAN_VERSION_ISOLATION_V1":
        raise VersionIsolationError("wrong Lean policy schema")
    if p.get("governance_version") != "DOGE_PREDICT_PLUS_LEAN_V1_20261015":
        raise VersionIsolationError("unregistered governance version")
    if p.get("authority_unchanged") is not True or p.get("no_order_execution") is not True or p.get("no_auto_release") is not True:
        raise VersionIsolationError("unapproved authority/permission change")
    if p.get("gate_state") != "STATIC_CONFIG_ONLY_RUNTIME_UNVERIFIED":
        raise VersionIsolationError("governance cannot claim production pass")
    expected_namespaces = {
        "sheet_legacy", "v42_original_frozen", "plus_original18_frozen",
        "plus_r2_frozen", "lean_v1_research_only"
    }
    if set(p.get("scopes_separate", [])) != expected_namespaces:
        raise VersionIsolationError("missing or duplicate source namespace")
    if p.get("legacy", {}).get("historical_read_only") is not True or p["legacy"].get("excluded_from_plus_forward_accuracy") is not True:
        raise VersionIsolationError("legacy history not read-only")
    if p.get("projection", {}).get("schema") != "DOGE_PLUS_SHADOW_V1" or p["projection"].get("mixed_statistic_pools_prohibited") is not True:
        raise VersionIsolationError("projection isolation missing")
    expected = {
        "v42": ("data/stage5/pre_registration.json", "DOGE_V42_DIRECTION_SHADOW_LOCK_20261012", "2026-10-12T00:00:00+00:00", (1, 24), "data/stage5/forward_validation_v2"),
        "original18": ("config/learned_shadow_v1.json", "DOGE_LEARNED_SHADOW_V1_20261016", "2026-10-16T00:00:00+00:00", (1, 24), "data/learned_shadow_v1"),
        "r2": ("config/phase2_forward_v2.json", "DOGE_V2_FROZEN120_20261020_R2", "2026-10-20T00:00:00Z", (1,), "data/phase2_forward_v2"),
    }
    if set(p.get("frozen_tracks", {})) != set(expected):
        raise VersionIsolationError("frozen tracks changed")
    known = set()
    for track, (source, protocol, effective, hours, evidence) in expected.items():
        entry = p["frozen_tracks"][track]
        if any(entry.get(k) != v for k, v in {
            "policy_file": source, "protocol_id": protocol,
            "effective_from_utc": effective, "evidence": evidence,
            "allowed_horizons_hours": list(hours), "model_changed": False,
        }.items()):
            raise VersionIsolationError("unexpected track binding: " + track)
        if entry.get("namespace") in known or entry.get("namespace") not in expected_namespaces:
            raise VersionIsolationError("shared or unknown namespace")
        known.add(entry["namespace"])
        local = _object(root / source)
        if local.get("protocol_id") != protocol or _date(local.get("effective_from_utc")) != _date(effective):
            raise VersionIsolationError("actual frozen protocol binding mismatch: " + track)
        authority_flag = "automated_trading_authorized" if track == "v42" else "real_orders_authorized"
        if local.get(authority_flag) is not False:
            raise VersionIsolationError("trading authorization unexpected: " + track)
        if track == "v42" and local.get("model_id") != "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1":
            raise VersionIsolationError("frozen V4.2 model identity drift")
        if track == "r2" and (entry.get("exclude_canary") is not True or local.get("canary_included_in_formal_statistics") is not False):
            raise VersionIsolationError("R2 canary segregation drift")
        if not (root / evidence).is_dir():
            raise VersionIsolationError("missing frozen evidence namespace: " + evidence)
    lean = p.get("lean_research", {})
    if any(lean.get(k) != v for k, v in {
        "namespace": "lean_v1_research_only",
        "future_output_root": "data/lean_v1/",
        "status": "NO_NEW_PREDICTIONS_REGISTERED",
        "new_forecast_issuance_authorized": False,
        "automatic_promotion": False,
    }.items()):
        raise VersionIsolationError("new research must remain isolated and unissued")
    if (root / "data/lean_v1").exists():
        raise VersionIsolationError("unregistered Lean forecast/output directory")
    return p


def assert_shadow_projection(report: dict, policy: dict) -> dict:
    if report.get("schema") != "DOGE_PLUS_SHADOW_V1" or report.get("status") != "SHADOW_REPORT_READY":
        raise VersionIsolationError("unregistered PLUS projection")
    if any(report.get(k) is not False for k in (
        "issuance_performed", "production_model_changed",
        "promotion_authorized", "real_orders_authorized", "validated_70pct"
    )):
        raise VersionIsolationError("report cannot issue/promote/claim validation")
    allowed = {(item["track"], item["horizon_hours"]) for item in policy["projection"]["allowed_rows"]}
    rows = report.get("forecasts")
    if not isinstance(rows, list) or len(rows) != len(allowed):
        raise VersionIsolationError("missing or extra report tracks")
    observed = set()
    for row in rows:
        if not isinstance(row, dict):
            raise VersionIsolationError("invalid report row")
        key = (row.get("track"), row.get("horizon_hours"))
        if key not in allowed or key in observed:
            raise VersionIsolationError("cross-track, canary, or duplicate row")
        observed.add(key)
        if row.get("status") not in (
            "WAIT_ACTIVATION", "NO_FORMAL_ISSUANCE",
            "UNVERIFIED_PUBLICATION", "PENDING_TARGET",
            "TARGET_WINDOW_OPEN", "MISSING_TARGET",
            "TARGET_EVIDENCE_PENDING", "TARGET_EVIDENCE_MISSING",
            "SCORED_PUBLICLY_VERIFIED"
        ):
            raise VersionIsolationError("unrecognized scoring state")
        if not row.get("prediction_verified", False) and (
            row.get("direction") is not None or row.get("probability_up") is not None
        ):
            raise VersionIsolationError("unverified forecast direction leaked")
        if row.get("status") != "SCORED_PUBLICLY_VERIFIED":
            measures = row.get("latest_same_event", {})
            if any(measures.get(k) is not None for k in ("accuracy", "net_return", "stress_net_return", "brier")):
                raise VersionIsolationError("unverified target scored or profit imputed")
    return {
        "status": "STATIC_NAMESPACE_GATE_PASS_ONLY",
        "governance_version": policy["governance_version"],
        "tracks": sorted([f"{k[0]}:{k[1]}h" for k in observed]),
        "model_issuance_performed": False,
        "runtime_stability_verified": False,
        "independent_retention_verified": False,
    }


def validate_future_lean_records(records: list, existing_keys: set | None = None) -> int:
    """Admission API for future Lean studies; never silently convert legacy rows."""
    if not isinstance(records, list):
        raise VersionIsolationError("registered batch required")
    keys = set(existing_keys or ())
    for record in records:
        if not isinstance(record, dict):
            raise VersionIsolationError("invalid forecast row")
        if record.get("namespace") != "lean_v1_research_only" or record.get("status") != "RESEARCH_ONLY":
            raise VersionIsolationError("old or unknown namespace in Lean output")
        if record.get("cohort") != "formal" or record.get("horizon_hours") not in (1, 24):
            raise VersionIsolationError("canary or unknown horizon cannot be promoted")
        if record.get("promotion_authorized") is not False or record.get("real_orders_authorized") is not False:
            raise VersionIsolationError("not an authorized study-only record")
        fields = ("namespace", "protocol_id", "model_identity", "forecast_id", "horizon_hours")
        if any(not isinstance(record.get(k), str) or not record[k].strip() for k in fields[:-1]):
            raise VersionIsolationError("missing immutable identity")
        key = tuple(record[k] for k in fields)
        if key in keys:
            raise VersionIsolationError("forecast version key collision")
        keys.add(key)
        issued = _date(record.get("issued_at_utc"))
        available = _date(record.get("available_at_utc"))
        if available > issued:
            raise VersionIsolationError("feature data arrived after forecast issuance")
        if record.get("score_verified") is not True and any(record.get(k) is not None for k in ("actual", "accuracy", "net_return")):
            raise VersionIsolationError("unverified outcome or return")
    return len(records)


def main() -> None:
    result = verify_installed()
    print(json.dumps({
        "gate": "PASS_STATIC_NAMESPACE_BOUNDARIES",
        "governance_version": result["governance_version"],
        "frozen_tracks_checked": sorted(result["frozen_tracks"]),
        "live_run_verified": False,
        "model_edge_validated": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
