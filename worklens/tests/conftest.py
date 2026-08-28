from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    """テストごとに独立した WORKLENS_HOME を用意する。"""
    monkeypatch.setenv("WORKLENS_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from worklens.api import deps

    deps.reset_connection()
    yield tmp_path
    deps.reset_connection()


@pytest.fixture()
def repos(home):
    from worklens.storage.db import init_db
    from worklens.storage.repositories import Repositories

    return Repositories(init_db(home / "worklens.db"))


@pytest.fixture()
def org(repos):
    """企業・ユーザー・PC・同意を揃えた最小構成。"""
    from worklens.agent.scopes import DEFAULT_CONSENT

    company_id = repos.create_company("テスト商事", 3000)
    user_id = repos.create_user(company_id, "test@example.co.jp", "検証太郎", "業務課", "admin")
    device_id = repos.register_device(company_id, user_id, "PC-TEST", "Windows", "0.1.0")
    for key, enabled in DEFAULT_CONSENT.items():
        repos.set_consent(company_id, user_id, device_id, key, enabled)
    return {"company_id": company_id, "user_id": user_id, "device_id": device_id}


@pytest.fixture()
def collected(repos, org, home):
    """合成ワークロードを取り込み済みの状態。"""
    from worklens.agent.collectors.synthetic import SyntheticCollector
    from worklens.agent.profile import AgentProfile
    from worklens.agent.recorder import Recorder, ingest_sessions

    recorder = Recorder(repos, org["company_id"], org["user_id"], org["device_id"])
    sessions = list(SyntheticCollector(seed=7).generate_days(12, date(2025, 5, 30)))
    stats = ingest_sessions(recorder, sessions)
    AgentProfile(
        org["company_id"], org["user_id"], org["device_id"], "テスト商事", "検証太郎", "PC-TEST"
    ).save(home)
    return {**org, "ingest": stats, "now": datetime(2025, 5, 31, tzinfo=timezone.utc)}


@pytest.fixture()
def analyzed(repos, collected):
    from worklens.analysis.pipeline import run_analysis

    result = run_analysis(
        repos, collected["company_id"], collected["user_id"],
        period_days=30, now=collected["now"],
    )
    return {**collected, "run_id": result.run_id, "stats": result.stats}
