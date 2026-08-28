"""PCエージェント CLI の検証。"""
from __future__ import annotations

import json

from worklens.agent.cli import main


def test_init_scopes_collect_and_status(home, capsys):
    assert main([
        "init", "--company", "CLI商事", "--name", "CLI太郎",
        "--email", "cli@example.co.jp", "--hourly-cost", "4000",
    ]) == 0

    capsys.readouterr()
    assert main(["scopes", "--json"]) == 0
    scopes = json.loads(capsys.readouterr().out)
    assert {s["key"] for s in scopes} >= {"app_usage", "window_title", "clipboard_meta"}
    # 収集しない項目が必ず明示されていること
    assert all(s["not_collected"] for s in scopes)

    assert main(["collect", "--source", "synthetic", "--days", "3", "--until", "2025-05-30"]) == 0
    out = capsys.readouterr().out
    assert "合成ワークロードを取り込みました" in out

    assert main(["status"]) == 0
    status = capsys.readouterr().out
    assert "CLI商事" in status
    assert "有効な収集" in status


def test_consent_can_be_turned_off_from_cli(home, capsys):
    main(["init", "--company", "CLI商事", "--name", "CLI太郎", "--email", "cli@example.co.jp"])
    capsys.readouterr()
    assert main(["consent", "set", "browser_usage", "off"]) == 0
    assert '"browser_usage": false' in capsys.readouterr().out


def test_collect_refuses_when_everything_is_stopped(home, capsys):
    main(["init", "--company", "CLI商事", "--name", "CLI太郎", "--email", "cli@example.co.jp"])
    main(["consent", "all-off"])
    capsys.readouterr()
    assert main(["collect", "--days", "2"]) == 1
    assert "収集はすべて停止されています" in capsys.readouterr().out


def test_unknown_scope_is_rejected(home, capsys):
    main(["init", "--company", "CLI商事", "--name", "CLI太郎", "--email", "cli@example.co.jp"])
    capsys.readouterr()
    assert main(["consent", "set", "screenshot", "on"]) == 2
