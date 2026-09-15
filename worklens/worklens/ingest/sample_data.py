"""サンプルデータの読み込み。

業種固有の知識はコードに入れない。samples/*.json を**登録データとして**
読み込むだけにし、別業種のサンプルを足すときはJSONを1つ置けば済むようにする。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..storage.registry_repo import RegistryRepo
from .base import ImportResult
from .importer import import_processes
from .json_import import parse_json

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"


def list_samples() -> list[dict]:
    """置かれているサンプルの一覧。"""
    out: list[dict] = []
    if not SAMPLES_DIR.exists():
        return out
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        company = data.get("company") or {}
        out.append({
            "key": path.stem,
            "path": str(path),
            "company": company.get("name") or path.stem,
            "industry": company.get("industry") or "",
            "processes": len(data.get("processes") or []),
        })
    return out


def load_sample(repo: RegistryRepo, key: str) -> tuple[str, ImportResult]:
    """サンプルを登録して (company_id, 結果) を返す。"""
    path = SAMPLES_DIR / f"{key}.json"
    if not path.exists():
        raise FileNotFoundError(f"サンプルがありません: {key}")

    data = json.loads(path.read_text(encoding="utf-8"))
    company_info, records, warnings = parse_json(path.read_text(encoding="utf-8"),
                                                 origin="sample")
    company_id = repo.upsert_company(
        name=company_info.get("name") or key,
        industry=company_info.get("industry"),
        employee_count=company_info.get("employee_count"),
        hourly_cost_jpy=company_info.get("hourly_cost_jpy"),
        note=company_info.get("note"),
    )
    # 業務に紐づかないマスタも登録しておく（部署・担当者・PC・ツール）
    for name in data.get("departments") or []:
        repo.ensure_department(company_id, name)
    for person in data.get("staff") or []:
        repo.ensure_staff(
            company_id, person["name"], department=person.get("department"),
            job_title=person.get("job_title"), email=person.get("email"),
        )
    for place in data.get("workplaces") or []:
        repo.ensure_workplace(company_id, place["name"], os_name=place.get("os"))
    for tool in data.get("tools") or []:
        repo.ensure_tool(
            company_id, tool["name"], kind=tool.get("kind", "software"),
            vendor=tool.get("vendor"), has_api=int(tool.get("has_api", 2)),
            has_csv_io=int(tool.get("has_csv_io", 2)),
            url_domain=tool.get("url_domain"),
        )

    result = import_processes(repo, company_id, records, kind="sample",
                              filename=path.name)
    result.errors.extend(warnings)
    return company_id, result
