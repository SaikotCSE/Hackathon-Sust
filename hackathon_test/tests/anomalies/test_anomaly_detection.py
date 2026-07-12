"""Layer 2 — Anomaly detection validation.

Maps to ``testing-scripts-prompt.md`` §2.

Covers:
  * Labeled synthetic dataset generation.
  * Precision / recall / F1 / FPR computation.
  * Dedicated false-positive test against legitimate salary-day + Eid baseline.
  * Forbidden-language assertions (no "fraud" / "confirmed" anywhere).
"""
from __future__ import annotations

import os

import pytest

from tests.helpers import synth
from tests.helpers.anomaly import Detector, confusion_matrix, metrics_from_cm
from tests.helpers.language import (
    FORBIDDEN_FINAL_DETERMINATION_WORDS,
    alert_is_safely_phrased,
    blob_contains_forbidden,
)
from tests.helpers.metrics import SINK

pytestmark = pytest.mark.layer2_anomaly


@pytest.fixture(scope="module")
def labeled_rows() -> list[dict]:
    return synth.build_labeled_dataset(n_normal=300, seed=42)


@pytest.fixture(scope="module")
def fp_baseline_rows() -> list[dict]:
    """Pure legitimate traffic — no injected anomalies."""
    return (
        synth.salary_day_normal(n=200, seed=1)
        + synth.eid_pre_holiday_normal(n=200, seed=2)
    )


@pytest.fixture(scope="module")
def detector(detector) -> Detector:
    return detector


# ---------- core confusion matrix + metric ---------------------------------

class TestLabeledMetrics:
    def test_precision_recall_f1_reported(self, labeled_rows, detector: Detector):
        cm = confusion_matrix(labeled_rows, detector)
        m = metrics_from_cm(cm)
        # Sanity: precision and recall in [0, 1]; support > 0.
        assert 0.0 <= m["precision"] <= 1.0
        assert 0.0 <= m["recall"] <= 1.0
        assert 0.0 <= m["f1"] <= 1.0
        assert m["support"] == len(labeled_rows)

        SINK.set(
            "anomaly_precision_recall_f1",
            {
                "precision": m["precision"],
                "recall": m["recall"],
                "f1": m["f1"],
                "support": m["support"],
                "confusion_matrix": cm,
            },
            method="Layer 2 confusion matrix on labeled synthetic dataset",
        )

    def test_results_never_label_as_fraud(self, labeled_rows, detector: Detector):
        """Every anomaly output must carry 'unusual' / 'requires review' and zero forbidden words."""
        for i, txn in enumerate(labeled_rows):
            window = labeled_rows[max(0, i - 20):i]
            res = detector.evaluate(txn, window)
            hits = blob_contains_forbidden(res.to_dict())
            assert not hits, (
                f"forbidden word(s) {hits} found in anomaly result for txn {txn.get('id')}: {res!r}"
            )
            if res.flagged:
                assert alert_is_safely_phrased(res.to_dict()), res


# ---------- dedicated false-positive test ----------------------------------

class TestFalsePositives:
    def test_salary_and_eid_baseline_under_threshold(self, fp_baseline_rows, detector: Detector):
        """Independent FPR figure required by §12 — must NOT be folded into PR/F1."""
        fp_flags = 0
        for i, txn in enumerate(fp_baseline_rows):
            window = fp_baseline_rows[max(0, i - 20):i]
            if detector.evaluate(txn, window).flagged:
                fp_flags += 1
        total = len(fp_baseline_rows)
        fpr = fp_flags / total if total else 0.0
        threshold = float(os.environ.get("FPR_THRESHOLD", "0.05"))
        assert fpr <= threshold, (
            f"FPR {fpr:.3f} exceeds threshold {threshold} ({fp_flags}/{total})"
        )

        SINK.set(
            "false_positive_rate_normal_spikes",
            {
                "false_positive_rate": round(fpr, 4),
                "flagged": fp_flags,
                "total_normal_scenarios": total,
                "threshold": threshold,
                "scenarios": ["salary_day", "eid_pre_holiday"],
            },
            method="Layer 2 dedicated FP baseline (legitimate high-volume patterns)",
        )


# ---------- forbidden language + required phrasing -------------------------

class TestLanguageGuard:
    def test_required_phrases_present_on_flagged_results(self, detector: Detector):
        # Use an injected structuring row so the result is flagged.
        rows = synth.structuring_pattern()
        for i, txn in enumerate(rows):
            window = rows[:i]
            res = detector.evaluate(txn, window)
            if res.flagged:
                blob = res.to_dict()
                # Must mention "unusual" AND "requires review" somewhere.
                assert "unusual" in str(blob).lower()
                assert "requires review" in str(blob).lower()
                # Must NOT contain any forbidden final-determination word.
                assert not blob_contains_forbidden(blob, FORBIDDEN_FINAL_DETERMINATION_WORDS)

    def test_non_flagged_results_have_low_confidence(self, detector: Detector):
        rows = synth.salary_day_normal(n=50, seed=3)
        for i, txn in enumerate(rows):
            window = rows[:i]
            res = detector.evaluate(txn, window)
            assert res.confidence < 0.5, (txn, res)