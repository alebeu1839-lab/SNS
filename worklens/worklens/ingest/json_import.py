"""JSON 取り込み。

CSV より表現力が高いので、手順・ツールの役割・指標ごとの出所まで持てる。
将来タスクマイニングの出力を受けるときも、この形に寄せる。

想定する形:
{
  "company": {"name": "...", "industry": "...", "employee_count": 18,
              "hourly_cost_jpy": 3200},
  "processes": [
    {"name": "...", "department": "...", "owner": "...",
     "metrics": {"minutes_per_run": {"value": 12, "source": "measured",
                                     "evidence": "システムログ"}},
     "tools": [{"name": "在庫管理システム", "kind": "system", "role": "参照元",
                "has_api": 1}],
     "steps": [{"action": "情報を確認", "tool": "在庫管理システム"}],
     "issues": [{"kind": "error", "description": "転記ミス"}]}
  ]
}
"""
from __future__ import annotations

import json
from typing import Any

from .base import normalize_source, to_bool, to_number
from .records import ProcessRecord, StepRecord, ToolRecord


def parse_json(text: str | dict, origin: str = "json") -> tuple[dict, list[ProcessRecord], list[str]]:
    """戻り値は (会社情報, レコード, 警告)。"""
    warnings: list[str] = []
    try:
        data = json.loads(text) if isinstance(text, str) else dict(text)
    except json.JSONDecodeError as exc:
        return {}, [], [f"JSON として読めません: {exc}"]

    if not isinstance(data, dict):
        return {}, [], ["JSON の最上位はオブジェクトにしてください"]

    company = data.get("company") or {}
    if not isinstance(company, dict):
        warnings.append("company がオブジェクトではないため無視しました")
        company = {}

    raw_processes = data.get("processes")
    if not isinstance(raw_processes, list):
        return company, [], warnings + ["processes が配列ではありません"]

    records: list[ProcessRecord] = []
    for index, raw in enumerate(raw_processes, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"{index}件目: オブジェクトではありません")
            continue
        try:
            records.append(_to_record(raw, origin))
        except ValueError as exc:
            warnings.append(f"{index}件目「{raw.get('name', '?')}」: {exc}")
    return company, records, warnings


def _to_record(raw: dict[str, Any], origin: str) -> ProcessRecord:
    record = ProcessRecord(
        name=str(raw.get("name") or "").strip(),
        summary=str(raw.get("summary") or "").strip(),
        category=str(raw.get("category") or "").strip(),
        department=(str(raw.get("department") or "").strip() or None),
        owner=(str(raw.get("owner") or "").strip() or None),
        judgment_note=str(raw.get("judgment_note") or "").strip(),
        security_level=str(raw.get("security_level") or "normal"),
        origin=str(raw.get("origin") or origin),
        origin_note=str(raw.get("origin_note") or ""),
    )
    for flag in ("has_data_entry", "has_transcription", "has_judgment",
                 "is_person_dependent"):
        if flag in raw:
            setattr(record, flag, to_bool(raw[flag]))

    metrics = raw.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise ValueError("metrics はオブジェクトにしてください")
    for key, spec in metrics.items():
        if isinstance(spec, dict):
            value = to_number(spec.get("value"))
            source = normalize_source(spec.get("source"), default="declared")
            evidence = str(spec.get("evidence") or "")
        else:
            # 数値だけ書かれている場合は「申告」扱いにし、そう明示する
            value = to_number(spec)
            source = "declared"
            evidence = "出所の記載が無いため申告として扱いました"
        if value is not None:
            record.metrics[key] = (value, source, evidence)

    for tool in raw.get("tools") or []:
        if isinstance(tool, str):
            record.tools.append(ToolRecord(name=tool))
            continue
        record.tools.append(ToolRecord(
            name=str(tool.get("name") or "").strip(),
            kind=tool.get("kind") or None,
            role=tool.get("role"),
            has_api=int(tool.get("has_api", 2)),
            has_csv_io=int(tool.get("has_csv_io", 2)),
            vendor=tool.get("vendor"),
            url_domain=tool.get("url_domain"),
        ))

    for i, step in enumerate(raw.get("steps") or [], start=1):
        if isinstance(step, str):
            record.steps.append(StepRecord(seq=i, action=step))
            continue
        record.steps.append(StepRecord(
            seq=int(step.get("seq", i)), action=str(step.get("action") or ""),
            tool_name=step.get("tool"), input_info=str(step.get("input") or ""),
            output_info=str(step.get("output") or ""), note=str(step.get("note") or ""),
        ))

    for issue in raw.get("issues") or []:
        if isinstance(issue, str):
            record.issues.append({"kind": "problem", "description": issue})
        else:
            record.issues.append({
                "kind": str(issue.get("kind") or "problem"),
                "description": str(issue.get("description") or ""),
            })
    return record
