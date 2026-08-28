"""ダッシュボードと選択保存APIの検証。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(analyzed):
    from worklens.api.app import app

    return TestClient(app)


def test_all_pages_render(client, analyzed):
    for path in ("/", "/candidates", "/tasks", "/privacy", "/audit"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "WorkLens" in res.text


def test_dashboard_shows_required_metrics(client):
    body = client.get("/").text
    for label in (
        "分析期間", "総作業時間", "発見した業務数", "自動化候補数",
        "推定削減可能時間", "推定削減コスト",
    ):
        assert label in body


def test_candidate_list_shows_required_columns(client):
    body = client.get("/candidates").text
    for label in (
        "業務名", "発生頻度", "推定作業時間", "自動化可能性", "推定削減時間",
        "推奨する自動化方法", "難易度", "リスク", "人の判断", "優先順位",
    ):
        assert label in body
    for label in ("自動化したい", "保留", "対象外"):
        assert label in body


def test_candidate_detail_is_reachable_and_complete(client, analyzed, repos):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    res = client.get(f"/candidates/{candidate['id']}")
    assert res.status_code == 200
    assert candidate["task_name"] in res.text
    assert "現在の作業内容" in res.text
    assert "推奨する自動化方法" in res.text
    assert "STEP2 へ引き継ぐ仕様" in res.text


def test_decision_is_saved_and_reflected(client, analyzed, repos):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    res = client.post(
        f"/api/candidates/{candidate['id']}/decision",
        data={"decision": "automate", "note": "まず転記から着手"},
    )
    assert res.status_code == 200
    assert res.json()["label"] == "自動化したい"

    stored = repos.get_candidate(candidate["id"])
    assert stored["decision"] == "automate"
    assert stored["decision_note"] == "まず転記から着手"
    assert "自動化したい" in client.get("/candidates?decision=automate").text


def test_decision_can_be_cleared(client, analyzed, repos):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    client.post(f"/api/candidates/{candidate['id']}/decision", data={"decision": "hold"})
    client.post(f"/api/candidates/{candidate['id']}/decision", data={"decision": "clear"})
    assert repos.get_candidate(candidate["id"])["decision"] is None


def test_invalid_decision_is_rejected(client, analyzed, repos):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    res = client.post(f"/api/candidates/{candidate['id']}/decision", data={"decision": "delete"})
    assert res.status_code == 400


def test_privacy_page_lists_what_is_and_is_not_collected(client):
    body = client.get("/privacy").text
    assert "収集する" in body and "収集しない" in body
    assert "ウィンドウタイトル" in body
    assert "パスワード入力画面のタイトル" in body
    assert "クリップボードの内容" in body
    assert "収集せずに破棄した件数" in body


def test_consent_can_be_toggled_from_the_dashboard(client, analyzed, repos):
    res = client.post("/api/consent", data={"scope_key": "browser_usage", "enabled": "0"})
    assert res.status_code == 200 and res.json()["enabled"] is False
    devices = repos.list_devices(analyzed["user_id"])
    assert repos.consent_map(analyzed["user_id"], devices[0]["id"])["browser_usage"] is False


def test_stop_button_disables_every_scope(client, analyzed, repos):
    client.post("/api/collection/stop")
    devices = repos.list_devices(analyzed["user_id"])
    consent = repos.consent_map(analyzed["user_id"], devices[0]["id"])
    assert not any(consent.values())
    assert "すべての収集が停止しています" in client.get("/privacy").text

    client.post("/api/collection/start")
    assert any(repos.consent_map(analyzed["user_id"], devices[0]["id"]).values())


def test_purge_deletes_collected_data(client, analyzed, repos):
    assert repos.count_events(analyzed["company_id"]) > 0
    res = client.post("/api/data/purge", data={"target": "user"})
    assert res.status_code == 200
    assert repos.count_events(analyzed["company_id"]) == 0


def test_member_role_cannot_open_audit_log(client, repos, analyzed):
    member = repos.create_user(analyzed["company_id"], "member@example.co.jp", "一般社員")
    res = client.get("/audit", headers={"X-WorkLens-User": member})
    assert res.status_code == 403
    res = client.post(
        "/api/data/purge", data={"target": "company"}, headers={"X-WorkLens-User": member}
    )
    assert res.status_code == 403


def test_candidate_of_another_company_is_not_visible(client, repos, analyzed):
    other_company = repos.create_company("別会社")
    other_user = repos.create_user(other_company, "x@y.jp", "他社ユーザー", role="admin")
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    res = client.get(
        f"/candidates/{candidate['id']}", headers={"X-WorkLens-User": other_user}
    )
    assert res.status_code == 404


def test_analysis_can_be_triggered_from_the_dashboard(client):
    # 分析期間は「今日から遡ってN日」。テストデータは固定日付のため広めに取る。
    res = client.post("/api/analysis/run", data={"period_days": 3650})
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"]
    assert body["stats"]["tasks"] > 0


def test_analysis_outside_the_period_reports_no_data(client):
    res = client.post("/api/analysis/run", data={"period_days": 1})
    assert res.status_code == 200
    assert res.json()["stats"]["events"] == 0
