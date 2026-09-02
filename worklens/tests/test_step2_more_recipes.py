"""追加した4レシピの検証。

共通して確かめること:
  - 自動化してよい範囲で止まっているか（送信しない、所感を書かない）
  - 判断が要る件を人へ回しているか
  - 何もしなくてよい件を「引き継ぎ」に混ぜていないか
"""
from __future__ import annotations

import socket
import threading
import time
from datetime import date

import httpx
import pytest
import uvicorn

from worklens.step2.recipes.daily_report import DailyReportRecipe
from worklens.step2.recipes.document_and_send import QuoteAndDraftRecipe
from worklens.step2.recipes.mail_reply import MailReplyDraftRecipe
from worklens.step2.recipes.mail_to_sheet import LEDGER_COLUMNS, LedgerWriter
from worklens.step2.recipes.threshold_monitor import InventoryMonitorRecipe
from worklens.step2.runner import SKIP, AutomationRunner


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            return server, f"http://127.0.0.1:{port}"
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("モックが起動しませんでした")


@pytest.fixture()
def mail_url():
    from mock.mail_system import DRAFTS, MESSAGES, app

    for m in MESSAGES:
        m["processed"] = False
        m["replied"] = False
    DRAFTS.clear()
    server, url = _serve(app)
    yield url
    server.should_exit = True
    DRAFTS.clear()


@pytest.fixture()
def inventory_url():
    from mock import data
    from mock.inventory_system import app

    data.reset()
    server, url = _serve(app)
    yield url
    server.should_exit = True
    data.reset()


@pytest.fixture()
def notify_url():
    from mock.notification_sink import NOTIFICATIONS, app

    NOTIFICATIONS.clear()
    server, url = _serve(app)
    yield url
    server.should_exit = True
    NOTIFICATIONS.clear()


# =========================================================== 見積書＋下書き
def _ledger(tmp_path, rows):
    writer = LedgerWriter(tmp_path / "台帳.xlsx")
    for row in rows:
        writer.append_row({c: row.get(c, "") for c in LEDGER_COLUMNS})
    return writer


CUSTOMER = {
    "受付日時": "2025-06-02 09:14", "氏名": "田中健一", "電話番号": "090-1111-2222",
    "メールアドレス": "tanaka@example.com", "希望車種": "アクア",
    "予算(円)": 1300000, "メールID": "m001",
}


def test_quote_is_generated_and_only_a_draft_is_created(
    tmp_path, inventory_url, mail_url
):
    """送信はしない。下書きまでで止まる。"""
    _ledger(tmp_path, [CUSTOMER])
    recipe = QuoteAndDraftRecipe(
        tmp_path / "台帳.xlsx", inventory_url, mail_url, tmp_path / "見積書"
    )
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.succeeded == 1

    pdfs = list((tmp_path / "見積書").glob("*.pdf"))
    assert len(pdfs) == 1 and pdfs[0].read_bytes().startswith(b"%PDF")

    drafts = httpx.get(f"{mail_url}/api/drafts").json()
    assert len(drafts) == 1
    assert drafts[0]["sent"] is False, "自動化が送信してはいけない"
    assert drafts[0]["attachment"] == pdfs[0].name


def test_over_budget_is_left_to_a_person(tmp_path, inventory_url, mail_url):
    """在庫はあるが予算超過。値引き判断は人の仕事。"""
    _ledger(tmp_path, [{**CUSTOMER, "希望車種": "ハリアー", "予算(円)": 1000000}])
    recipe = QuoteAndDraftRecipe(
        tmp_path / "台帳.xlsx", inventory_url, mail_url, tmp_path / "見積書"
    )
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.handoff == 1
    assert "予算" in result.items[0].reason
    assert httpx.get(f"{mail_url}/api/drafts").json() == []


def test_no_matching_stock_is_left_to_a_person(tmp_path, inventory_url, mail_url):
    _ledger(tmp_path, [{**CUSTOMER, "希望車種": "ランボルギーニ"}])
    recipe = QuoteAndDraftRecipe(
        tmp_path / "台帳.xlsx", inventory_url, mail_url, tmp_path / "見積書"
    )
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.handoff == 1
    assert "該当する在庫がありません" in result.items[0].reason


def test_quote_is_not_created_twice(tmp_path, inventory_url, mail_url):
    _ledger(tmp_path, [CUSTOMER])
    recipe = QuoteAndDraftRecipe(
        tmp_path / "台帳.xlsx", inventory_url, mail_url, tmp_path / "見積書"
    )
    AutomationRunner(recipe, tmp_path, mode="live").run()
    second = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert second.processed == 0, "台帳に見積書名が入った行は対象外になる"
    assert len(list((tmp_path / "見積書").glob("*.pdf"))) == 1


def test_dry_run_creates_neither_pdf_nor_draft(tmp_path, inventory_url, mail_url):
    _ledger(tmp_path, [CUSTOMER])
    recipe = QuoteAndDraftRecipe(
        tmp_path / "台帳.xlsx", inventory_url, mail_url, tmp_path / "見積書"
    )
    result = AutomationRunner(recipe, tmp_path, mode="dry-run").run()
    assert result.succeeded == 1
    assert not (tmp_path / "見積書").exists()
    assert httpx.get(f"{mail_url}/api/drafts").json() == []


# =============================================================== 返信下書き
def test_replies_are_drafted_but_never_sent(mail_url, tmp_path):
    recipe = MailReplyDraftRecipe(mail_url)
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.succeeded == 6
    assert result.handoff == 1               # クレームは人へ

    drafts = httpx.get(f"{mail_url}/api/drafts").json()
    assert len(drafts) == 6
    assert all(d["sent"] is False for d in drafts), "自動化が送信してはいけない"
    assert all(d["subject"].startswith("Re: ") for d in drafts)


def test_complaint_is_never_auto_replied(mail_url, tmp_path):
    recipe = MailReplyDraftRecipe(mail_url)
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    complaint = next(i for i in result.items if i.key == "m005")
    assert complaint.status == "handoff"
    assert "クレーム" in complaint.reason
    refs = {d["ref"] for d in httpx.get(f"{mail_url}/api/drafts").json()}
    assert "reply:m005" not in refs


def test_already_replied_mails_are_out_of_scope_not_handoff(mail_url, tmp_path):
    """返信済みは「対象外」。引き継ぎに混ぜると未処理が積み上がる。"""
    recipe = MailReplyDraftRecipe(mail_url)
    AutomationRunner(recipe, tmp_path, mode="live").run()
    second = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert second.count("skipped") == 6
    assert second.succeeded == 0


def test_draft_body_does_not_promise_facts(mail_url, tmp_path):
    """在庫あり・価格などを機械が約束しないこと。"""
    recipe = MailReplyDraftRecipe(mail_url)
    payload, _ = recipe.validate(
        {"id": "x", "from": "a@b", "subject": "在庫について",
         "body": "田中と申します。アクアの在庫はありますか。"}
    )
    assert "確認" in payload["body"]
    assert "自動作成" in payload["body"], "自動生成である旨を残す"


# =================================================================== 日報
def test_daily_report_fills_numbers_but_leaves_the_opinion_blank(
    inventory_url, tmp_path
):
    recipe = DailyReportRecipe(inventory_url, tmp_path, target_date=date(2025, 6, 3))
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.succeeded == 1

    text = (tmp_path / "日報_20250603.md").read_text(encoding="utf-8")
    assert "在庫総数 | 12 台" in text
    assert "## 所感" in text
    # 所感欄に文章が書かれていないこと
    opinion = text.split("## 所感", 1)[1].split("---", 1)[0]
    assert "担当者が記入" in opinion
    assert len([l for l in opinion.splitlines() if l.strip() and not l.strip().startswith("<!--")]) == 0


def test_daily_report_is_not_overwritten(inventory_url, tmp_path):
    recipe = DailyReportRecipe(inventory_url, tmp_path, target_date=date(2025, 6, 3))
    AutomationRunner(recipe, tmp_path, mode="live").run()
    second = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert second.handoff == 1
    assert "既に作成されています" in second.items[0].reason


def test_daily_report_reports_missing_data_instead_of_guessing(tmp_path):
    recipe = DailyReportRecipe("http://x", tmp_path)
    payload, reason = recipe.validate({"date": "2025-06-03", "cars": []})
    assert payload is None
    assert "取得できませんでした" in reason


# ============================================================== 条件通知
def test_monitor_notifies_only_what_matches(inventory_url, notify_url, tmp_path):
    recipe = InventoryMonitorRecipe(inventory_url, notify_url)
    result = AutomationRunner(recipe, tmp_path, mode="live").run()

    assert result.processed == 12
    assert result.succeeded == 3          # 価格未設定 / 走行距離未入力 / 要確認メモ
    assert result.count("skipped") == 9   # 異常なしは「対象外」
    assert result.handoff == 0, "異常なしを人へ引き継いではいけない"

    notifications = httpx.get(f"{notify_url}/api/notifications").json()
    assert len(notifications) == 3
    assert {n["level"] for n in notifications} == {"warn"}


def test_monitor_sends_nothing_when_everything_is_fine(notify_url, tmp_path):
    """異常が無ければ通知は0件。通知が来ないこと自体が情報。"""
    clean = [
        {"id": 1, "maker": "トヨタ", "model": "アクア", "price_yen": 1000000,
         "mileage_km": 30000, "note": ""},
    ]
    recipe = InventoryMonitorRecipe("http://x", notify_url)
    recipe.fetch = lambda: clean          # type: ignore[method-assign]
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.succeeded == 0 and result.count("skipped") == 1
    assert httpx.get(f"{notify_url}/api/notifications").json() == []


def test_dry_run_monitor_sends_no_notification(inventory_url, notify_url, tmp_path):
    recipe = InventoryMonitorRecipe(inventory_url, notify_url)
    result = AutomationRunner(recipe, tmp_path, mode="dry-run").run()
    assert result.succeeded == 3
    assert httpx.get(f"{notify_url}/api/notifications").json() == []


def test_skip_is_counted_separately_from_handoff(tmp_path):
    """SKIP と handoff を混同しないこと（未処理の山を作らないため）。"""

    class Recipe:
        name = "t"

        def fetch(self):
            return [{"id": "a"}, {"id": "b"}, {"id": "c"}]

        def key_of(self, s):
            return s["id"]

        def validate(self, s):
            if s["id"] == "a":
                return SKIP, "対応不要"
            if s["id"] == "b":
                return None, "判断が必要"
            return {"ok": True}, "処理する"

        def apply(self, p):
            pass

        def finalize(self, s, p):
            pass

    result = AutomationRunner(Recipe(), tmp_path, mode="live").run()
    assert result.count("skipped") == 1
    assert result.handoff == 1
    assert result.succeeded == 1
    assert result.summary()["skipped"] == 1
