"""STEP1 → STEP2 の受け渡しの検証。"""
from __future__ import annotations

import pytest

from worklens.step2.cli import _resolve_endpoints


def test_step1_produces_a_usable_spec_for_step2(analyzed, repos):
    """候補の step2_spec が、そのまま実行の入力として使えること。"""
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    spec = repos.get_candidate(candidate["id"])["step2_spec"]

    assert spec["task_name"]
    assert spec["trigger"]
    roles = {s["role"] for s in spec["systems"]}
    assert roles == {"参照元", "書き込み先"}
    assert spec["guardrails"] and spec["open_questions"]

    mapping = {
        s["domain"]: f"http://127.0.0.1:910{i}"
        for i, s in enumerate(spec["systems"], start=1)
    }
    endpoints = _resolve_endpoints(spec, mapping)
    assert endpoints["source"].endswith("9101")
    assert endpoints["target"].endswith("9102")


def test_endpoints_must_be_mapped_explicitly(analyzed, repos):
    """本番ドメインへ勝手に接続しないこと。

    接続先の解決は best-effort（単一システムの業務では片方しか無い）。
    足りているかの判断はレシピが行い、足りなければ実行前に止まる。
    """
    from worklens.step2 import registry

    registry.bootstrap()
    candidate = repos.get_candidate(repos.list_candidates(analyzed["run_id"])[0]["id"])
    spec = candidate["step2_spec"]

    endpoints = _resolve_endpoints(spec, {})
    assert endpoints == {}, "--map が無ければ接続先は1つも解決されない"

    entry = registry.select(candidate)
    with pytest.raises(SystemExit) as exc:
        entry.factory(spec=spec, endpoints=endpoints, browser=None)
    assert "--map" in str(exc.value)


def test_execution_is_recorded_for_effect_measurement(repos, analyzed):
    """STEP3（効果測定）のために、実行結果が残ること。"""
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    for mode, succeeded, saved in (("dry-run", 9, 48.6), ("live", 9, 48.6), ("live", 4, 21.6)):
        repos.record_execution({
            "company_id": analyzed["company_id"], "candidate_id": candidate["id"],
            "task_id": candidate["task_id"], "recipe": "vehicle_transfer", "mode": mode,
            "status": "succeeded", "processed": succeeded + 3, "succeeded": succeeded,
            "handoff": 3, "failed": 0, "saved_minutes": saved,
            "log_path": "/tmp/x.jsonl", "detail": {"items": []},
            "started_at": "2025-06-01T09:00:00+09:00",
        })
    executions = repos.list_executions(candidate_id=candidate["id"])
    assert len(executions) == 3
    assert executions[0]["started_at"] == "2025-06-01T00:00:00+00:00"

    totals = repos.execution_totals(analyzed["company_id"])
    assert totals["runs"] == 2                     # 本番実行のみ集計する
    assert totals["succeeded"] == 13
    assert totals["saved_minutes"] == pytest.approx(70.2)


def test_purging_company_data_removes_execution_history(repos, analyzed):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    repos.record_execution({
        "company_id": analyzed["company_id"], "candidate_id": candidate["id"],
        "task_id": candidate["task_id"], "recipe": "vehicle_transfer", "mode": "live",
        "status": "succeeded", "succeeded": 1, "started_at": "2025-06-01T00:00:00+00:00",
    })
    counts = repos.purge_company_data(analyzed["company_id"])
    assert counts["automation_executions"] == 1
    assert repos.list_executions(company_id=analyzed["company_id"]) == []
