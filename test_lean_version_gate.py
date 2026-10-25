"""Regression tests: no legacy, canary, future input or missing proof contamination."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lean_version_gate as gate


class VersionBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = gate.verify_installed()

    def _row(self, track, h):
        return {
            "track": track, "horizon_hours": h, "status": "WAIT_ACTIVATION",
            "prediction_verified": False, "direction": None, "probability_up": None,
            "latest_same_event": {"accuracy": None, "brier": None,
                                  "net_return": None, "stress_net_return": None},
        }

    def _report(self):
        return {
            "schema": "DOGE_PLUS_SHADOW_V1",
            "status": "SHADOW_REPORT_READY",
            "forecasts": [self._row("original18", 1),
                          self._row("original18", 24), self._row("r2", 1)],
            "issuance_performed": False, "production_model_changed": False,
            "promotion_authorized": False, "real_orders_authorized": False,
            "validated_70pct": False,
        }

    def _future_row(self):
        return {
            "namespace": "lean_v1_research_only", "protocol_id": "REGISTERED_TEST_V1",
            "model_identity": "frozen_model_test", "forecast_id": "test-001",
            "horizon_hours": 1, "status": "RESEARCH_ONLY", "cohort": "formal",
            "issued_at_utc": "2026-10-16T00:10:00Z",
            "available_at_utc": "2026-10-16T00:09:59Z",
            "promotion_authorized": False, "real_orders_authorized": False,
            "score_verified": False, "actual": None, "accuracy": None,
            "net_return": None,
        }

    def test_authoritative_frozen_policy_files_match_registry(self):
        p = gate.verify_installed()
        self.assertEqual(set(p["frozen_tracks"]), {"v42", "original18", "r2"})
        self.assertTrue(p["legacy"]["historical_read_only"])
        self.assertEqual(p["gate_state"], "STATIC_CONFIG_ONLY_RUNTIME_UNVERIFIED")

    def test_shadow_projection_accepts_only_expected_three_separated_tracks(self):
        report = self._report()
        result = gate.assert_shadow_projection(report, self.policy)
        self.assertEqual(result["status"], "STATIC_NAMESPACE_GATE_PASS_ONLY")
        self.assertEqual(len(result["tracks"]), 3)

    def test_duplicate_track_and_horizon_rejected(self):
        report = self._report()
        report["forecasts"][2]["track"] = "original18"
        with self.assertRaises(gate.VersionIsolationError):
            gate.assert_shadow_projection(report, self.policy)

    def test_legacy_and_canary_tracks_rejected(self):
        for track in ("sheet_legacy", "v41", "canary", "v42"):
            report = self._report()
            report["forecasts"][2]["track"] = track
            with self.subTest(track=track), self.assertRaises(gate.VersionIsolationError):
                gate.assert_shadow_projection(report, self.policy)

    def test_unverified_direction_or_return_rejected(self):
        for field, value in (("direction", "UP"), ("probability_up", .8)):
            report = self._report()
            report["forecasts"][0][field] = value
            with self.subTest(field=field), self.assertRaises(gate.VersionIsolationError):
                gate.assert_shadow_projection(report, self.policy)
        report = self._report()
        report["forecasts"][0]["latest_same_event"]["net_return"] = .01
        with self.assertRaises(gate.VersionIsolationError):
            gate.assert_shadow_projection(report, self.policy)

    def test_rename_cannot_authorize_model_release(self):
        report = self._report()
        report["promotion_authorized"] = True
        with self.assertRaises(gate.VersionIsolationError):
            gate.assert_shadow_projection(report, self.policy)

    def test_future_lean_proof_can_pass_without_becoming_production(self):
        self.assertEqual(gate.validate_future_lean_records([self._future_row()]), 1)

    def test_future_lean_cannot_import_old_rows(self):
        for name in ("sheet_legacy", "plus_r2_frozen", "v42_original_frozen"):
            item = self._future_row()
            item["namespace"] = name
            with self.subTest(name=name), self.assertRaises(gate.VersionIsolationError):
                gate.validate_future_lean_records([item])

    def test_future_lean_timestamps_and_score_leakage(self):
        item = self._future_row()
        item["available_at_utc"] = "2026-10-16T00:10:01Z"
        with self.assertRaises(gate.VersionIsolationError):
            gate.validate_future_lean_records([item])
        item = self._future_row()
        item["accuracy"] = 1
        with self.assertRaises(gate.VersionIsolationError):
            gate.validate_future_lean_records([item])

    def test_future_lean_rejects_colliding_keys_and_canaries(self):
        row = self._future_row()
        with self.assertRaises(gate.VersionIsolationError):
            gate.validate_future_lean_records([row, copy.deepcopy(row)])
        row["cohort"] = "canary"
        with self.assertRaises(gate.VersionIsolationError):
            gate.validate_future_lean_records([row])

    def test_frozen_protocol_drift_detected(self):
        original = gate._object
        def changed(path):
            obj = original(path)
            if Path(path).as_posix().endswith("config/phase2_forward_v2.json"):
                obj["protocol_id"] = "RENAMED_TO_LEAN_WITHOUT_FREEZE"
            return obj
        with patch.object(gate, "_object", side_effect=changed):
            with self.assertRaises(gate.VersionIsolationError):
                gate.verify_installed()

    def test_unknown_or_extra_lean_output_directory_is_not_accepted(self):
        original_exists = Path.exists
        def path_exists(p):
            if p.as_posix().endswith("/data/lean_v1"):
                return True
            return original_exists(p)
        with patch.object(Path, "exists", path_exists):
            with self.assertRaises(gate.VersionIsolationError):
                gate.verify_installed()


if __name__ == "__main__":
    unittest.main()
