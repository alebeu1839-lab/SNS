"""収集 → 分析 → 候補生成 までの通し検証。"""
from __future__ import annotations


def test_collection_stores_events_and_drops_sensitive_ones(collected, repos):
    stats = collected["ingest"]
    assert stats["stored"] > 1000
    assert stats["dropped"] > 0
    reasons = stats["drop_reasons"]
    assert set(reasons) <= {"sensitive_app", "sensitive_window", "sensitive_domain"}
    # 破棄したイベントは1件もDBに入っていない
    events = repos.list_events(company_id=collected["company_id"])
    assert all("1Password" not in (e["app_name"] or "") for e in events)
    assert all("ログイン" not in (e["window_title"] or "") for e in events)


def test_analysis_discovers_the_dominant_transcription_task(analyzed, repos):
    stats = analyzed["stats"]
    assert stats["tasks"] > 0
    assert stats["candidates"] == stats["tasks"]
    assert stats["days_observed"] >= 8

    candidates = repos.list_candidates(analyzed["run_id"])
    top = candidates[0]
    # 合成データで最も多い業務は「管理システム→掲載サイトの転記」
    assert "転記" in top["task_name"]
    assert "社内管理システム" in top["task_name"]
    assert "掲載サイト" in top["task_name"]
    assert top["feasibility"] >= 0.8
    assert top["est_saved_minutes_month"] > 0


def test_every_candidate_has_all_required_fields(analyzed, repos):
    required = [
        "task_name", "task_summary", "frequency_per_month", "est_minutes_per_run",
        "feasibility", "est_saved_minutes_month", "method", "difficulty",
        "risk_level", "human_judgment", "rank",
    ]
    for c in repos.list_candidates(analyzed["run_id"]):
        for field in required:
            assert c[field] not in (None, ""), f"{field} が欠けています: {c['task_name']}"
        assert 0 < c["feasibility"] <= 1
        assert c["difficulty"] in ("低", "中", "高")
        assert c["risk_level"] in ("低", "中", "高")
        assert c["human_judgment"] in ("不要", "一部必要", "必須")


def test_ranks_are_unique_and_ordered_by_priority(analyzed, repos):
    candidates = repos.list_candidates(analyzed["run_id"])
    assert [c["rank"] for c in candidates] == list(range(1, len(candidates) + 1))
    scores = [c["priority_score"] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_tasks_carry_evidence_back_to_raw_events(analyzed, repos):
    task_id = repos.list_candidates(analyzed["run_id"])[0]["task_id"]
    task = repos.get_task(task_id)
    assert task["occurrence_count"] > 3
    assert task["steps"], "業務手順が保存されていること"
    assert task["occurrences"], "根拠となる実行履歴が保存されていること"


def test_analysis_with_all_scopes_off_produces_nothing(repos, org, home):
    """収集停止中はイベントが保存されず、分析も候補を作らない。"""
    from datetime import date, datetime, timezone

    from worklens.agent.collectors.synthetic import SyntheticCollector
    from worklens.agent.recorder import Recorder, ingest_sessions
    from worklens.agent.scopes import ALL_SCOPE_KEYS
    from worklens.analysis.pipeline import run_analysis

    for key in ALL_SCOPE_KEYS:
        repos.set_consent(org["company_id"], org["user_id"], org["device_id"], key, False)
    recorder = Recorder(repos, org["company_id"], org["user_id"], org["device_id"])
    stats = ingest_sessions(
        recorder, list(SyntheticCollector(seed=3).generate_days(5, date(2025, 5, 30)))
    )
    assert stats["stored"] == 0
    assert repos.count_events(org["company_id"]) == 0

    result = run_analysis(
        repos, org["company_id"], org["user_id"], period_days=30,
        now=datetime(2025, 5, 31, tzinfo=timezone.utc),
    )
    assert result.stats["events"] == 0
    assert repos.list_candidates(result.run_id) == []


def test_paused_recorder_stores_nothing(repos, org):
    from datetime import date

    from worklens.agent.collectors.synthetic import SyntheticCollector
    from worklens.agent.recorder import Recorder

    recorder = Recorder(repos, org["company_id"], org["user_id"], org["device_id"])
    recorder.pause()
    start, _, events = next(iter(SyntheticCollector(seed=5).generate_days(1, date(2025, 5, 30))))
    session = recorder.start_session(start.isoformat())
    stats = recorder.ingest(session, events)
    assert stats.stored == 0
    assert stats.drop_reasons["collection_paused"] == len(events)
