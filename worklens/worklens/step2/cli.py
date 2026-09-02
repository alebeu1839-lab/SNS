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
from ..analysis.llm import ClaudeClient
from ..storage.repositories import Repositories
from . import registry
from .recipes.web_transfer import FormBrowser
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


def cmd_recipes(args: argparse.Namespace) -> int:
    """実装済みのレシピと、対応する業務種別を見せる。"""
    registry.bootstrap()
    print("=== 実装済みの自動化レシピ ===\n")
    for entry in registry.all_recipes():
        print(f"[{entry.key}] {entry.label}")
        print(f"  対応する業務種別: {'、'.join(entry.categories)}")
        if entry.required_endpoints:
            print(f"  必要な接続先: {'、'.join(entry.required_endpoints)}")
        print(f"  ブラウザ操作: {'必要' if entry.needs_browser else '不要'}")
        print(f"  {entry.description}\n")
    print(f"未対応の業務種別を指定すると、実行は拒否されます（誤った自動実行を防ぐため）。")
    return 0


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


def _resolve_endpoints(spec: dict, mapping: dict[str, str]) -> dict[str, str]:
    """spec の systems と --map から、使える接続先を集める。

    ここでは足りないと判断しない。単一システムの業務では「参照元」しか
    観測されないことがあり（通知先のように、自動化が新しく持ち込む先は
    観測時点では存在しない）、source/target が揃わないのが正常だからです。
    何が必要かはレシピが知っているので、判断はそちらに任せる。

    対応付けのキーはドメインでもシステム名でもよい。デスクトップアプリには
    ドメインが無いため（Outlook / Excel など）、名前でも引けるようにする。
    """
    resolved: dict[str, str] = {}
    role_keys = {"参照元": "source", "書き込み先": "target"}

    for system in spec.get("systems", []):
        slot = role_keys.get(system.get("role") or "")
        if not slot or slot in resolved:
            continue
        for key in (system.get("domain"), system.get("name")):
            if key and key in mapping:
                resolved[slot] = mapping[key]
                break

    # --map で渡された名前はそのまま全部渡す（inventory / notify / mail など）
    for key, value in mapping.items():
        resolved.setdefault(key, value)
    return resolved


def _endpoint_help(spec: dict) -> str:
    systems = spec.get("systems") or []
    return "、".join(
        f"{s.get('name') or s.get('domain') or '?'}（{s.get('role') or '役割なし'}）"
        for s in systems
    ) or "（この候補には systems がありません）"


def cmd_run(args: argparse.Namespace) -> int:
    repos, settings = _repos()
    candidate = repos.get_candidate(args.candidate)
    if not candidate:
        print("候補が見つかりません。`list` でIDを確認してください。", file=sys.stderr)
        return 1

    registry.bootstrap()
    try:
        entry = registry.select(candidate)
    except registry.NoRecipeError as exc:
        # ここで別のレシピを代用しない。誤った業務を自動実行するほうが害が大きい。
        print(str(exc), file=sys.stderr)
        return 2

    spec = candidate["step2_spec"] or {}
    mapping = dict(pair.split("=", 1) for pair in args.map)
    endpoints = _resolve_endpoints(spec, mapping)

    print(f"業務      : {candidate['task_name']}（{candidate['task_category']}）")
    print(f"レシピ    : {entry.label} [{entry.key}]")
    labels = {"source": "参照元", "target": "書き込み先"}
    for key, value in endpoints.items():
        print(f"{labels.get(key, key):<10}: {value}")
    print(f"モード    : {args.mode}"
          f"{'（書き込みは行いません）' if args.mode == 'dry-run' else '（実際に書き込みます）'}")
    if args.mode == "live" and not args.yes:
        answer = input("本番実行します。よろしいですか？ [y/N]: ")
        if answer.strip().lower() != "y":
            print("中止しました。")
            return 1
    print()

    if entry.required_endpoints:
        print(f"必要な接続先: {'、'.join(entry.required_endpoints)}")
        print(f"この候補のシステム: {_endpoint_help(spec)}\n")

    log_dir = Path(settings.home) / "step2_logs"
    client = ClaudeClient(settings.anthropic_api_key, settings.model, settings.llm_timeout_sec)
    needs_browser = entry.needs_browser and args.mode == "live"

    def _execute(browser) -> "RunResult":  # type: ignore[name-defined]
        # どの接続先を何に使うかはレシピが決める。CLI は解決結果を渡すだけ。
        recipe = entry.factory(
            spec=spec, endpoints=endpoints, browser=browser,
            output_dir=args.output_dir, client=client,
        )
        runner = AutomationRunner(recipe, log_dir=log_dir, mode=args.mode, limit=args.limit)
        return runner.run()

    if needs_browser:
        with FormBrowser(
            headless=not args.headed, executable_path=args.browser_path
        ) as browser:
            result = _execute(browser)
    else:
        result = _execute(None)

    summary = result.summary(minutes_per_run=candidate["est_minutes_per_run"])
    _print_result(result, summary)

    status = "failed" if result.error else ("partial" if result.failed else "succeeded")
    execution_id = repos.record_execution(
        {
            "company_id": candidate["company_id"],
            "candidate_id": candidate["id"],
            "task_id": candidate["task_id"],
            "recipe": entry.key,
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
    # 人へ回した件は、ログに埋もれさせず担当者が見られる場所へ残す
    repos.record_handoffs(
        company_id=candidate["company_id"],
        execution_id=execution_id,
        candidate_id=candidate["id"],
        task_id=candidate["task_id"],
        items=[
            {"item_key": i.key, "reason": i.reason, "context": i.source}
            for i in result.items if i.status == "handoff"
        ],
    )
    repos.audit("system", f"automation.{args.mode}", candidate["company_id"],
                "candidate", candidate["id"], summary)
    return 1 if result.error or result.failed else 0


def _summarize_payload(payload: dict | None, max_len: int = 78) -> str:
    """書き込む内容を1行で見せる。レシピごとに項目が違うので固定しない。"""
    if not payload:
        return "(内容なし)"
    parts = []
    for key, value in payload.items():
        text = str(value).strip()
        if not text:
            continue
        parts.append(f"{key}={text}" if not key.isascii() or True else text)
    line = " / ".join(parts)
    return line if len(line) <= max_len else line[:max_len] + "…"


def _print_result(result, summary: dict) -> None:
    ok = [i for i in result.items if i.status == "ok"]
    handoff = [i for i in result.items if i.status == "handoff"]
    failed = [i for i in result.items if i.status == "failed"]
    skipped = [i for i in result.items if i.status == "skipped"]

    print(f"--- 自動処理できた {len(ok)}件 ---")
    for i in ok:
        print(f"  {i.key}  {_summarize_payload(i.payload)}")
    if handoff:
        print(f"\n--- 人へ引き継ぎ {len(handoff)}件（自動処理していません） ---")
        for i in handoff:
            print(f"  {i.key}  {i.reason}")
    if failed:
        print(f"\n--- 失敗 {len(failed)}件 ---")
        for i in failed:
            print(f"  {i.key}  {i.reason}")
    if skipped:
        # 監視業務では大半がここに入る。「何もしなくてよかった」件数。
        print(f"\n--- 対象外 {len(skipped)}件（対応の必要なし） ---")
        print(f"  例: {skipped[0].reason}")
    print(f"\n処理 {summary['processed']}件"
          f" / 成功 {summary['succeeded']}"
          f" / 引き継ぎ {summary['handoff']}"
          f" / 対象外 {summary['skipped']}"
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
    run.add_argument("--output-dir", default=None,
                     help="生成物（PDF等）の出力先。既定は書き込み先の隣")
    run.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    run.add_argument("--browser-path", default=None, help="Chromium の実行ファイルパス")
    run.add_argument("--yes", action="store_true", help="本番実行の確認を省略する")
    run.set_defaults(func=cmd_run)

    rc = sub.add_parser("recipes", help="実装済みのレシピを一覧する")
    rc.set_defaults(func=cmd_recipes)

    hist = sub.add_parser("history", help="実行履歴を見る")
    hist.add_argument("--limit", type=int, default=20)
    hist.set_defaults(func=cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
