"""STEP2 の実行 CLI。

STEP1 が出した自動化候補を入力に取り、その業務の自動化を実行する。

  # 「自動化したい」と選ばれた候補を見る
  python -m worklens.step2.cli list

  # ドライラン（既定・書き込みなし）
  python -m worklens.step2.cli run --candidate <ID> \
      --map kanri.example.co.jp=http://127.0.0.1:9101 \
      --map keisai.example-portal.jp=http://127.0.0.1:9102

  # 本番実行（明示が必要）
  python -m worklens.step2.cli run --candidate <ID> --mode live --map ... --map ...

  # 実行履歴
  python -m worklens.step2.cli history
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import get_settings
from ..storage.db import init_db
from ..storage.repositories import Repositories
from .recipes.web_transfer import FormBrowser, VehicleTransferRecipe
from .runner import AutomationRunner

DECISION_LABELS = {"automate": "自動化したい", "hold": "今回は保留", "exclude": "対象外"}


def _repos() -> tuple[Repositories, "Settings"]:  # type: ignore[name-defined]
    settings = get_settings()
    return Repositories(init_db(settings.db_path)), settings


def _latest_candidates(repos: Repositories) -> tuple[str, list[dict]]:
    companies = repos.list_companies()
    if not companies:
        print("データがありません。先に STEP1 を実行してください。", file=sys.stderr)
        raise SystemExit(1)
    company_id = companies[0]["id"]
    run = repos.latest_run(company_id)
    if not run:
        print("分析結果がありません。`worklens.agent.cli analyze` を実行してください。",
              file=sys.stderr)
        raise SystemExit(1)
    return company_id, repos.list_candidates(run["id"])


def cmd_list(args: argparse.Namespace) -> int:
    repos, _ = _repos()
    _, candidates = _latest_candidates(repos)
    targets = [c for c in candidates if c["decision"] == "automate"] or candidates[:5]
    header = (
        "「自動化したい」と選ばれた候補"
        if any(c["decision"] == "automate" for c in candidates)
        else "まだ選択されていないため、上位5件を表示します"
    )
    print(f"=== {header} ===\n")
    for c in targets:
        print(f"[{c['id']}]")
        print(f"  {c['rank']}位  {c['task_name']}")
        print(f"  自動化可能性 {c['feasibility']:.0%} / 難易度 {c['difficulty']}"
              f" / 月{c['frequency_per_month']:.0f}回 × {c['est_minutes_per_run']}分")
        print(f"  選択: {DECISION_LABELS.get(c['decision'], '未選択')}\n")
    return 0


def _resolve_endpoints(spec: dict, mapping: dict[str, str]) -> tuple[str, str]:
    """spec の systems（参照元/書き込み先）を実際のURLへ解決する。

    本番のドメインと検証環境のURLを結び付ける。ここを固定値で
    ハードコードすると、検証環境で本番を叩く事故が起きる。
    """
    source = target = None
    for system in spec.get("systems", []):
        domain = system.get("domain")
        url = mapping.get(domain or "")
        if not url:
            continue
        if system.get("role") == "参照元" and source is None:
            source = url
        elif system.get("role") == "書き込み先" and target is None:
            target = url
    if not source or not target:
        known = ", ".join(
            s.get("domain") or s.get("name", "?") for s in spec.get("systems", [])
        )
        raise SystemExit(
            f"参照元・書き込み先のURLが解決できません。--map で指定してください。\n"
            f"  この候補のシステム: {known}"
        )
    return source, target


def cmd_run(args: argparse.Namespace) -> int:
    repos, settings = _repos()
    candidate = repos.get_candidate(args.candidate)
    if not candidate:
        print("候補が見つかりません。`list` でIDを確認してください。", file=sys.stderr)
        return 1

    spec = candidate["step2_spec"] or {}
    mapping = dict(pair.split("=", 1) for pair in args.map)
    source_url, target_url = _resolve_endpoints(spec, mapping)

    print(f"業務      : {candidate['task_name']}")
    print(f"参照元    : {source_url}")
    print(f"書き込み先: {target_url}")
    print(f"モード    : {args.mode}"
          f"{'（書き込みは行いません）' if args.mode == 'dry-run' else '（実際に書き込みます）'}")
    if args.mode == "live" and not args.yes:
        answer = input("本番実行します。よろしいですか？ [y/N]: ")
        if answer.strip().lower() != "y":
            print("中止しました。")
            return 1
    print()

    log_dir = Path(settings.home) / "step2_logs"
    browser_cm = (
        FormBrowser(headless=not args.headed, executable_path=args.browser_path)
        if args.mode == "live"
        else None
    )

    def _execute(browser) -> "RunResult":  # type: ignore[name-defined]
        recipe = VehicleTransferRecipe(source_url, target_url, browser=browser)
        runner = AutomationRunner(recipe, log_dir=log_dir, mode=args.mode, limit=args.limit)
        return runner.run()

    if browser_cm is not None:
        with browser_cm as browser:
            result = _execute(browser)
    else:
        result = _execute(None)

    summary = result.summary(minutes_per_run=candidate["est_minutes_per_run"])
    _print_result(result, summary)

    status = "failed" if result.error else ("partial" if result.failed else "succeeded")
    repos.record_execution(
        {
            "company_id": candidate["company_id"],
            "candidate_id": candidate["id"],
            "task_id": candidate["task_id"],
            "recipe": "vehicle_transfer",
            "mode": args.mode,
            "status": status,
            "processed": result.processed,
            "succeeded": result.succeeded,
            "handoff": result.handoff,
            "failed": result.failed,
            "saved_minutes": summary["saved_minutes"],
            "log_path": result.log_path,
            "detail": {"items": [i.to_dict() for i in result.items]},
            "error": result.error or None,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
        }
    )
    repos.audit("system", f"automation.{args.mode}", candidate["company_id"],
                "candidate", candidate["id"], summary)
    return 1 if result.error or result.failed else 0


def _print_result(result, summary: dict) -> None:
    ok = [i for i in result.items if i.status == "ok"]
    handoff = [i for i in result.items if i.status == "handoff"]
    failed = [i for i in result.items if i.status == "failed"]

    print(f"--- 自動処理できた {len(ok)}件 ---")
    for i in ok:
        p = i.payload or {}
        print(f"  {i.key}  {p.get('maker','')} {p.get('model','')}"
              f"  {p.get('year','')}年 {p.get('mileage','')}km {p.get('price','')}円")
    if handoff:
        print(f"\n--- 人へ引き継ぎ {len(handoff)}件（自動処理していません） ---")
        for i in handoff:
            print(f"  {i.key}  {i.reason}")
    if failed:
        print(f"\n--- 失敗 {len(failed)}件 ---")
        for i in failed:
            print(f"  {i.key}  {i.reason}")
    print(f"\n処理 {summary['processed']}件"
          f" / 成功 {summary['succeeded']}"
          f" / 引き継ぎ {summary['handoff']}"
          f" / 失敗 {summary['failed']}")
    print(f"削減時間の見込み: {summary['saved_minutes']}分（この実行分）")
    print(f"実行ログ: {summary['log_path']}")
    if result.error:
        print(f"エラー: {result.error}")


def cmd_history(args: argparse.Namespace) -> int:
    repos, _ = _repos()
    companies = repos.list_companies()
    if not companies:
        print("データがありません。")
        return 1
    company_id = companies[0]["id"]
    executions = repos.list_executions(company_id=company_id, limit=args.limit)
    if not executions:
        print("実行履歴はまだありません。")
        return 0
    print(f"{'実行日時':<22}{'モード':<10}{'状態':<12}{'成功':>5}{'引継':>5}{'失敗':>5}"
          f"{'削減(分)':>10}")
    for e in executions:
        print(f"{e['started_at'][:19]:<22}{e['mode']:<10}{e['status']:<12}"
              f"{e['succeeded']:>5}{e['handoff']:>5}{e['failed']:>5}"
              f"{e['saved_minutes']:>10.1f}")
    totals = repos.execution_totals(company_id)
    print(f"\n本番実行の累計: {totals['runs']}回 / 成功 {totals['succeeded']}件"
          f" / 引き継ぎ {totals['handoff']}件 / 削減 {totals['saved_minutes']:.0f}分")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="worklens-step2", description="STEP2: 自動化の実行")
    sub = p.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("list", help="自動化する候補を一覧する")
    ls.set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="自動化を実行する")
    run.add_argument("--candidate", required=True, help="候補ID（list で確認）")
    run.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    run.add_argument("--map", action="append", default=[],
                     help="ドメイン=URL の対応（例 kanri.example.co.jp=http://127.0.0.1:9101）")
    run.add_argument("--limit", type=int, default=None, help="処理件数の上限")
    run.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    run.add_argument("--browser-path", default=None, help="Chromium の実行ファイルパス")
    run.add_argument("--yes", action="store_true", help="本番実行の確認を省略する")
    run.set_defaults(func=cmd_run)

    hist = sub.add_parser("history", help="実行履歴を見る")
    hist.add_argument("--limit", type=int, default=20)
    hist.set_defaults(func=cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
