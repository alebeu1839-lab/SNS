"""機密情報の除外・マスクが実際に効いているかの検証。"""
from __future__ import annotations

import pytest

from worklens.agent.privacy import Redactor, normalize_url

ALL_ON = {
    "app_usage": True, "window_title": True, "browser_usage": True, "file_ops": True,
    "input_activity": True, "clipboard_meta": True, "work_hours": True,
}


def test_disabled_scope_is_dropped_before_storage():
    r = Redactor({**ALL_ON, "browser_usage": False})
    result = r.apply({"scope_key": "browser_usage", "url": "https://kanri.example.co.jp/cars/1"})
    assert result.event is None
    assert result.reason == "scope_disabled"


@pytest.mark.parametrize(
    "app", ["1Password.exe", "bitwarden", "KeePassXC", "Windows Credential Manager"]
)
def test_password_manager_events_are_dropped(app):
    r = Redactor(ALL_ON)
    assert r.apply({"scope_key": "app_usage", "app_name": app}).event is None


@pytest.mark.parametrize(
    "title",
    [
        "ログイン - 社内システム",
        "パスワードの変更",
        "クレジットカード情報の登録",
        "二段階認証コードの入力",
        "マイナンバーの確認",
        "API Key 設定",
    ],
)
def test_sensitive_windows_are_dropped(title):
    r = Redactor(ALL_ON)
    result = r.apply({"scope_key": "window_title", "app_name": "chrome", "window_title": title})
    assert result.event is None
    assert result.reason == "sensitive_window"


def test_banking_domain_is_dropped():
    r = Redactor(ALL_ON)
    result = r.apply(
        {"scope_key": "browser_usage", "app_name": "chrome", "url": "https://www.smbc.co.jp/x"}
    )
    assert result.event is None
    assert result.reason == "sensitive_domain"


def test_pii_in_title_is_masked_not_stored():
    r = Redactor(ALL_ON)
    ev = r.apply(
        {
            "scope_key": "window_title",
            "app_name": "EXCEL.EXE",
            "window_title": "顧客台帳 tanaka@example.com 090-1234-5678 4111111111111111",
        }
    ).event
    assert ev is not None
    assert "tanaka@example.com" not in ev["window_title"]
    assert "090-1234-5678" not in ev["window_title"]
    assert "4111111111111111" not in ev["window_title"]
    assert ev["redacted"] is True


def test_message_body_is_never_kept():
    r = Redactor(ALL_ON)
    ev = r.apply(
        {
            "scope_key": "window_title",
            "app_name": "OUTLOOK.EXE",
            "window_title": "お世話になっております。先日の件ですが、来週伺います。よろしくお願いします。",
        }
    ).event
    assert ev["window_title"] == "<本文と判定したため非保存>"


def test_file_path_and_name_are_never_stored():
    r = Redactor(ALL_ON)
    ev = r.apply(
        {
            "scope_key": "file_ops",
            "file_op": "save",
            "file_path": r"C:\機密\契約書_田中様.pdf",
            "file_name": "契約書_田中様.pdf",
        }
    ).event
    assert ev["file_ext"] == "pdf"
    assert "file_path" not in ev and "file_name" not in ev
    assert "田中" not in str(ev)


def test_structurally_forbidden_fields_are_stripped():
    r = Redactor(ALL_ON)
    ev = r.apply(
        {
            "scope_key": "input_activity",
            "app_name": "chrome",
            "keys": "password123",
            "clipboard": "顧客の個人情報",
            "screenshot": b"binary",
        }
    ).event
    for forbidden in ("keys", "clipboard", "screenshot"):
        assert forbidden not in ev


def test_url_is_reduced_to_domain_and_shape():
    domain, shape = normalize_url("https://kanri.example.co.jp/cars/48213/edit?token=secret#frag")
    assert domain == "kanri.example.co.jp"
    assert shape == "/cars/:id/edit"
    assert "secret" not in shape


def test_query_string_never_survives():
    r = Redactor(ALL_ON)
    ev = r.apply(
        {
            "scope_key": "browser_usage",
            "app_name": "chrome",
            "url": "https://keisai.example-portal.jp/search?q=%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1",
        }
    ).event
    assert "q=" not in str(ev)
    assert ev["url_domain"] == "keisai.example-portal.jp"
