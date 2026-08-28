#!/usr/bin/env python3
"""デモ用の一括セットアップ。

  python scripts/run_demo.py --days 20

企業・ユーザー・PCを登録し、合成ワークロードを収集して分析まで実行する。
実PCでの収集は `python -m worklens.agent.cli collect --source live` を使う。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worklens.agent.collectors.synthetic import SyntheticCollector  # noqa: E402
from worklens.agent.profile import AgentProfile, host_info  # noqa: E402
from worklens.agent.recorder import Recorder, ingest_sessions  # noqa: E402
from worklens.agent.scopes import DEFAULT_CONSENT  # noqa: E402
from worklens.analysis.pipeline import run_analysis  # noqa: E402
from worklens.config import get_settings  # noqa: E402
from worklens.storage.db import init_db  # noqa: E402
from worklens.storage.repositories import Repositories  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="サンプル自動車販売")
    ap.add_argument("--name", default="山田太郎")
    ap.add_argument("--email", default="yamada@example.co.jp")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--until", default=None, help="最終日 YYYY-MM-DD")
    ap.add_argument("--hourly-cost", type=int, default=3500)
    ap.add_argument("--reset", action="store_true", help="既存DBを消してからやり直す")
    args = ap.parse_args()

    settings = get_settings()
    if args.reset and settings.db_path.exists():
        settings.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            extra = settings.db_path.with_name(settings.db_path.name + suffix)
            if extra.exists():
                extra.unlink()

    repos = Repositories(init_db(settings.db_path))
    company = next((c for c in repos.list_companies() if c["name"] == args.company), None)
    company_id = company["id"] if company else repos.create_company(args.company, args.hourly_cost)
    user = repos.find_user(company_id, args.email)
    user_id = user["id"] if user else repos.create_user(
        company_id, args.email, args.name, "業務課", "admin"
    )
    hostname, os_name, os_version = host_info()
    device_id = repos.register_device(
        company_id, user_id, hostname, os_name, settings.agent_version, os_version
    )
    for key, enabled in DEFAULT_CONSENT.items():
        repos.set_consent(company_id, user_id, device_id, key, enabled)
    AgentProfile(company_id, user_id, device_id, args.company, args.name, hostname).save(
        settings.home
    )

    end = date.fromisoformat(args.until) if args.until else date.today()
    recorder = Recorder(repos, company_id, user_id, device_id,
                        agent_version=settings.agent_version,
                        idle_gap_sec=settings.idle_gap_sec)
    ingest = ingest_sessions(
        recorder, list(SyntheticCollector().generate_days(args.days, end))
    )
    print("収集:", json.dumps({k: v for k, v in ingest.items() if k != "sessions"},
                              ensure_ascii=False))

    # 生成データの最終日を基準に分析する（--until を指定した場合に過去日でも動くように）
    analysis_now = min(
        datetime.now(timezone.utc),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc),
    ) if end < date.today() else datetime.now(timezone.utc)
    result = run_analysis(
        repos, company_id, user_id, period_days=args.days + 5,
        settings=settings, now=analysis_now,
    )
    print("分析:", json.dumps(result.stats, ensure_ascii=False, indent=2))
    print(f"\nDB: {settings.db_path}")
    print("ダッシュボード: python -m uvicorn worklens.api.app:app --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
