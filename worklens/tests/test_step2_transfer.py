"""転記レシピの検証。モックの2システムを実際に起動して通す。"""
from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from mock import data as mock_data
from mock.listing_site import LISTINGS
from worklens.step2.recipes.web_transfer import VehicleTransferRecipe
from worklens.step2.runner import AutomationRunner


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            return server
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("モックサイトが起動しませんでした")


@pytest.fixture()
def mocks():
    from mock.inventory_system import app as inventory_app
    from mock.listing_site import app as listing_app

    mock_data.reset()
    LISTINGS.clear()
    inv_port, lst_port = _free_port(), _free_port()
    inv = _serve(inventory_app, inv_port)
    lst = _serve(listing_app, lst_port)
    yield f"http://127.0.0.1:{inv_port}", f"http://127.0.0.1:{lst_port}"
    inv.should_exit = lst.should_exit = True
    mock_data.reset()
    LISTINGS.clear()


class HttpFormBrowser:
    """ブラウザの代わりにHTTPでフォーム送信する試験用スタブ。

    Playwright を使わずに、レシピ〜転記先の結線と結果検証を確かめる。
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def submit_form(self, url, values, submit_selector, success_check):
        res = httpx.post(f"{self.base_url}/vehicles", data=values, follow_redirects=True)
        if not success_check(str(res.url)):
            raise RuntimeError(f"転記先が登録を受け付けませんでした: {res.url}")


# ------------------------------------------------------------------ 判定
@pytest.mark.parametrize(
    "car, expect_handoff, keyword",
    [
        ({"id": 1, "maker": "トヨタ", "model": "アクア", "year": 2019, "mileage_km": 42000,
          "price_yen": 1280000, "color": "白", "note": ""}, False, ""),
        ({"id": 2, "maker": "レクサス", "model": "RX", "year": 2021, "mileage_km": 18000,
          "price_yen": None, "price_text": "応談", "color": "白", "note": ""}, True, "価格"),
        ({"id": 3, "maker": "マツダ", "model": "CX-5", "year": 2019, "mileage_km": None,
          "price_yen": 1880000, "color": "赤", "note": ""}, True, "走行距離"),
        ({"id": 4, "maker": "スバル", "model": "フォレスター", "year": 2017,
          "mileage_km": 76500, "price_yen": 1290000, "color": "黒",
          "note": "※要確認 修復歴の有無を確認中"}, True, "確認"),
        ({"id": 5, "maker": "", "model": "ノート", "year": 2018, "mileage_km": 58200,
          "price_yen": 980000, "color": "銀", "note": ""}, True, "必須項目"),
    ],
)
def test_validation_separates_automatable_from_human_work(car, expect_handoff, keyword):
    recipe = VehicleTransferRecipe("http://x", "http://y")
    payload, reason = recipe.validate(car)
    assert (payload is None) is expect_handoff
    if expect_handoff:
        assert keyword in reason
    else:
        assert payload["vehicle_id"] == "1"


# -------------------------------------------------------------- 通し実行
def test_dry_run_leaves_both_systems_untouched(mocks, tmp_path):
    source, target = mocks
    recipe = VehicleTransferRecipe(source, target, browser=HttpFormBrowser(target))
    result = AutomationRunner(recipe, tmp_path, mode="dry-run").run()

    assert result.processed == 12
    assert result.succeeded == 9
    assert result.handoff == 3
    # 転記先には1件も入っていない
    assert httpx.get(f"{target}/vehicles").text.count("<tr>") == 0
    # 転記元の掲載フラグも変わっていない
    cars = httpx.get(f"{source}/api/cars").json()
    assert all(not c["listed"] for c in cars)


def test_live_run_transfers_only_the_safe_records(mocks, tmp_path):
    source, target = mocks
    recipe = VehicleTransferRecipe(source, target, browser=HttpFormBrowser(target))
    result = AutomationRunner(recipe, tmp_path, mode="live").run()

    assert result.succeeded == 9
    assert result.handoff == 3
    assert result.failed == 0

    listed = [c for c in httpx.get(f"{source}/api/cars").json() if c["listed"]]
    unlisted = [c["id"] for c in httpx.get(f"{source}/api/cars").json() if not c["listed"]]
    assert len(listed) == 9
    # 人へ引き継いだ3件は転記されず、元システムに残っている
    assert sorted(unlisted) == [48204, 48206, 48208]

    page = httpx.get(f"{target}/vehicles").text
    assert "掲載中の車両（9台）" in page
    assert "48201" in page and "48204" not in page


def test_second_run_does_not_duplicate(mocks, tmp_path):
    """完了印を付けているので、再実行しても二重登録にならない。"""
    source, target = mocks
    recipe = VehicleTransferRecipe(source, target, browser=HttpFormBrowser(target))
    AutomationRunner(recipe, tmp_path, mode="live").run()
    second = AutomationRunner(recipe, tmp_path, mode="live").run()

    assert second.processed == 3          # 残るのは引き継ぎ3件だけ
    assert second.succeeded == 0
    assert second.handoff == 3
    assert "掲載中の車両（9台）" in httpx.get(f"{target}/vehicles").text


def test_target_rejection_is_reported_as_failure_not_success(mocks, tmp_path):
    """転記先に弾かれたのに成功扱いする、が最も危ない。必ず失敗として扱う。"""
    source, target = mocks
    httpx.post(
        f"{target}/vehicles",
        data={"vehicle_id": "48201", "maker": "X", "model": "Y", "year": "2019",
              "mileage": "1", "price": "1", "color": "白"},
        follow_redirects=True,
    )   # 先に48201を登録しておく → 自動化側は重複で弾かれる
    recipe = VehicleTransferRecipe(source, target, browser=HttpFormBrowser(target))
    result = AutomationRunner(recipe, tmp_path, mode="live", stop_on_error=True).run()

    assert result.failed == 1
    assert result.succeeded == 0
    failed = next(i for i in result.items if i.status == "failed")
    assert failed.key == "48201"
    # 失敗した車両に完了印は付いていない
    cars = {c["id"]: c for c in httpx.get(f"{source}/api/cars").json()}
    assert cars[48201]["listed"] is False
