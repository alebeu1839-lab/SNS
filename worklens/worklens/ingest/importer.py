"""ProcessRecord を登録する共通の入口。

CSV も JSON も手動入力もサンプルも、ここを通る。
1件が壊れていても他は登録する（全部落とすと、大きいCSVで何も入らない）。
壊れた行は理由つきで errors に残し、画面へ出す。
"""
from __future__ import annotations

from typing import Callable, Sequence

from ..storage.registry_repo import RegistryRepo
from .base import ImportResult
from .records import ProcessRecord

AuditFn = Callable[..., None]


def import_processes(
    repo: RegistryRepo,
    company_id: str,
    records: Sequence[ProcessRecord],
    kind: str,
    filename: str | None = None,
    audit: AuditFn | None = None,
) -> ImportResult:
    result = ImportResult()
    existing = {p.name for p in repo.list_processes(company_id)}

    for index, record in enumerate(records, start=1):
        problems = record.problems()
        if problems:
            result.skipped += 1
            result.errors.append(
                f"{index}件目「{record.name or '(名称なし)'}」: " + " / ".join(problems)
            )
            continue
        try:
            is_new = record.name not in existing
            process_id = repo.upsert_process(
                company_id=company_id, name=record.name, summary=record.summary,
                category=record.category, department=record.department, owner=record.owner,
                has_data_entry=record.has_data_entry,
                has_transcription=record.has_transcription,
                has_judgment=record.has_judgment, judgment_note=record.judgment_note,
                is_person_dependent=record.is_person_dependent,
                security_level=record.security_level, origin=record.origin,
                origin_note=record.origin_note,
            )
            for key, (value, source, evidence) in record.metrics.items():
                repo.set_metric(process_id, key, value, source, evidence=evidence)

            name_to_id: dict[str, str] = {}
            tool_entries = []
            for tool in record.tools:
                tool_id = repo.ensure_tool(
                    company_id, tool.name, kind=tool.kind, vendor=tool.vendor,
                    has_api=tool.has_api, has_csv_io=tool.has_csv_io,
                    url_domain=tool.url_domain,
                )
                name_to_id[tool.name] = tool_id
                tool_entries.append({"tool_id": tool_id, "role": tool.role})
            if tool_entries:
                repo.set_process_tools(process_id, tool_entries)

            if record.steps:
                repo.set_process_steps(process_id, [
                    {
                        "seq": s.seq, "action": s.action,
                        "tool_id": name_to_id.get(s.tool_name or ""),
                        "input_info": s.input_info, "output_info": s.output_info,
                        "note": s.note,
                    }
                    for s in record.steps
                ])
            if record.issues:
                repo.set_process_issues(process_id, record.issues)

            result.process_ids.append(process_id)
            if is_new:
                result.created += 1
                existing.add(record.name)
            else:
                result.updated += 1
        except Exception as exc:
            result.skipped += 1
            result.errors.append(f"{index}件目「{record.name}」: {type(exc).__name__}: {exc}")

    repo.record_import(
        company_id, kind, filename, result.created, result.updated,
        result.skipped, result.errors,
    )
    if audit:
        audit("system", f"import.{kind}", company_id, "import", filename, result.as_dict())
    return result
