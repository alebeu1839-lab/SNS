"""取り込み（CSV / JSON / 手動入力 / サンプル）の検証。"""
from __future__ import annotations

import json

import pytest

from worklens.ingest.base import normalize_source, to_bool, to_number
from worklens.ingest.csv_import import parse_csv
from worklens.ingest.importer import import_processes
from worklens.ingest.json_import import parse_json
from worklens.ingest.sample_data import list_samples, load_sample
from worklens.storage.db import init_db
from worklens.storage.registry_repo import RegistryRepo


@pytest.fixture()
def reg(home):
    return RegistryRepo(init_db(home / "worklens.db"))


# ------------------------------------------------------------ 値の解釈
@pytest.mark.parametrize("value", ["1", "true", "はい", "あり", "○", "YES"])
def test_truthy_words(value):
    assert to_bool(value) is True


@pytest.mark.parametrize("value", ["0", "no", "いいえ", "なし", "×", ""])
def test_falsy_words(value):
    assert to_bool(value) is False


def test_unreadable_boolean_is_reported():
    with pytest.raises(ValueError, match="はい/いいえ"):
        to_bool("たぶん")


def test_numbers_tolerate_separators_and_percent():
    assert to_number("1,250") == 1250
    assert to_number("35％") == 35
    assert to_number("") is None
    with pytest.raises(ValueError, match="数値として読めません"):
        to_number("たくさん")


@pytest.mark.parametrize("value,expected", [
    ("実測", "measured"), ("measured", "measured"), ("ログ", "measured"),
    ("申告", "declared"), ("ヒアリング", "declared"),
    ("推定", "estimated"), ("概算", "estimated"),
])
def test_source_words_are_normalized(value, expected):
    assert normalize_source(value) == expected


def test_unknown_source_is_refused_not_guessed():
    """出所が読めないものを勝手に「実測」にしない。"""
    with pytest.raises(ValueError, match="出所が不明"):
        normalize_source("たぶん実測")


def test_missing_source_falls_back_to_declared():
    assert normalize_source("") == "declared"


# ------------------------------------------------------------------ CSV
CSV = """業務名,担当部署,担当者,作業時間,月間回数,出所,転記,判断作業,使用ソフト,使用Webサービス,作業ステップ,問題点
車両情報の掲載,業務課,山田,12,240,実測,あり,なし,Excel|エクスプローラー,在庫管理/掲載サイト,確認→入力→保存,転記ミス
問い合わせ対応,営業課,佐藤,8,120,申告,なし,あり,Outlook,,受信→返信,
"""


def test_csv_accepts_japanese_headers_and_mixed_separators():
    records, warnings = parse_csv(CSV)
    assert warnings == []
    assert len(records) == 2
    first = records[0]
    assert first.name == "車両情報の掲載"
    assert first.department == "業務課"
    assert first.has_transcription is True and first.has_judgment is False
    assert [t.name for t in first.tools] == [
        "Excel", "エクスプローラー", "在庫管理", "掲載サイト"
    ]
    assert len(first.steps) == 3
    assert first.metrics["minutes_per_run"] == (12.0, "measured", "")


def test_csv_reports_unreadable_columns_instead_of_dropping_silently():
    records, warnings = parse_csv("業務名,作業時間,月間回数,謎の列\nA,5,10,x\n")
    assert any("謎の列" in w for w in warnings)
    assert len(records) == 1


def test_csv_without_header_is_reported():
    _, warnings = parse_csv("")
    assert warnings and "見出し行" in warnings[0]


def test_csv_row_with_bad_source_is_reported_per_line():
    records, warnings = parse_csv("業務名,作業時間,月間回数,出所\nA,5,10,たぶん\n")
    assert records == []
    assert any("2行目" in w for w in warnings)


# ----------------------------------------------------------------- JSON
def test_json_carries_per_metric_sources():
    payload = {
        "company": {"name": "A社", "industry": "小売"},
        "processes": [{
            "name": "台帳入力",
            "metrics": {
                "minutes_per_run": {"value": 12, "source": "実測", "evidence": "ログ"},
                "runs_per_month": 240,
            },
            "tools": [{"name": "Excel", "kind": "software", "has_api": 1}],
        }],
    }
    company, records, warnings = parse_json(json.dumps(payload, ensure_ascii=False))
    assert company["name"] == "A社" and warnings == []
    metrics = records[0].metrics
    assert metrics["minutes_per_run"][1] == "measured"
    assert metrics["runs_per_month"][1] == "declared"
    assert "出所の記載が無い" in metrics["runs_per_month"][2], "推測せず、扱いを明示する"


def test_broken_json_is_reported():
    _, records, warnings = parse_json("{壊れている")
    assert records == [] and warnings and "JSON として読めません" in warnings[0]


# ------------------------------------------------------------ 登録まで
def test_import_creates_then_updates(reg):
    cid = reg.upsert_company("A社")
    records, _ = parse_csv(CSV)
    first = import_processes(reg, cid, records, kind="csv")
    assert (first.created, first.updated) == (2, 0)

    second = import_processes(reg, cid, records, kind="csv")
    assert (second.created, second.updated) == (0, 2)
    assert len(reg.list_processes(cid)) == 2


def test_broken_row_does_not_stop_the_good_ones(reg):
    """1件壊れていても他は入る。大きいCSVで何も入らないのが一番困る。"""
    cid = reg.upsert_company("A社")
    records, _ = parse_csv(
        "業務名,作業時間,月間回数\n正常な業務,5,10\n,8,20\n必須欠け,,\n"
    )
    result = import_processes(reg, cid, records, kind="csv")
    assert result.created == 1
    assert result.skipped == 2
    assert len(result.errors) == 2
    assert any("業務名が空です" in e for e in result.errors)
    assert any("月間実行回数" in e for e in result.errors)


def test_import_is_recorded_for_audit(reg):
    cid = reg.upsert_company("A社")
    records, _ = parse_csv(CSV)
    import_processes(reg, cid, records, kind="csv", filename="test.csv")
    runs = reg.list_imports(cid)
    assert runs[0]["kind"] == "csv" and runs[0]["filename"] == "test.csv"
    assert runs[0]["created"] == 2


# --------------------------------------------------------- サンプル
def test_sample_is_data_not_hardcoded_logic():
    """業種固有の知識は samples/*.json にだけ置く。"""
    samples = list_samples()
    assert any(s["key"] == "used_car_dealer" for s in samples)
    used_car = next(s for s in samples if s["key"] == "used_car_dealer")
    assert used_car["industry"] == "中古車販売"
    assert used_car["processes"] == 8


def test_loading_the_sample_registers_masters_and_processes(reg):
    cid, result = load_sample(reg, "used_car_dealer")
    assert result.created == 8 and result.errors == []
    assert len(reg.list_departments(cid)) == 3
    assert len(reg.list_staff(cid)) == 3
    assert len(reg.list_workplaces(cid)) == 2
    assert len(reg.list_tools(cid)) == 6

    company = reg.get_company(cid)
    assert company.industry_name == "中古車販売"
    assert company.employee_count == 18

    top = max(reg.list_processes(cid), key=lambda p: p.monthly_minutes)
    assert top.name == "中古車情報の掲載サイトへの入力"
    assert top.monthly_minutes_source == "measured"
    assert top.confidence == 1.0


def test_loading_the_sample_twice_does_not_duplicate(reg):
    cid, _ = load_sample(reg, "used_car_dealer")
    cid2, second = load_sample(reg, "used_car_dealer")
    assert cid == cid2
    assert second.created == 0 and second.updated == 8
    assert len(reg.list_processes(cid)) == 8


def test_missing_sample_is_reported(reg):
    with pytest.raises(FileNotFoundError):
        load_sample(reg, "存在しない業種")
