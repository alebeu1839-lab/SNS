"""自動化候補の評価とランキングの検証。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from worklens.analysis.automation import build_candidates, estimate_feasibility
from worklens.analysis.pattern import Cluster, Occurrence
from worklens.analysis.sessionizer import Segment
from worklens.analysis.task_inference import TaskDraft

BASE = datetime(2025, 5, 1, 9, 0, tzinfo=timezone.utc)


def _task(category, count, avg_sec, contexts, cv=0.2, name="テスト業務"):
    occurrences = []
    for i in range(count):
        segs = []
        offset = i * (avg_sec + 300)
        per_step = max(1, avg_sec // len(contexts))
        for j, (context, app_category, domain, action) in enumerate(contexts):
            seg = Segment(
                session_id="S1",
                started_at=BASE + timedelta(seconds=offset + j * per_step),
                ended_at=BASE + timedelta(seconds=offset + (j + 1) * per_step),
                app_name=context, app_category=app_category, domain=domain, context=context,
            )
            if action == "コピー":
                seg.copies = 1
            elif action == "貼り付け":
                seg.pastes = 1
            elif action == "入力":
                seg.keystrokes = per_step * 2
            segs.append(seg)
        occurrences.append(Occurrence(segs))
    cluster = Cluster(
        signature=tuple(s.token for s in occurrences[0].segments),
        occurrences=occurrences, is_repetitive=count >= 3,
    )
    return TaskDraft(
        name=name, summary="要約", category=category,
        steps=[{"action": a, "app_name": c, "detail": ""} for c, _, _, a in contexts],
        apps=[c for c, _, _, _ in contexts], cluster=cluster,
    )


TRANSFER_STEPS = [
    ("社内管理システム", "business_system", "kanri.example.co.jp", "コピー"),
    ("掲載サイト", "listing_site", "keisai.example-portal.jp", "貼り付け"),
]
MEETING_STEPS = [("Teams", "chat", None, "確認")]


def test_repetitive_transfer_scores_higher_than_meeting():
    transfer = _task("データ転記", 40, 300, TRANSFER_STEPS)
    meeting = _task("会議・打合せ", 10, 2400, MEETING_STEPS, name="打合せ")
    f_transfer, _ = estimate_feasibility(transfer)
    f_meeting, _ = estimate_feasibility(meeting)
    assert f_transfer > 0.8
    assert f_meeting < 0.2
    assert f_transfer > f_meeting


def test_ranking_puts_high_saving_low_difficulty_first():
    tasks = [
        _task("会議・打合せ", 10, 2400, MEETING_STEPS, name="打合せ"),
        _task("データ転記", 40, 300, TRANSFER_STEPS, name="転記作業"),
    ]
    candidates = build_candidates(tasks, days_observed=10, hourly_cost_jpy=3000)
    assert candidates[0].rank == 1
    assert candidates[0].task.name == "転記作業"
    assert candidates[0].est_saved_minutes_month > candidates[-1].est_saved_minutes_month


def test_saved_time_never_exceeds_current_time():
    tasks = [_task("データ転記", 60, 240, TRANSFER_STEPS)]
    c = build_candidates(tasks, days_observed=10)[0]
    assert 0 < c.est_saved_minutes_month < c.est_current_minutes_month


def test_meeting_requires_human_judgement_and_is_not_recommended():
    c = build_candidates([_task("会議・打合せ", 10, 2400, MEETING_STEPS)], days_observed=10)[0]
    assert c.human_judgment == "必須"
    assert "非推奨" in c.method
    assert c.est_saved_minutes_month < 60


def test_monthly_frequency_is_extrapolated_from_observed_days():
    """10営業日で40回 → 月20営業日なら80回。"""
    c = build_candidates(
        [_task("データ転記", 40, 300, TRANSFER_STEPS)],
        days_observed=10, business_days_per_month=20,
    )[0]
    assert c.frequency_per_month == 80.0
    assert c.est_minutes_per_run == 5.0


def test_cost_is_derived_from_hourly_rate():
    tasks = [_task("データ転記", 40, 300, TRANSFER_STEPS)]
    cheap = build_candidates(tasks, days_observed=10, hourly_cost_jpy=2000)[0]
    pricey = build_candidates(tasks, days_observed=10, hourly_cost_jpy=4000)[0]
    assert pricey.est_saved_cost_month_jpy == 2 * cheap.est_saved_cost_month_jpy


def test_external_write_and_mail_raise_risk_level():
    mail_task = _task(
        "書類作成・送付", 20, 400,
        [("Excel", "spreadsheet", None, "入力"), ("Outlook", "mail", None, "入力")],
    )
    read_only = _task("確認・モニタリング", 20, 200,
                      [("社内管理システム", "business_system", "kanri.example.co.jp", "確認")])
    risky = build_candidates([mail_task], days_observed=10)[0]
    safe = build_candidates([read_only], days_observed=10)[0]
    assert risky.risk_level in ("中", "高")
    assert safe.risk_level == "低"


def test_step2_spec_is_generated_for_handoff():
    c = build_candidates([_task("データ転記", 40, 300, TRANSFER_STEPS)], days_observed=10)[0]
    spec = c.step2_spec
    assert spec["task_name"]
    assert len(spec["systems"]) == 2
    assert spec["systems"][0]["role"] == "参照元"
    assert spec["systems"][1]["role"] == "書き込み先"
    assert spec["guardrails"] and spec["open_questions"]
