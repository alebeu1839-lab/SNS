"""登録系の画面とAPI（会社登録 / 業務一覧 / 業務詳細 / 取り込み）。

既存の観測系ダッシュボードとは独立したルーターにする。
複数の会社を扱うため、会社は URL のクエリ（?company=）で選ぶ。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..core.models import (
    METRIC_SPECS, ORIGIN_LABELS, SECURITY_LABELS, TOOL_KIND_LABELS, source_label,
)
from ..ingest.csv_import import COLUMN_ALIASES, parse_csv
from ..ingest.importer import import_processes
from ..ingest.json_import import parse_json
from ..ingest.records import ProcessRecord, StepRecord, ToolRecord
from ..ingest.sample_data import list_samples, load_sample
from ..storage.registry_repo import RegistryRepo
from .deps import Principal, get_principal, get_repos

router = APIRouter()


def _registry() -> RegistryRepo:
    return RegistryRepo(get_repos().conn)


def _selected_company(repo: RegistryRepo, company_id: str | None) -> dict | None:
    companies = repo.list_companies()
    if not companies:
        return None
    if company_id:
        for c in companies:
            if c["id"] == company_id:
                return c
    return companies[0]


def _base(request: Request, principal: Principal, repo: RegistryRepo,
          company_id: str | None) -> dict[str, Any]:
    companies = repo.list_companies()
    current = _selected_company(repo, company_id)
    return {
        "request": request,
        "principal": principal,
        "companies": companies,
        "company": current,
        "company_obj": repo.get_company(current["id"]) if current else None,
        "metric_specs": METRIC_SPECS,
        "security_labels": SECURITY_LABELS,
        "tool_kind_labels": TOOL_KIND_LABELS,
        "origin_labels": ORIGIN_LABELS,
    }


# ============================================================ 画面2 会社登録
@router.get("/registry", response_class=HTMLResponse)
def registry_page(
    request: Request, company: str | None = None,
    principal: Principal = Depends(get_principal),
) -> HTMLResponse:
    from .app import templates

    repo = _registry()
    ctx = _base(request, principal, repo, company)
    if ctx["company"]:
        cid = ctx["company"]["id"]
        ctx.update({
            "departments": repo.list_departments(cid),
            "staff": repo.list_staff(cid),
            "workplaces": repo.list_workplaces(cid),
            "tools": repo.list_tools(cid),
            "imports": repo.list_imports(cid),
        })
    ctx.update({
        "industries": repo.list_industries(),
        "samples": list_samples(),
        "csv_columns": COLUMN_ALIASES,
    })
    return templates.TemplateResponse(request, "registry.html", ctx)


@router.post("/api/registry/companies")
def create_company(
    name: str = Form(...),
    industry: str = Form(default=""),
    employee_count: str = Form(default=""),
    hourly_cost_jpy: str = Form(default=""),
    note: str = Form(default=""),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    repo = _registry()
    try:
        company_id = repo.upsert_company(
            name=name,
            industry=industry.strip() or None,
            employee_count=int(employee_count) if employee_count.strip() else None,
            hourly_cost_jpy=int(hourly_cost_jpy) if hourly_cost_jpy.strip() else None,
            note=note.strip() or None,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_repos().audit(f"user:{principal.user_id}", "registry.company.saved",
                      company_id, "company", company_id, {"name": name})
    return JSONResponse({"ok": True, "company_id": company_id})


@router.post("/api/registry/companies/{company_id}/master")
def add_master(
    company_id: str,
    kind: str = Form(...),
    name: str = Form(...),
    extra1: str = Form(default=""),
    extra2: str = Form(default=""),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    """部署・担当者・PC・ツールの追加。登録制なので何でも足せる。"""
    repo = _registry()
    if not repo.get_company(company_id):
        raise HTTPException(status_code=404, detail="会社が見つかりません")
    try:
        if kind == "department":
            repo.ensure_department(company_id, name)
        elif kind == "staff":
            repo.ensure_staff(company_id, name, department=extra1.strip() or None,
                              job_title=extra2.strip() or None)
        elif kind == "workplace":
            repo.ensure_workplace(company_id, name, os_name=extra1.strip() or None)
        elif kind == "tool":
            repo.ensure_tool(company_id, name, kind=extra1.strip() or "software",
                             has_api={"あり": 1, "なし": 0}.get(extra2.strip(), 2))
        else:
            raise HTTPException(status_code=400, detail=f"不明な種別です: {kind}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_repos().audit(f"user:{principal.user_id}", f"registry.{kind}.added",
                      company_id, kind, name)
    return JSONResponse({"ok": True})


# ============================================================ 画面3 業務一覧
@router.get("/processes", response_class=HTMLResponse)
def process_list(
    request: Request, company: str | None = None,
    principal: Principal = Depends(get_principal),
) -> HTMLResponse:
    from .app import templates

    repo = _registry()
    ctx = _base(request, principal, repo, company)
    processes = repo.list_processes(ctx["company"]["id"]) if ctx["company"] else []
    processes.sort(key=lambda p: p.monthly_minutes, reverse=True)
    ctx.update({
        "processes": processes,
        "total_monthly_minutes": sum(p.monthly_minutes for p in processes),
        "source_label": source_label,
    })
    return templates.TemplateResponse(request, "processes.html", ctx)


@router.get("/processes/{process_id}", response_class=HTMLResponse)
def process_detail(
    request: Request, process_id: str,
    principal: Principal = Depends(get_principal),
) -> HTMLResponse:
    from .app import templates

    repo = _registry()
    process = repo.get_process(process_id)
    if not process:
        raise HTTPException(status_code=404, detail="業務が見つかりません")
    ctx = _base(request, principal, repo, process.company_id)
    ctx.update({"p": process, "source_label": source_label})
    return templates.TemplateResponse(request, "process_detail.html", ctx)


@router.post("/api/processes")
def create_process(
    company_id: str = Form(...),
    name: str = Form(...),
    summary: str = Form(default=""),
    category: str = Form(default=""),
    department: str = Form(default=""),
    owner: str = Form(default=""),
    minutes_per_run: str = Form(default=""),
    runs_per_month: str = Form(default=""),
    metric_source: str = Form(default="declared"),
    evidence: str = Form(default=""),
    manual_ratio: str = Form(default=""),
    pc_operation_ratio: str = Form(default=""),
    data_entry_ratio: str = Form(default=""),
    transcription_fields: str = Form(default=""),
    error_rate: str = Form(default=""),
    has_data_entry: str = Form(default=""),
    has_transcription: str = Form(default=""),
    has_judgment: str = Form(default=""),
    judgment_note: str = Form(default=""),
    is_person_dependent: str = Form(default=""),
    security_level: str = Form(default="normal"),
    tools: str = Form(default=""),
    steps: str = Form(default=""),
    issues: str = Form(default=""),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    """手動入力での業務登録（画面から1件ずつ）。"""
    from ..ingest.base import normalize_source, to_number
    from ..ingest.csv_import import _split

    repo = _registry()
    if not repo.get_company(company_id):
        raise HTTPException(status_code=404, detail="会社が見つかりません")

    try:
        source = normalize_source(metric_source, default="declared")
        record = ProcessRecord(
            name=name, summary=summary, category=category,
            department=department.strip() or None, owner=owner.strip() or None,
            has_data_entry=bool(has_data_entry), has_transcription=bool(has_transcription),
            has_judgment=bool(has_judgment), judgment_note=judgment_note,
            is_person_dependent=bool(is_person_dependent),
            security_level=security_level or "normal", origin="manual",
        )
        raw_metrics = {
            "minutes_per_run": minutes_per_run, "runs_per_month": runs_per_month,
            "manual_ratio": manual_ratio, "pc_operation_ratio": pc_operation_ratio,
            "data_entry_ratio": data_entry_ratio,
            "transcription_fields": transcription_fields, "error_rate": error_rate,
        }
        for key, raw in raw_metrics.items():
            value = to_number(raw)
            if value is not None:
                record.metrics[key] = (value, source, evidence)
        record.tools = [ToolRecord(name=n) for n in _split(tools)]
        record.steps = [StepRecord(seq=i, action=a) for i, a in enumerate(_split(steps), 1)]
        record.issues = [{"kind": "problem", "description": d} for d in _split(issues)]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    problems = record.problems()
    if problems:
        raise HTTPException(status_code=400, detail=" / ".join(problems))

    result = import_processes(repo, company_id, [record], kind="manual",
                              audit=get_repos().audit)
    if result.errors:
        raise HTTPException(status_code=400, detail=" / ".join(result.errors))
    return JSONResponse({"ok": True, "process_id": result.process_ids[0]})


@router.post("/api/processes/{process_id}/delete")
def delete_process(
    process_id: str, principal: Principal = Depends(get_principal)
) -> JSONResponse:
    repo = _registry()
    process = repo.get_process(process_id)
    if not process:
        raise HTTPException(status_code=404, detail="業務が見つかりません")
    repo.delete_process(process_id)
    get_repos().audit(f"user:{principal.user_id}", "registry.process.deleted",
                      process.company_id, "process", process_id, {"name": process.name})
    return JSONResponse({"ok": True})


# ============================================================== 取り込み
@router.post("/api/registry/import/csv")
async def import_csv(
    company_id: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    repo = _registry()
    if not repo.get_company(company_id):
        raise HTTPException(status_code=404, detail="会社が見つかりません")
    raw = await file.read()
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=400, detail="文字コードを判別できませんでした")

    records, warnings = parse_csv(text, origin="csv")
    result = import_processes(repo, company_id, records, kind="csv",
                              filename=file.filename, audit=get_repos().audit)
    payload = result.as_dict()
    payload["warnings"] = warnings
    return JSONResponse(payload)


@router.post("/api/registry/import/json")
async def import_json_file(
    company_id: str = Form(default=""),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    repo = _registry()
    text = (await file.read()).decode("utf-8", errors="replace")
    company_info, records, warnings = parse_json(text, origin="json")

    if not company_id:
        if not company_info.get("name"):
            raise HTTPException(
                status_code=400,
                detail="会社を選ぶか、JSON に company.name を書いてください",
            )
        company_id = repo.upsert_company(
            name=company_info["name"], industry=company_info.get("industry"),
            employee_count=company_info.get("employee_count"),
            hourly_cost_jpy=company_info.get("hourly_cost_jpy"),
        )
    result = import_processes(repo, company_id, records, kind="json",
                              filename=file.filename, audit=get_repos().audit)
    payload = result.as_dict()
    payload["warnings"] = warnings
    payload["company_id"] = company_id
    return JSONResponse(payload)


@router.post("/api/registry/import/sample")
def import_sample(
    key: str = Form(...), principal: Principal = Depends(get_principal)
) -> JSONResponse:
    repo = _registry()
    try:
        company_id, result = load_sample(repo, key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    get_repos().audit(f"user:{principal.user_id}", "registry.sample.loaded",
                      company_id, "sample", key, result.as_dict())
    payload = result.as_dict()
    payload["company_id"] = company_id
    return JSONResponse(payload)


@router.get("/api/registry/processes")
def api_processes(
    company: str | None = None, principal: Principal = Depends(get_principal)
) -> JSONResponse:
    repo = _registry()
    current = _selected_company(repo, company)
    if not current:
        return JSONResponse({"company": None, "processes": []})
    return JSONResponse({
        "company": {"id": current["id"], "name": current["name"]},
        "processes": [p.to_dict() for p in repo.list_processes(current["id"])],
    })
