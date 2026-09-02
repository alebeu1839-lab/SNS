"""レシピ選択の検証。誤った業務を自動実行しないことが主眼。"""
from __future__ import annotations

import pytest

from worklens.step2 import registry
from worklens.step2.cli import _resolve_endpoints


@pytest.fixture(autouse=True)
def loaded():
    registry.bootstrap()


def test_both_builtin_recipes_are_registered():
    keys = {e.key for e in registry.all_recipes()}
    assert {"web_transfer", "mail_to_ledger"} <= keys


def test_recipe_is_chosen_by_business_category():
    transfer = registry.select({"task_category": "データ転記", "task_name": "転記業務"})
    entry = registry.select({"task_category": "データ入力", "task_name": "入力業務"})
    assert transfer.key == "web_transfer"
    assert entry.key == "mail_to_ledger"


@pytest.mark.parametrize(
    "category", ["会議・打合せ", "情報収集・調査", "その他", "存在しない種別"]
)
def test_unsupported_category_is_refused_not_substituted(category):
    """未対応の業務に別のレシピを当てない。動くより間違えないほうが大事。"""
    with pytest.raises(registry.NoRecipeError) as exc:
        registry.select({"task_category": category, "task_name": "ある業務"})
    message = str(exc.value)
    assert "対応するレシピはまだありません" in message
    assert "ある業務" in message
    assert "実装済みの業務種別" in message


def test_endpoints_resolve_by_domain():
    spec = {"systems": [
        {"name": "社内管理システム", "domain": "kanri.example.co.jp", "role": "参照元"},
        {"name": "掲載サイト", "domain": "keisai.example-portal.jp", "role": "書き込み先"},
    ]}
    resolved = _resolve_endpoints(spec, {
        "kanri.example.co.jp": "http://127.0.0.1:9101",
        "keisai.example-portal.jp": "http://127.0.0.1:9102",
    })
    assert resolved["source"] == "http://127.0.0.1:9101"
    assert resolved["target"] == "http://127.0.0.1:9102"


def test_endpoints_resolve_by_system_name_for_desktop_apps():
    """デスクトップアプリにはドメインが無いので、名前でも引けること。"""
    spec = {"systems": [
        {"name": "Outlook", "domain": None, "role": "参照元"},
        {"name": "Excel", "domain": None, "role": "書き込み先"},
    ]}
    resolved = _resolve_endpoints(spec, {
        "Outlook": "http://127.0.0.1:9103", "Excel": "./台帳.xlsx",
    })
    assert resolved["source"] == "http://127.0.0.1:9103"
    assert resolved["target"] == "./台帳.xlsx"


def test_partial_mapping_is_rejected_by_the_recipe():
    """片方しか指定されていなければ、レシピが実行前に止める。"""
    spec = {"systems": [
        {"name": "Outlook", "domain": None, "role": "参照元"},
        {"name": "Excel", "domain": None, "role": "書き込み先"},
    ]}
    endpoints = _resolve_endpoints(spec, {"Outlook": "http://127.0.0.1:9103"})
    assert endpoints["source"] == "http://127.0.0.1:9103"
    assert "target" not in endpoints

    entry = registry.select({"task_category": "データ入力", "task_name": "入力業務"})
    with pytest.raises(SystemExit) as exc:
        entry.factory(spec=spec, endpoints=endpoints)
    assert "台帳" in str(exc.value)


def test_single_system_tasks_still_resolve():
    """通知先のように、観測時点に存在しなかった接続先も --map で渡せる。"""
    spec = {"systems": [{"name": "社内管理システム", "domain": None, "role": "参照元"}]}
    endpoints = _resolve_endpoints(spec, {
        "社内管理システム": "http://127.0.0.1:9101",
        "inventory": "http://127.0.0.1:9101",
        "notify": "http://127.0.0.1:9104",
    })
    entry = registry.select({"task_category": "確認・モニタリング", "task_name": "在庫確認"})
    recipe = entry.factory(spec=spec, endpoints=endpoints)
    assert recipe.notify_base_url == "http://127.0.0.1:9104"


def test_every_recipe_declares_what_it_needs():
    for entry in registry.all_recipes():
        assert entry.required_endpoints, f"{entry.key} が必要な接続先を宣言していない"


def test_extra_endpoints_are_passed_through_for_recipes_that_need_them():
    """3つ目以降の接続先（在庫参照・通知先など）も渡せること。"""
    spec = {"systems": [
        {"name": "Excel", "domain": None, "role": "参照元"},
        {"name": "Outlook", "domain": None, "role": "書き込み先"},
    ]}
    resolved = _resolve_endpoints(spec, {
        "Excel": "./台帳.xlsx", "Outlook": "http://127.0.0.1:9103",
        "inventory": "http://127.0.0.1:9101",
    })
    assert resolved["inventory"] == "http://127.0.0.1:9101"


def test_recipes_now_cover_the_top_ranked_business_categories():
    covered = registry.supported_categories()
    assert {"データ転記", "データ入力", "書類作成・送付"} <= covered
