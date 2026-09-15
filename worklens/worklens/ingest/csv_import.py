"""CSV 取り込み。

列名は日本語・英語のどちらでも受ける。実務のCSVは列名が揃わないので、
別名表を持って吸収する。読み取れない列は黙って捨てず警告に出す
（入れたつもりのデータが入っていない、という事故を防ぐため）。
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from .base import normalize_source, to_bool, to_number
from .records import ProcessRecord, StepRecord, ToolRecord

# 列名の別名。左が正、右が受け入れる表記。
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("業務名", "process", "process_name", "name", "業務"),
    "summary": ("業務概要", "概要", "summary", "description"),
    "category": ("分類", "カテゴリ", "category"),
    "department": ("担当部署", "部署", "department"),
    "owner": ("担当者", "owner", "staff"),
    "minutes_per_run": ("作業時間", "1回あたり作業時間", "minutes_per_run", "分"),
    "runs_per_month": ("月間回数", "実行頻度", "月間実行回数", "runs_per_month", "回数"),
    "manual_ratio": ("手作業率", "manual_ratio"),
    "pc_operation_ratio": ("PC操作比率", "pc_operation_ratio"),
    "data_entry_ratio": ("データ入力比率", "data_entry_ratio"),
    "transcription_fields": ("転記項目数", "transcription_fields"),
    "error_rate": ("エラー発生率", "error_rate", "エラー率"),
    "metric_source": ("数値の出所", "出所", "source", "measurement"),
    "has_data_entry": ("入力作業", "入力の有無", "has_data_entry"),
    "has_transcription": ("転記", "転記の有無", "has_transcription"),
    "has_judgment": ("判断作業", "判断作業の有無", "has_judgment"),
    "judgment_note": ("判断内容", "judgment_note"),
    "is_person_dependent": ("属人化", "is_person_dependent"),
    "security_level": ("機密度", "security_level"),
    "tools": ("使用ソフト", "使用ツール", "tools", "使用アプリ"),
    "web_services": ("使用Webサービス", "web_services", "使用Webサイト"),
    "steps": ("作業ステップ", "steps", "手順"),
    "issues": ("問題点", "課題", "issues", "エラー"),
    "evidence": ("根拠", "evidence", "備考"),
}

METRIC_COLUMNS = (
    "minutes_per_run", "runs_per_month", "manual_ratio", "pc_operation_ratio",
    "data_entry_ratio", "transcription_fields", "error_rate",
)

# 複数値の区切り。実務のCSVは区切り方が揃わないので広めに受ける。
SPLIT_PATTERN = re.compile(r"[|／/、,→]+")

_LOOKUP = {
    alias.strip().lower(): canonical
    for canonical, aliases in COLUMN_ALIASES.items()
    for alias in aliases
}


def _canonical(header: str) -> str | None:
    return _LOOKUP.get((header or "").strip().lower())


def _split(value: Any) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in SPLIT_PATTERN.split(str(value)) if part.strip()]


def parse_csv(text: str, origin: str = "csv") -> tuple[list[ProcessRecord], list[str]]:
    """CSV 本文から ProcessRecord を作る。戻り値は (レコード, 警告)。"""
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["CSV に見出し行がありません"]

    unknown = [h for h in reader.fieldnames if h and _canonical(h) is None]
    if unknown:
        warnings.append("読み取れない列は無視しました: " + "、".join(unknown))

    records: list[ProcessRecord] = []
    for line_no, raw in enumerate(reader, start=2):
        row: dict[str, Any] = {}
        for header, value in raw.items():
            key = _canonical(header or "")
            if key:
                row[key] = value
        try:
            records.append(_row_to_record(row, origin))
        except ValueError as exc:
            warnings.append(f"{line_no}行目: {exc}")
    return records, warnings


def _row_to_record(row: dict[str, Any], origin: str) -> ProcessRecord:
    source = normalize_source(row.get("metric_source"), default="declared")
    evidence = str(row.get("evidence") or "").strip()

    record = ProcessRecord(
        name=str(row.get("name") or "").strip(),
        summary=str(row.get("summary") or "").strip(),
        category=str(row.get("category") or "").strip(),
        department=(str(row.get("department") or "").strip() or None),
        owner=(str(row.get("owner") or "").strip() or None),
        judgment_note=str(row.get("judgment_note") or "").strip(),
        security_level=(str(row.get("security_level") or "normal").strip() or "normal"),
        origin=origin,
    )
    for flag in ("has_data_entry", "has_transcription", "has_judgment",
                 "is_person_dependent"):
        if row.get(flag) not in (None, ""):
            setattr(record, flag, to_bool(row[flag]))

    for key in METRIC_COLUMNS:
        value = to_number(row.get(key))
        if value is not None:
            record.metrics[key] = (value, source, evidence)

    for name in _split(row.get("tools")):
        record.tools.append(ToolRecord(name=name, kind="software"))
    for name in _split(row.get("web_services")):
        record.tools.append(ToolRecord(name=name, kind="web_service"))
    for i, action in enumerate(_split(row.get("steps")), start=1):
        record.steps.append(StepRecord(seq=i, action=action))
    for description in _split(row.get("issues")):
        record.issues.append({"kind": "problem", "description": description})
    return record
