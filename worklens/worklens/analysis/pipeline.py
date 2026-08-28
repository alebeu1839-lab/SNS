"""分析パイプライン: 収集データ → 業務 → 自動化候補 → 保存。

  events → Sessionizer → pattern.mine → task_inference → automation → DB
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings, get_settings
from ..storage.repositories import Repositories
from .automation import build_candidates
from .llm import ClaudeClient
from .pattern import mine
from .sessionizer import Sessionizer, group_events_by_session
from .task_inference import infer_tasks


@dataclass
class AnalysisResult:
    run_id: str
    engine: str
    stats: dict[str, Any]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _count_workdays(sessions: list[dict], tz_name: str) -> int:
    """観測できた稼働日数。UTC保存の時刻を現地時間へ直してから日付を数える。"""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    days = set()
    for s in sessions:
        try:
            days.add(datetime.fromisoformat(s["started_at"]).astimezone(tz).date())
        except ValueError:
            continue
    return len(days)


def run_analysis(
    repos: Repositories,
    company_id: str,
    user_id: str,
    period_days: int = 30,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> AnalysisResult:
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    period_end = _iso(now)
    period_start = _iso(now - timedelta(days=period_days))

    client = ClaudeClient(settings.anthropic_api_key, settings.model, settings.llm_timeout_sec)
    engine_hint = f"llm:{settings.model}" if client.available else "rule-based"
    run_id = repos.start_run(company_id, user_id, period_start, period_end, engine_hint)

    try:
        sessions = repos.list_sessions(user_id, period_start, period_end)
        session_ids = {s["id"] for s in sessions}
        events = [
            e
            for e in repos.list_events(
                company_id=company_id, period_start=period_start, period_end=period_end
            )
            if e["session_id"] in session_ids
        ]
        if not events:
            stats = {"events": 0, "note": "対象期間に収集データがありません"}
            repos.finish_run(run_id, stats)
            return AnalysisResult(run_id, engine_hint, stats)

        # --- ① セグメント化 -------------------------------------------
        sessionizer = Sessionizer(idle_gap_sec=settings.idle_gap_sec)
        grouped = group_events_by_session(events)
        segments_by_session = {sid: sessionizer.build(evs) for sid, evs in grouped.items()}

        # --- ② 繰り返しパターンの抽出 ---------------------------------
        clusters, mine_stats = mine(segments_by_session, min_support=settings.min_repetition)

        # --- ③ 業務単位への変換 ---------------------------------------
        days_observed = max(1, _count_workdays(sessions, settings.timezone))
        tasks, engine = infer_tasks(clusters, days_observed, client)

        # --- ④ 自動化候補の評価 ---------------------------------------
        company = repos.get_company(company_id) or {}
        hourly = int(company.get("hourly_cost_jpy", 3500))
        candidates = build_candidates(
            tasks,
            days_observed=days_observed,
            business_days_per_month=settings.business_days_per_month,
            hourly_cost_jpy=hourly,
            client=client,
        )

        # --- ⑤ 保存 ----------------------------------------------------
        task_ids: dict[int, str] = {}
        for i, task in enumerate(tasks):
            occurrences = [
                {
                    "session_id": o.segments[0].session_id,
                    "started_at": o.started_at.isoformat(),
                    "ended_at": o.ended_at.isoformat(),
                    "duration_sec": o.duration_sec,
                    "evidence": [eid for s in o.segments for eid in s.event_ids[:5]],
                }
                for o in task.cluster.occurrences[:200]
            ]
            task_ids[i] = repos.create_task(
                {
                    "company_id": company_id,
                    "user_id": user_id,
                    "run_id": run_id,
                    "name": task.name,
                    "summary": task.summary,
                    "category": task.category,
                    "apps": task.apps,
                    "occurrence_count": task.count,
                    "avg_duration_sec": task.avg_duration_sec,
                    "total_duration_sec": task.total_duration_sec,
                    "per_day_count": round(task.count / days_observed, 2),
                    "is_repetitive": task.cluster.is_repetitive,
                    "repetition_score": task.cluster.repetition_score,
                    "confidence": task.confidence,
                    "inference_source": task.inference_source,
                    "first_seen_at": task.cluster.occurrences[0].started_at.isoformat()
                    if task.cluster.occurrences else None,
                    "last_seen_at": task.cluster.occurrences[-1].ended_at.isoformat()
                    if task.cluster.occurrences else None,
                },
                task.steps,
                occurrences,
            )

        index_of_task = {id(t): i for i, t in enumerate(tasks)}
        repos.create_candidates(
            [
                {
                    "company_id": company_id,
                    "task_id": task_ids[index_of_task[id(c.task)]],
                    "run_id": run_id,
                    "rank": c.rank,
                    "feasibility": c.feasibility,
                    "method": c.method,
                    "method_detail": c.method_detail,
                    "tools": c.tools,
                    "difficulty": c.difficulty,
                    "difficulty_score": c.difficulty_score,
                    "risk_level": c.risk_level,
                    "risk_notes": c.risk_notes,
                    "human_judgment": c.human_judgment,
                    "human_judgment_ratio": c.human_judgment_ratio,
                    "frequency_per_month": c.frequency_per_month,
                    "est_minutes_per_run": c.est_minutes_per_run,
                    "est_current_minutes_month": c.est_current_minutes_month,
                    "est_saved_minutes_month": c.est_saved_minutes_month,
                    "est_saved_cost_month_jpy": c.est_saved_cost_month_jpy,
                    "priority_score": c.priority_score,
                    "rationale": c.rationale,
                    "step2_spec": c.step2_spec,
                }
                for c in candidates
            ]
        )

        # 総作業時間は2種類出す:
        #   active = アプリ使用として実測できた時間（分析の母数）
        #   span   = 作業開始〜終了の在席時間（離席も含む）
        total_active_sec = sum(
            r["total_sec"] for r in repos.app_usage_totals(company_id, period_start, period_end)
        )
        total_span_sec = 0
        for s in sessions:
            if s.get("ended_at"):
                total_span_sec += int(
                    (
                        datetime.fromisoformat(s["ended_at"])
                        - datetime.fromisoformat(s["started_at"])
                    ).total_seconds()
                )

        stats = {
            "events": len(events),
            "sessions": len(sessions),
            "days_observed": days_observed,
            "segments": mine_stats.get("segments", 0),
            "blocks": mine_stats.get("blocks", 0),
            "block_gap_sec": mine_stats.get("block_gap_sec", 0),
            "clusters": len(clusters),
            "tasks": len(tasks),
            "repetitive_tasks": sum(1 for t in tasks if t.cluster.is_repetitive),
            "candidates": len(candidates),
            "engine": engine,
            "total_active_sec": total_active_sec,
            "total_span_sec": total_span_sec,
            "total_saved_minutes_month": round(
                sum(c.est_saved_minutes_month for c in candidates), 1
            ),
            "total_saved_cost_month_jpy": sum(c.est_saved_cost_month_jpy for c in candidates),
            "hourly_cost_jpy": hourly,
        }
        repos.finish_run(run_id, stats)
        repos.audit("system", "analysis.completed", company_id, "run", run_id, stats)
        return AnalysisResult(run_id, engine, stats)

    except Exception as exc:
        repos.fail_run(run_id, f"{exc}\n{traceback.format_exc()}")
        repos.audit("system", "analysis.failed", company_id, "run", run_id, {"error": str(exc)})
        raise
