"""ダッシュボード（FastAPI + サーバサイドレンダリング）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..agent.scopes import SCOPES, SCOPES_BY_KEY, scope
from ..analysis.pipeline import run_analysis
from ..appcatalog import CATEGORY_LABELS
from ..config import get_settings
from ..storage.repositories import Repositories
from .deps import Principal, get_principal, get_repos, require

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="WorkLens", description="業務分析・自動化候補発見システム (STEP1)")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

DECISIONS = {
    "automate": "自動化したい",
    "hold": "今回は保留",
    "exclude": "対象外",
}


# ------------------------------------------------------------ フィルタ
def fmt_minutes(value: float | None) -> str:
    if not value:
        return "0分"
    value = float(value)
    if value < 60:
        return f"{value:.0f}分"
    return f"{value / 60:.1f}時間"


def fmt_sec(value: float | None) -> str:
    return fmt_minutes((value or 0) / 60)


def fmt_yen(value: int | None) -> str:
    return f"{int(value or 0):,}円"


def _display_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(get_settings().timezone)
    except Exception:
        return timezone.utc


def fmt_date(value: str | None) -> str:
    """保存はUTC、表示は企業のタイムゾーンで行う。"""
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_display_tz()).strftime("%Y/%m/%d %H:%M")


templates.env.filters["minutes"] = fmt_minutes
templates.env.filters["sec"] = fmt_sec
templates.env.filters["yen"] = fmt_yen
def from_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


templates.env.filters["dt"] = fmt_date
templates.env.filters["from_json"] = from_json


def _base_context(request: Request, principal: Principal, repos: Repositories) -> dict[str, Any]:
    company = repos.get_company(principal.company_id) or {}
    return {
        "request": request,
        "principal": principal,
        "company": company,
        "decisions": DECISIONS,
    }


def _latest_run(repos: Repositories, company_id: str) -> dict | None:
    run = repos.latest_run(company_id)
    if run and run.get("stats_json"):
        run["stats"] = json.loads(run["stats_json"])
    elif run:
        run["stats"] = {}
    return run


# ------------------------------------------------------------ 画面
@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> HTMLResponse:
    repos = get_repos()
    run = _latest_run(repos, principal.company_id)
    ctx = _base_context(request, principal, repos)
    if not run:
        return templates.TemplateResponse(request, "empty.html", ctx)

    candidates = repos.list_candidates(run["id"])
    tasks = repos.list_tasks(run["id"])
    stats = run["stats"]
    decided = repos.decision_summary(run["id"])

    by_category: dict[str, dict[str, float]] = {}
    for t in tasks:
        row = by_category.setdefault(t["category"], {"count": 0, "sec": 0})
        row["count"] += 1
        row["sec"] += t["total_duration_sec"]

    ctx.update(
        {
            "run": run,
            "stats": stats,
            "candidates": candidates,
            "top_candidates": candidates[:5],
            "tasks": tasks,
            "by_category": sorted(
                by_category.items(), key=lambda kv: kv[1]["sec"], reverse=True
            ),
            "decided": decided,
            "total_saved_minutes": sum(c["est_saved_minutes_month"] for c in candidates),
            "total_saved_cost": sum(c["est_saved_cost_month_jpy"] for c in candidates),
            "repetitive_count": sum(1 for t in tasks if t["is_repetitive"]),
        }
    )
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/candidates", response_class=HTMLResponse)
def candidate_list(
    request: Request,
    sort: str = "rank",
    decision: str = "",
    principal: Principal = Depends(get_principal),
) -> HTMLResponse:
    repos = get_repos()
    run = _latest_run(repos, principal.company_id)
    ctx = _base_context(request, principal, repos)
    if not run:
        return templates.TemplateResponse(request, "empty.html", ctx)

    candidates = repos.list_candidates(run["id"])
    if decision:
        candidates = [c for c in candidates if (c["decision"] or "undecided") == decision]
    keys = {
        "rank": lambda c: c["rank"],
        "saved": lambda c: -c["est_saved_minutes_month"],
        "feasibility": lambda c: -c["feasibility"],
        "frequency": lambda c: -c["frequency_per_month"],
        "difficulty": lambda c: c["difficulty_score"],
    }
    candidates.sort(key=keys.get(sort, keys["rank"]))
    ctx.update({"run": run, "candidates": candidates, "sort": sort, "filter_decision": decision})
    return templates.TemplateResponse(request, "candidates.html", ctx)


@app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
def candidate_detail(
    request: Request,
    candidate_id: str,
    principal: Principal = Depends(get_principal),
) -> HTMLResponse:
    repos = get_repos()
    candidate = repos.get_candidate(candidate_id)
    if not candidate or candidate["company_id"] != principal.company_id:
        raise HTTPException(status_code=404, detail="候補が見つかりません")
    ctx = _base_context(request, principal, repos)
    ctx.update(
        {
            "c": candidate,
            "task": candidate["task"],
            "step2_spec": json.dumps(candidate["step2_spec"], ensure_ascii=False, indent=2),
        }
    )
    return templates.TemplateResponse(request, "candidate_detail.html", ctx)


@app.get("/tasks", response_class=HTMLResponse)
def task_list(
    request: Request, principal: Principal = Depends(get_principal)
) -> HTMLResponse:
    repos = get_repos()
    run = _latest_run(repos, principal.company_id)
    ctx = _base_context(request, principal, repos)
    if not run:
        return templates.TemplateResponse(request, "empty.html", ctx)
    tasks = repos.list_tasks(run["id"])
    candidates = {c["task_id"]: c for c in repos.list_candidates(run["id"])}
    ctx.update({"run": run, "tasks": tasks, "candidates": candidates})
    return templates.TemplateResponse(request, "tasks.html", ctx)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(
    request: Request, principal: Principal = Depends(get_principal)
) -> HTMLResponse:
    """「何を収集しているか」をユーザーが確認できる画面。"""
    repos = get_repos()
    devices = repos.list_devices(principal.user_id)
    device_id = devices[0]["id"] if devices else None
    consent = repos.consent_map(principal.user_id, device_id)
    breakdown = repos.event_type_breakdown(principal.company_id)
    samples = repos.list_events(company_id=principal.company_id, limit=25)
    ctx = _base_context(request, principal, repos)
    ctx.update(
        {
            "scopes": SCOPES,
            "consent": consent,
            "devices": devices,
            "breakdown": breakdown,
            "samples": samples,
            "redactions": repos.redaction_summary(principal.company_id),
            "event_count": repos.count_events(principal.company_id),
            "sessions": repos.list_sessions(principal.user_id),
            "collection_active": any(consent.values()),
        }
    )
    return templates.TemplateResponse(request, "privacy.html", ctx)


@app.get("/audit", response_class=HTMLResponse)
def audit(
    request: Request, principal: Principal = Depends(get_principal)
) -> HTMLResponse:
    repos = get_repos()
    require(principal, "manager")
    ctx = _base_context(request, principal, repos)
    ctx.update(
        {
            "logs": repos.list_audit(principal.company_id, 300),
            "runs": repos.list_runs(principal.company_id),
        }
    )
    return templates.TemplateResponse(request, "audit.html", ctx)


# ------------------------------------------------------------ 操作API
@app.post("/api/candidates/{candidate_id}/decision")
def set_decision(
    candidate_id: str,
    decision: str = Form(...),
    note: str = Form(default=""),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    """ユーザーによる選択（自動化したい / 保留 / 対象外）を保存する。

    STEP1 では保存のみ。自動化処理の実行は STEP2 で扱う。
    """
    repos = get_repos()
    if decision not in DECISIONS and decision != "clear":
        raise HTTPException(status_code=400, detail="不正な選択です")
    candidate = repos.get_candidate(candidate_id)
    if not candidate or candidate["company_id"] != principal.company_id:
        raise HTTPException(status_code=404, detail="候補が見つかりません")

    if decision == "clear":
        repos.clear_decision(candidate_id)
    else:
        repos.set_decision(
            principal.company_id, candidate_id, candidate["task_id"],
            principal.user_id, decision, note or None,
        )
    repos.audit(
        f"user:{principal.user_id}", "candidate.decision", principal.company_id,
        "candidate", candidate_id, {"decision": decision, "note": note},
    )
    return JSONResponse(
        {
            "ok": True,
            "candidate_id": candidate_id,
            "decision": None if decision == "clear" else decision,
            "label": DECISIONS.get(decision),
        }
    )


@app.post("/api/consent")
def update_consent(
    scope_key: str = Form(...),
    enabled: str = Form(...),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    repos = get_repos()
    if scope_key not in SCOPES_BY_KEY:
        raise HTTPException(status_code=400, detail="不正な収集項目です")
    devices = repos.list_devices(principal.user_id)
    device_id = devices[0]["id"] if devices else None
    value = enabled in ("1", "true", "on", "True")
    repos.set_consent(principal.company_id, principal.user_id, device_id, scope_key, value)
    repos.audit(
        f"user:{principal.user_id}", "consent.changed", principal.company_id,
        "scope", scope_key, {"enabled": value},
    )
    return JSONResponse(
        {"ok": True, "scope_key": scope_key, "enabled": value,
         "label": scope(scope_key).label}
    )


@app.post("/api/collection/stop")
def stop_collection(principal: Principal = Depends(get_principal)) -> JSONResponse:
    """収集停止ボタン。全スコープを OFF にする。"""
    repos = get_repos()
    devices = repos.list_devices(principal.user_id)
    device_id = devices[0]["id"] if devices else None
    for s in SCOPES:
        repos.set_consent(principal.company_id, principal.user_id, device_id, s.key, False)
    repos.audit(
        f"user:{principal.user_id}", "collection.stopped_all", principal.company_id,
        "device", device_id,
    )
    return JSONResponse({"ok": True, "stopped": [s.key for s in SCOPES]})


@app.post("/api/collection/start")
def start_collection(principal: Principal = Depends(get_principal)) -> JSONResponse:
    repos = get_repos()
    devices = repos.list_devices(principal.user_id)
    device_id = devices[0]["id"] if devices else None
    for s in SCOPES:
        repos.set_consent(
            principal.company_id, principal.user_id, device_id, s.key, s.default_enabled
        )
    repos.audit(
        f"user:{principal.user_id}", "collection.resumed", principal.company_id,
        "device", device_id,
    )
    return JSONResponse({"ok": True})


@app.post("/api/data/purge")
def purge(
    target: str = Form(default="user"),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    """データ削除機能。company 単位の削除は admin のみ。"""
    repos = get_repos()
    if target == "company":
        require(principal, "admin")
        counts = repos.purge_company_data(principal.company_id)
    else:
        counts = repos.purge_user_data(principal.user_id)
    repos.audit(
        f"user:{principal.user_id}", f"data.purged.{target}", principal.company_id,
        detail=counts,
    )
    return JSONResponse({"ok": True, "deleted": counts})


@app.post("/api/analysis/run")
def trigger_analysis(
    period_days: int = Form(default=30),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    repos = get_repos()
    result = run_analysis(
        repos, principal.company_id, principal.user_id,
        period_days=period_days, settings=get_settings(),
        now=datetime.now(timezone.utc),
    )
    return JSONResponse({"ok": True, "run_id": result.run_id, "stats": result.stats})


@app.get("/api/candidates")
def api_candidates(principal: Principal = Depends(get_principal)) -> JSONResponse:
    repos = get_repos()
    run = _latest_run(repos, principal.company_id)
    if not run:
        return JSONResponse({"run": None, "candidates": []})
    return JSONResponse(
        {
            "run": {"id": run["id"], "engine": run["engine"], "stats": run["stats"]},
            "candidates": repos.list_candidates(run["id"]),
        }
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
