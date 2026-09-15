"""汎用データモデルと、数値の出所の検証。

このMVPの肝は「その数字はどこから来たのか」を必ず言えること。
出所の無い数値を作れないことを、型のレベルで守る。
"""
from __future__ import annotations

import pytest

from worklens.core.models import (
    METRIC_SPECS, Metric, Process, SOURCE_CONFIDENCE, source_label,
)


def _process(**metrics) -> Process:
    p = Process(id="p1", company_id="c1", name="テスト業務")
    for key, (value, source) in metrics.items():
        p.metrics[key] = Metric(key, value, source)
    return p


def test_metric_requires_a_known_source():
    with pytest.raises(ValueError, match="出所が不正"):
        Metric("minutes_per_run", 10, "なんとなく")
    with pytest.raises(ValueError, match="出所が不正"):
        Metric("minutes_per_run", 10, "")


@pytest.mark.parametrize("source,label", [
    ("measured", "実測"), ("declared", "申告"), ("estimated", "推定"),
])
def test_every_source_is_displayable_in_japanese(source, label):
    assert source_label(source) == label
    assert Metric("minutes_per_run", 5, source).source_label == label


def test_metric_range_is_enforced():
    with pytest.raises(ValueError, match="範囲外"):
        Metric("error_rate", 1.5, "declared")
    with pytest.raises(ValueError, match="範囲外"):
        Metric("minutes_per_run", -1, "declared")
    assert Metric("error_rate", 1.0, "declared").value == 1.0


def test_unit_comes_from_the_specification():
    assert Metric("minutes_per_run", 5, "declared").unit == "分"
    assert Metric("runs_per_month", 5, "declared").unit == "回/月"


def test_monthly_minutes_is_derived_from_the_two_required_metrics():
    p = _process(minutes_per_run=(12, "measured"), runs_per_month=(240, "measured"))
    assert p.monthly_minutes == 2880


def test_derived_value_inherits_the_weakest_source():
    """実測×申告の掛け算を「実測」と言ってはいけない。"""
    p = _process(minutes_per_run=(12, "measured"), runs_per_month=(240, "declared"))
    assert p.monthly_minutes_source == "declared"

    p2 = _process(minutes_per_run=(12, "declared"), runs_per_month=(240, "estimated"))
    assert p2.monthly_minutes_source == "estimated"

    p3 = _process(minutes_per_run=(12, "measured"), runs_per_month=(240, "measured"))
    assert p3.monthly_minutes_source == "measured"


def test_confidence_is_higher_for_measured_data():
    measured = _process(minutes_per_run=(12, "measured"), runs_per_month=(240, "measured"))
    declared = _process(minutes_per_run=(12, "declared"), runs_per_month=(240, "declared"))
    estimated = _process(minutes_per_run=(12, "estimated"), runs_per_month=(240, "estimated"))
    assert measured.confidence > declared.confidence > estimated.confidence
    assert SOURCE_CONFIDENCE["measured"] == 1.0


def test_confidence_rises_as_more_fields_are_filled():
    sparse = _process(minutes_per_run=(12, "measured"), runs_per_month=(240, "measured"))
    full = _process(
        minutes_per_run=(12, "measured"), runs_per_month=(240, "measured"),
        manual_ratio=(0.9, "measured"), pc_operation_ratio=(1.0, "measured"),
        data_entry_ratio=(0.8, "measured"), transcription_fields=(14, "measured"),
        error_rate=(0.04, "measured"),
    )
    assert full.confidence > sparse.confidence
    assert full.confidence == 1.0
    assert full.missing_metrics == []


def test_process_without_metrics_has_no_confidence():
    """数値が無い業務を、分析にかけてよい状態と誤認させない。"""
    p = Process(id="p", company_id="c", name="未計測の業務")
    assert p.confidence == 0.0
    assert len(p.missing_metrics) == len(METRIC_SPECS)


def test_to_dict_carries_the_source_alongside_the_number():
    p = _process(minutes_per_run=(12, "measured"), runs_per_month=(240, "declared"))
    data = p.to_dict()
    assert data["monthly_minutes_source"] == "declared"
    assert data["metrics"]["minutes_per_run"]["source"] == "measured"
