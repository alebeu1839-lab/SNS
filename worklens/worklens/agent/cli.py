"""PCエージェントの CLI。

  python -m worklens.agent.cli init --company "サンプル自動車" --name 山田 --email a@b.jp
  python -m worklens.agent.cli scopes            # 何を収集しているか
  python -m worklens.agent.cli consent set browser_usage off
  python -m worklens.agent.cli collect --days 20 --source synthetic
  python -m worklens.agent.cli collect --source live --minutes 30
  python -m worklens.agent.cli status
  python -m worklens.agent.cli purge --scope user
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import get_settings
from ..storage.db import init_db
from ..storage.repositories import Repositories
from .collectors.os_collectors import detect_collector, missing_dependency_hint
from .collectors.synthetic import SyntheticCollector
from .profile import AgentProfile, host_info
from .recorder import Recorder, ingest_sessions
from .scopes import ALL_SCOPE_KEYS, DEFAULT_CONSENT, SCOPES, scope


def _repos(settings) -> tuple[Repositories, "sqlite3.Connection"]:  # type: ignore[name-defined]
    conn = init_db(settings.db_path)
    return Repositories(conn), conn


def cmd_init(args: argparse.Namespace) -> int:
    settings = get_settings()
    repos, _ = _repos(settings)
    company = next(
        (c for c in repos.list_companies() if c["name"] == args.company), None
    )
    company_id = company["id"] if company else repos.create_company(
        args.company, args.hourly_cost
    )
    user = repos.find_user(company_id, args.email)
    user_id = user["id"] if user else repos.create_user(
        company_id, args.email, args.name, args.department, args.role
    )
    hostname, os_name, os_version = host_info()
    device_id = repos.register_device(
        company_id, user_id, hostname, os_name, settings.agent_version, os_version,
        settings.timezone,
    )
    for key, enabled in DEFAULT_CONSENT.items():
        repos.set_consent(company_id, user_id, device_id, key, enabled)
    AgentProfile(
        company_id, user_id, device_id, args.company, args.name, hostname
    ).save(settings.home)
    repos.audit(f"user:{user_id}", "agent.initialized", company_id, "device", device_id)

    print("エージェントを初期化しました。")
    print(f"  企業     : {args.company} ({company_id})")
    print(f"  ユーザー : {args.name} <{args.email}> ({user_id})")
    print(f"  PC       : {hostname} / {os_name} ({device_id})")
    print(f"  DB       : {settings.db_path}")
    print("\n収集項目は既定値で有効化しました。`scopes` で内容を確認できます。")
    return 0


def cmd_scopes(args: argparse.Namespace) -> int:
    settings = get_settings()
    repos, _ = _repos(settings)
    profile = AgentProfile.load(settings.home)
    consent = (
        repos.consent_map(profile.user_id, profile.device_id) if profile else DEFAULT_CONSENT
    )
    if args.json:
        print(json.dumps(
            [
                {
                    "key": s.key, "label": s.label, "enabled": consent.get(s.key, False),
                    "collected": s.collected, "not_collected": s.not_collected,
                    "required": s.required,
                }
                for s in SCOPES
            ],
            ensure_ascii=False, indent=2,
        ))
        return 0
    print("=== 収集項目（この一覧にないものは収集しません） ===\n")
    for s in SCOPES:
        mark = "ON " if consent.get(s.key, False) else "OFF"
        req = "  ※分析に必須" if s.required else ""
        print(f"[{mark}] {s.label}  ({s.key}){req}")
        print(f"       {s.description}")
        print(f"       収集する    : {', '.join(s.collected)}")
        print(f"       収集しない  : {', '.join(s.not_collected)}\n")
    return 0


def cmd_consent(args: argparse.Namespace) -> int:
    settings = get_settings()
    repos, _ = _repos(settings)
    profile = _require_profile(settings)
    if args.action == "set":
        if args.scope_key not in ALL_SCOPE_KEYS:
            print(f"未知の収集項目: {args.scope_key}", file=sys.stderr)
            return 2
        enabled = args.value == "on"
        if not enabled and scope(args.scope_key).required:
            print(
                f"警告: {scope(args.scope_key).label} は分析に必須です。"
                "OFF にすると自動化候補は生成されません。"
            )
        repos.set_consent(
            profile.company_id, profile.user_id, profile.device_id, args.scope_key, enabled
        )
        repos.audit(
            f"user:{profile.user_id}", "consent.changed", profile.company_id,
            "scope", args.scope_key, {"enabled": enabled},
        )
        print(f"{scope(args.scope_key).label} を {'ON' if enabled else 'OFF'} にしました。")
    elif args.action == "all-off":
        for key in ALL_SCOPE_KEYS:
            repos.set_consent(
                profile.company_id, profile.user_id, profile.device_id, key, False
            )
        repos.audit(
            f"user:{profile.user_id}", "collection.stopped_all", profile.company_id
        )
        print("すべての収集を停止しました。")
    consent = repos.consent_map(profile.user_id, profile.device_id)
    print(json.dumps(consent, ensure_ascii=False))
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    settings = get_settings()
    repos, _ = _repos(settings)
    profile = _require_profile(settings)
    recorder = Recorder(
        repos, profile.company_id, profile.user_id, profile.device_id,
        agent_version=settings.agent_version, idle_gap_sec=settings.idle_gap_sec,
    )
    if not any(recorder.consent.values()):
        print("収集はすべて停止されています（consent set ... on で再開できます）。")
        return 1

    if args.source == "synthetic":
        end = date.fromisoformat(args.until) if args.until else date.today()
        sessions = list(SyntheticCollector(seed=args.seed).generate_days(args.days, end))
        result = ingest_sessions(recorder, sessions)
        print(f"合成ワークロードを取り込みました（{len(result['sessions'])} セッション）")
    else:
        collector = detect_collector()
        if collector is None:
            print(
                "この環境では実収集コレクタを利用できません。\n"
                f"  必要な準備: {missing_dependency_hint()}\n"
                "  --source synthetic でデモデータを使えます。",
                file=sys.stderr,
            )
            return 3
        result = _collect_live(
            recorder, collector, args.minutes, args.interval, settings.idle_gap_sec
        )
        print(f"実収集を終了しました（コレクタ: {collector.name}）")

    print(json.dumps({k: v for k, v in result.items() if k != "sessions"},
                     ensure_ascii=False, indent=2))
    return 0


def _collect_live(
    recorder: Recorder, collector, minutes: int, interval: float, idle_after: int
) -> dict:
    """実PCからの収集ループ。

    離席中はアプリの記録を止める。これをやらないと、昼休みに開いたままの
    Excel が「3時間の作業」として集計され、削減見込みまで水増しされる。
    """
    from .activity import ActivityProbe
    from .idle import IdleDetector

    idle_detector = IdleDetector()
    probe = ActivityProbe(idle_detector, interval)
    notes = probe.capability_notes()
    if notes:
        print("この環境で取得できないもの:")
        for note in notes:
            print(f"  - {note}")
        print()

    started = datetime.now(timezone.utc).replace(microsecond=0)
    session_id = recorder.start_session(started.isoformat())
    recorder.ingest(session_id, [{
        "ts": started.isoformat(), "event_type": "work_start", "scope_key": "work_hours",
        "detail": {"source": "agent"},
    }])

    deadline = time.time() + minutes * 60
    stored = dropped = masked = 0
    idle_seconds = 0
    is_idle = False
    idle_since: datetime | None = None
    try:
        while time.time() < deadline:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            since_input = idle_detector.seconds_since_input()
            became_idle = since_input is not None and since_input >= idle_after

            if became_idle and not is_idle:
                is_idle = True
                # 離席は「しきい値を超えた時点」ではなく「最後の入力時刻」から始まる
                idle_since = now - timedelta(seconds=int(since_input or idle_after))
                recorder.ingest(session_id, [{
                    "ts": idle_since.isoformat(), "event_type": "idle_start",
                    "scope_key": "work_hours", "detail": {"threshold_sec": idle_after},
                }])
            elif not became_idle and is_idle:
                is_idle = False
                if idle_since is not None:
                    idle_seconds += max(0, int((now - idle_since).total_seconds()))
                idle_since = None
                recorder.ingest(session_id, [{
                    "ts": now.isoformat(), "event_type": "idle_end", "scope_key": "work_hours",
                }])

            if not is_idle:
                events = list(collector.poll())
                app = next(
                    (e.get("app_name") for e in events if e.get("app_name")), None
                )
                # 入力の「量」だけを記録する。押されたキーは取得しない。
                if probe.input_happened(since_input):
                    events.append({
                        "ts": now.isoformat(), "event_type": "input_burst",
                        "scope_key": "input_activity", "app_name": app,
                        "detail": {"active_sec": round(float(interval), 3)},
                    })
                # クリップボードの「変更が起きた事実」だけを記録する。中身は読まない。
                if probe.clipboard_changed():
                    events.append({
                        "ts": now.isoformat(), "event_type": "clipboard_op",
                        "scope_key": "clipboard_meta", "app_name": app,
                        "detail": {"op": "copy", "length_bucket": "unknown"},
                    })
                stats = recorder.ingest(session_id, events)
                stored += stats.stored
                dropped += stats.dropped
                masked += stats.masked
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n収集を中断しました。")
    finally:
        ended = datetime.now(timezone.utc).replace(microsecond=0)
        if is_idle and idle_since is not None:      # 離席したまま終了した場合
            idle_seconds += max(0, int((ended - idle_since).total_seconds()))
        recorder.ingest(session_id, [{
            "ts": ended.isoformat(), "event_type": "work_end", "scope_key": "work_hours",
            "detail": {"source": "agent"},
        }])
        recorder.build_app_usage(session_id)
        recorder.end_session(session_id, ended.isoformat(), paused_sec=idle_seconds)
    return {
        "sessions": [session_id], "stored": stored, "dropped": dropped, "masked": masked,
        "idle_sec": idle_seconds,
    }


def cmd_analyze(args: argparse.Namespace) -> int:
    """収集済みデータを分析し、自動化候補を生成する。"""
    from ..analysis.pipeline import run_analysis

    settings = get_settings()
    repos, _ = _repos(settings)
    profile = _require_profile(settings)
    result = run_analysis(
        repos, profile.company_id, profile.user_id,
        period_days=args.period_days, settings=settings,
        now=datetime.now(timezone.utc),
    )
    print(json.dumps(result.stats, ensure_ascii=False, indent=2))
    candidates = repos.list_candidates(result.run_id)
    if candidates:
        print("\n--- 自動化候補（上位5件） ---")
        for c in candidates[:5]:
            print(
                f"{c['rank']}. {c['task_name']}\n"
                f"   自動化可能性 {c['feasibility']:.0%} / 難易度 {c['difficulty']}"
                f" / リスク {c['risk_level']} / 月間削減 "
                f"{c['est_saved_minutes_month'] / 60:.1f}時間"
                f" ({c['est_saved_cost_month_jpy']:,}円)"
            )
    print("\nダッシュボード: python -m uvicorn worklens.api.app:app --port 8000")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """このPCで何が取得できるかを、収集を始める前に確かめる。"""
    from .activity import ClipboardChangeWatcher
    from .idle import IdleDetector

    settings = get_settings()
    print("=== このPCでの収集可否 ===\n")

    collector = detect_collector()
    idle = IdleDetector()
    clipboard = ClipboardChangeWatcher()

    checks = [
        ("使用アプリ・ウィンドウタイトル", collector is not None,
         missing_dependency_hint(), "業務の判別ができません（これが無いと分析不能）"),
        ("離席の検出", idle.is_supported(), idle.unsupported_reason(),
         "昼休みなどの離席が作業時間に混ざります"),
        ("入力量の検出", idle.is_supported(), idle.unsupported_reason(),
         "入力作業と閲覧の区別が付きません"),
        ("コピーの検出", clipboard.available(), clipboard.unsupported_reason(),
         "転記業務が検出されにくくなります"),
    ]
    ok = True
    for label, available, hint, impact in checks:
        mark = "OK  " if available else "不可"
        print(f"[{mark}] {label}")
        if not available:
            ok = False
            if hint:
                print(f"        対処: {hint}")
            print(f"        影響: {impact}")
    print()
    print("※ ファイル操作（保存・リネーム）は現時点では実PCから取得しません。")
    print("   書類作成系の業務は、アプリとタイトルから推定します。\n")

    if collector is not None:
        app, title = collector.active_window()
        print(f"いま見えている画面: {app or '取得できず'} / {title or '(タイトルなし)'}")
    print(f"保存先: {settings.db_path}")

    if not ok:
        print("\n一部が取得できません。上の対処を行うと分析の精度が上がります。")
    else:
        print("\nすべて取得できます。`collect --source live` を実行できます。")
    return 0 if collector is not None else 3


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    repos, _ = _repos(settings)
    profile = AgentProfile.load(settings.home)
    if not profile:
        print("未初期化です。`init` を実行してください。")
        return 1
    consent = repos.consent_map(profile.user_id, profile.device_id)
    sessions = repos.list_sessions(profile.user_id)
    print(f"企業        : {profile.company_name}")
    print(f"ユーザー    : {profile.user_name}")
    print(f"PC          : {profile.hostname}")
    print(f"DB          : {settings.db_path}")
    print(f"セッション  : {len(sessions)} 件")
    print(f"イベント    : {repos.count_events(profile.company_id)} 件")
    print(f"有効な収集  : {', '.join(k for k, v in consent.items() if v) or '（なし）'}")
    print(f"停止中の収集: {', '.join(k for k, v in consent.items() if not v) or '（なし）'}")
    print("\n--- 除外・マスク実績 ---")
    for row in repos.redaction_summary(profile.company_id):
        print(f"  {row['reason']:<20} {row['scope_key'] or '-':<16} {row['count']} 件")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    settings = get_settings()
    repos, _ = _repos(settings)
    profile = _require_profile(settings)
    if not args.yes:
        answer = input("収集データを削除します。よろしいですか？ [y/N]: ")
        if answer.strip().lower() != "y":
            print("中止しました。")
            return 1
    if args.scope == "company":
        counts = repos.purge_company_data(profile.company_id)
        repos.audit(f"user:{profile.user_id}", "data.purged.company", profile.company_id,
                    detail=counts)
    else:
        counts = repos.purge_user_data(profile.user_id)
        repos.audit(f"user:{profile.user_id}", "data.purged.user", profile.company_id,
                    detail=counts)
    print("削除しました:", json.dumps(counts, ensure_ascii=False))
    return 0


def _require_profile(settings) -> AgentProfile:
    profile = AgentProfile.load(settings.home)
    if not profile:
        print("未初期化です。先に `init` を実行してください。", file=sys.stderr)
        raise SystemExit(1)
    return profile


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="worklens-agent", description="WorkLens PCエージェント")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="企業・ユーザー・PCを登録する")
    i.add_argument("--company", required=True)
    i.add_argument("--name", required=True)
    i.add_argument("--email", required=True)
    i.add_argument("--department", default=None)
    i.add_argument("--role", default="admin", choices=["member", "manager", "admin"])
    i.add_argument("--hourly-cost", type=int, default=3500, help="平均人件費（円/時）")
    i.set_defaults(func=cmd_init)

    s = sub.add_parser("scopes", help="何を収集しているかを表示する")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scopes)

    c = sub.add_parser("consent", help="収集項目のON/OFF")
    c.add_argument("action", choices=["set", "all-off", "show"])
    c.add_argument("scope_key", nargs="?")
    c.add_argument("value", nargs="?", choices=["on", "off"])
    c.set_defaults(func=cmd_consent)

    col = sub.add_parser("collect", help="収集を実行する")
    col.add_argument("--source", choices=["synthetic", "live"], default="synthetic")
    col.add_argument("--days", type=int, default=20, help="synthetic: 生成日数")
    col.add_argument("--until", default=None, help="synthetic: 最終日 (YYYY-MM-DD)")
    col.add_argument("--seed", type=int, default=20250501)
    col.add_argument("--minutes", type=int, default=10, help="live: 収集する分数")
    col.add_argument("--interval", type=float, default=5.0, help="live: ポーリング間隔（秒）")
    col.set_defaults(func=cmd_collect)

    dr = sub.add_parser("doctor", help="このPCで何が収集できるかを確認する")
    dr.set_defaults(func=cmd_doctor)

    an = sub.add_parser("analyze", help="収集済みデータを分析して自動化候補を作る")
    an.add_argument("--period-days", type=int, default=30, help="分析対象とする直近の日数")
    an.set_defaults(func=cmd_analyze)

    st = sub.add_parser("status", help="状態を表示する")
    st.set_defaults(func=cmd_status)

    pg = sub.add_parser("purge", help="収集データを削除する")
    pg.add_argument("--scope", choices=["user", "company"], default="user")
    pg.add_argument("--yes", action="store_true")
    pg.set_defaults(func=cmd_purge)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
