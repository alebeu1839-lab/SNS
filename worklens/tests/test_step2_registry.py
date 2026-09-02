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
    "category", ["書類作成・送付", "メール対応", "会議・打合せ", "情報収集・調査", "その他"]
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
    assert resolved == {"source": "http://127.0.0.1:9101", "target": "http://127.0.0.1:9102"}


def test_endpoints_resolve_by_system_name_for_desktop_apps():
    """デスクトップアプリにはドメインが無いので、名前でも引けること。"""
    spec = {"systems": [
        {"name": "Outlook", "domain": None, "role": "参照元"},
        {"name": "Excel", "domain": None, "role": "書き込み先"},
    ]}
    resolved = _resolve_endpoints(spec, {
        "Outlook": "http://127.0.0.1:9103", "Excel": "./台帳.xlsx",
    })
    assert resolved == {"source": "http://127.0.0.1:9103", "target": "./台帳.xlsx"}


def test_partial_mapping_is_rejected():
    spec = {"systems": [
        {"name": "Outlook", "domain": None, "role": "参照元"},
        {"name": "Excel", "domain": None, "role": "書き込み先"},
    ]}
    with pytest.raises(SystemExit) as exc:
        _resolve_endpoints(spec, {"Outlook": "http://127.0.0.1:9103"})
    assert "Excel" in str(exc.value)
