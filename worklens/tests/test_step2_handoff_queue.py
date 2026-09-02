"""引き継ぎキュー（人へ回した件）の検証。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def with_handoffs(repos, analyzed):
    candidate = repos.list_candidates(analyzed["run_id"])[0]
    execution_id = repos.record_execution({
        "company_id": analyzed["company_id"], "candidate_id": candidate["id"],
        "task_id": candidate["task_id"], "recipe": "web_transfer", "mode": "live",
        "status": "succeeded", "processed": 12, "succeeded": 9, "handoff": 3,
        "failed": 0, "saved_minutes": 48.6, "started_at": "2025-06-01T00:00:00+00:00",
    })
    added = repos.record_handoffs(
        company_id=analyzed["company_id"], execution_id=execution_id,
        candidate_id=candidate["id"], task_id=candidate["task_id"],
        items=[
            {"item_key": "48204", "reason": "価格が数値ではありません（応談）",
             "context": {"maker": "レクサス"}},
            {"item_key": "48206", "reason": "走行距離が未入力です", "context": {}},
            {"item_key": "48208", "reason": "備考に確認事項があります", "context": {}},
        ],
    )
    return {**analyzed, "candidate": candidate, "execution_id": execution_id, "added": added}


def test_handoffs_are_stored_with_their_reason(repos, with_handoffs):
    assert with_handoffs["added"] == 3
    rows = repos.list_handoffs(with_handoffs["company_id"])
    assert len(rows) == 3
    assert all(r["reason"] for r in rows), "理由の無い引き継ぎを作らない"
    assert all(r["status"] == "open" for r in rows)
    lexus = next(r for r in rows if r["item_key"] == "48204")
    assert lexus["context"] == {"maker": "レクサス"}
    assert lexus["task_name"]


def test_rerunning_does_not_pile_up_the_same_item(repos, with_handoffs):
    """同じ対象が毎回引き継がれても、未処理は1件のままにする。"""
    repos.record_handoffs(
        company_id=with_handoffs["company_id"], execution_id=with_handoffs["execution_id"],
        candidate_id=with_handoffs["candidate"]["id"],
        task_id=with_handoffs["candidate"]["task_id"],
        items=[{"item_key": "48204", "reason": "価格が数値ではありません（商談中）"}],
    )
    rows = repos.list_handoffs(with_handoffs["company_id"])
    assert len(rows) == 3
    updated = next(r for r in rows if r["item_key"] == "48204")
    assert "商談中" in updated["reason"], "理由は最新のものに更新される"


def test_resolving_removes_it_from_the_open_queue(repos, with_handoffs):
    rows = repos.list_handoffs(with_handoffs["company_id"])
    repos.resolve_handoff(rows[0]["id"], with_handoffs["user_id"], "手で登録しました")

    assert len(repos.list_handoffs(with_handoffs["company_id"], status="open")) == 2
    assert repos.handoff_counts(with_handoffs["company_id"]) == {"open": 2, "resolved": 1}
    resolved = [
        r for r in repos.list_handoffs(with_handoffs["company_id"], status="resolved")
    ][0]
    assert resolved["note"] == "手で登録しました"
    assert resolved["resolved_by"] == with_handoffs["user_id"]


def test_resolved_item_can_be_handed_off_again_later(repos, with_handoffs):
    rows = repos.list_handoffs(with_handoffs["company_id"])
    target = next(r for r in rows if r["item_key"] == "48204")
    repos.resolve_handoff(target["id"], with_handoffs["user_id"])

    repos.record_handoffs(
        company_id=with_handoffs["company_id"], execution_id=with_handoffs["execution_id"],
        candidate_id=with_handoffs["candidate"]["id"],
        task_id=with_handoffs["candidate"]["task_id"],
        items=[{"item_key": "48204", "reason": "再び価格が未確定です"}],
    )
    open_rows = repos.list_handoffs(with_handoffs["company_id"], status="open")
    assert "48204" in {r["item_key"] for r in open_rows}


# ------------------------------------------------------------------ 画面
@pytest.fixture()
def client(with_handoffs):
    from worklens.api.app import app

    return TestClient(app)


def test_automation_page_shows_the_queue_and_history(client, with_handoffs):
    body = client.get("/automation").text
    assert "自動化の実行状況" in body
    assert "人へ回った件" in body
    assert "48204" in body and "応談" in body
    assert "web_transfer" in body
    # 実装済みレシピの一覧も出る
    assert "mail_to_ledger" in body


def test_page_explains_that_handoff_is_not_a_failure(client):
    assert "失敗ではなく設計どおりの動作" in client.get("/automation").text


def test_resolving_from_the_screen_persists(client, repos, with_handoffs):
    handoff = repos.list_handoffs(with_handoffs["company_id"])[0]
    res = client.post(f"/api/handoffs/{handoff['id']}/resolve", data={"note": "対応済み"})
    assert res.status_code == 200
    assert repos.handoff_counts(with_handoffs["company_id"])["resolved"] == 1


def test_cannot_resolve_another_companys_handoff(client, repos, with_handoffs):
    other_company = repos.create_company("別会社")
    other_user = repos.create_user(other_company, "x@y.jp", "他社", role="admin")
    handoff = repos.list_handoffs(with_handoffs["company_id"])[0]
    res = client.post(
        f"/api/handoffs/{handoff['id']}/resolve",
        headers={"X-WorkLens-User": other_user},
    )
    assert res.status_code == 404
    assert repos.handoff_counts(with_handoffs["company_id"]).get("resolved") is None


def test_candidate_detail_shows_its_own_execution_history(client, with_handoffs):
    body = client.get(f"/candidates/{with_handoffs['candidate']['id']}").text
    assert "自動化の実行実績" in body
    assert "人へ回っている件" in body
