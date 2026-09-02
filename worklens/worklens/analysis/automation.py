"""自動化候補分析AI。

業務ごとに次を評価し、優先順位をつける:
  - 自動化可能性 / 自動化方法 / 作業頻度 / 推定作業時間
  - 推定削減時間 / 難易度 / リスク / 人間による判断の必要性

スコアは決定論モデルで必ず算出する（LLM が無くても機能する）。
LLM が使える場合は、方法の具体化・リスク文言・注意点の記述を上書きする。
数値の根拠は常にモデル側に残し、LLM に数字を作らせない。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .llm import ClaudeClient
from .task_inference import (
    CAT_CHECK, CAT_DOCUMENT, CAT_ENTRY, CAT_MAIL, CAT_MEETING, CAT_OTHER,
    CAT_REPORT, CAT_RESEARCH, CAT_TRANSFER, TaskDraft,
)

# 業務カテゴリごとの自動化可能性の基準値
CATEGORY_PRIOR: dict[str, float] = {
    CAT_TRANSFER: 0.90,
    CAT_ENTRY: 0.85,
    CAT_DOCUMENT: 0.75,
    CAT_MAIL: 0.70,
    CAT_REPORT: 0.62,
    CAT_CHECK: 0.55,
    CAT_RESEARCH: 0.25,
    CAT_MEETING: 0.05,
    CAT_OTHER: 0.40,
}

# 人間の判断がどれだけ残るか（削減できない割合）
JUDGMENT_RATIO: dict[str, float] = {
    CAT_TRANSFER: 0.10,
    CAT_ENTRY: 0.12,
    CAT_DOCUMENT: 0.25,
    CAT_MAIL: 0.35,
    CAT_REPORT: 0.30,
    CAT_CHECK: 0.20,
    CAT_RESEARCH: 0.75,
    CAT_MEETING: 0.95,
    CAT_OTHER: 0.35,
}

# 自動化後も残る監視・例外対応のオーバーヘッド
RESIDUAL_OVERHEAD = {"低": 0.05, "中": 0.12, "高": 0.22}

STRUCTURED_CATEGORIES = {
    "spreadsheet", "business_system", "listing_site", "crm", "accounting", "cloud_doc",
}
DESKTOP_CATEGORIES = {"spreadsheet", "document", "presentation", "pdf", "file_manager", "editor"}


@dataclass
class Candidate:
    task: TaskDraft
    feasibility: float
    method: str
    method_detail: str
    tools: list[str]
    difficulty: str
    difficulty_score: float
    risk_level: str
    risk_notes: str
    human_judgment: str
    human_judgment_ratio: float
    frequency_per_month: float
    est_minutes_per_run: float
    est_current_minutes_month: float
    est_saved_minutes_month: float
    est_saved_cost_month_jpy: int
    priority_score: float
    rationale: str
    step2_spec: dict[str, Any] = field(default_factory=dict)
    rank: int = 0


# ------------------------------------------------------------------ 評価
def _stability(task: TaskDraft) -> float:
    return max(0.0, 1.0 - min(task.cluster.duration_cv, 1.0))


def _structured_ratio(task: TaskDraft) -> float:
    segs = task.cluster.occurrences[0].segments if task.cluster.occurrences else []
    if not segs:
        return 0.5
    hit = sum(1 for s in segs if s.app_category in STRUCTURED_CATEGORIES or s.domain)
    return hit / len(segs)


def _representative_segments(task: TaskDraft):
    return task.cluster.occurrences[0].segments if task.cluster.occurrences else []


def estimate_feasibility(task: TaskDraft) -> tuple[float, list[str]]:
    """自動化可能性を算出し、根拠となった要素も返す。"""
    prior = CATEGORY_PRIOR.get(task.category, 0.4)
    stability = _stability(task)
    repetition = task.cluster.repetition_score
    structured = _structured_ratio(task)

    score = prior + 0.15 * (stability - 0.5) + 0.20 * (repetition - 0.5) + 0.10 * (structured - 0.5)

    reasons: list[str] = [f"業務種別「{task.category}」の基準値 {prior:.0%}"]
    if repetition >= 0.6:
        reasons.append(f"手順が繰り返し観測されている（繰り返し度 {repetition:.2f}）")
    elif repetition < 0.35:
        reasons.append(f"手順の反復が弱い（繰り返し度 {repetition:.2f}）")
    if stability >= 0.6:
        reasons.append(f"所要時間が安定している（ばらつき {task.cluster.duration_cv:.2f}）")
    elif stability < 0.4:
        reasons.append(f"所要時間のばらつきが大きい（{task.cluster.duration_cv:.2f}）")
    if structured >= 0.6:
        reasons.append("扱うデータが構造化されたシステム上にある")

    if task.count < 5:
        score -= 0.08
        reasons.append("観測回数が少なく確度が低い")
    # 上限は 95%: 例外対応が完全に消えることはない
    return max(0.02, min(0.95, round(score, 2))), reasons


def choose_method(task: TaskDraft) -> tuple[str, str, list[str]]:
    """推奨する自動化方法を決める。"""
    segs = _representative_segments(task)
    domains = [s.domain for s in segs if s.domain]
    web_only = bool(segs) and all(s.app_category == "browser" or s.domain for s in segs)
    has_desktop = any(s.app_category in DESKTOP_CATEGORIES for s in segs)
    has_mail = any(s.app_category == "mail" for s in segs)
    has_files = any(s.file_ops for s in segs)

    if task.category == CAT_TRANSFER and web_only:
        return (
            "API連携（不可の場合はRPA）",
            "転記元・転記先ともにWebシステムのため、まず双方のAPI／CSV入出力の有無を確認する。"
            "APIが提供されていればサーバ間連携で完全自動化でき、無ければブラウザRPAで"
            "フォーム入力を再現する。",
            ["Playwright / Power Automate Desktop", "各システムのAPIまたはCSV入出力"],
        )
    if task.category == CAT_TRANSFER:
        return (
            "RPA（画面操作の自動化）",
            "デスクトップアプリを含む転記のため、画面操作を再現するRPAが第一候補。"
            "対象アプリがファイル入出力に対応していればスクリプト化の方が安定する。",
            ["Power Automate Desktop / UiPath", "Python (openpyxl 等)"],
        )
    if task.category == CAT_ENTRY and has_mail:
        return (
            "AI（LLM）による抽出＋スクリプト書き込み",
            "メール本文から必要項目をLLMで構造化抽出し、抽出結果をスクリプトで台帳へ書き込む。"
            "抽出結果の確信度が低い場合のみ人が確認するフローにする。",
            ["Claude API（項目抽出）", "Microsoft Graph API / IMAP", "Python (openpyxl)"],
        )
    if task.category == CAT_ENTRY:
        return (
            "スクリプト（データ連携）",
            "入力元データを取得して台帳へ書き込むスクリプトを組む。"
            "入力フォーマットが固定されているため、ルールベースで実装できる。",
            ["Python (pandas / openpyxl)", "対象システムのAPI"],
        )
    if task.category == CAT_DOCUMENT:
        return (
            "スクリプト（帳票生成）＋送信は人が承認",
            "テンプレートへの差し込み・PDF化・ファイル名付与までを自動生成し、"
            "送信直前に人が内容を確認する構成にする。誤送信リスクを構造的に排除できる。",
            ["Python (openpyxl / reportlab)", "Outlook 下書き作成 API"],
        )
    if task.category == CAT_MAIL:
        return (
            "AI（LLM）による返信下書き＋人が承認して送信",
            "過去の返信を学習データとして定型パターンを分類し、LLMで下書きを生成する。"
            "送信は人が押す運用にすることで、誤回答リスクを抑えつつ作成時間を削減できる。",
            ["Claude API", "Outlook / Gmail の下書きAPI"],
        )
    if task.category == CAT_REPORT:
        return (
            "スクリプトによる自動集計＋AIによる文章生成",
            "参照元システムから数値を自動取得して定型部分を生成し、"
            "所感など人が書くべき箇所だけを残す。",
            ["各システムのAPI", "Claude API（要約・文章化）"],
        )
    if task.category == CAT_CHECK:
        return (
            "定期実行スクリプト＋しきい値通知",
            "毎回の目視確認をやめ、条件に合致したときだけ通知する仕組みへ置き換える。",
            ["定期実行（cron / タスクスケジューラ）", "Slack / Teams 通知"],
        )
    if task.category == CAT_RESEARCH:
        return (
            "AI（LLM）による調査支援（部分自動化）",
            "情報源の収集と要約までを自動化し、判断そのものは人が行う。"
            "完全自動化には向かない。",
            ["Claude API", "Web スクレイピング"],
        )
    if task.category == CAT_MEETING:
        return (
            "自動化は非推奨（周辺業務のみ）",
            "会議そのものは自動化対象外。議事録作成や共有など周辺作業に限定して検討する。",
            ["文字起こし＋要約ツール"],
        )
    method = "RPA（画面操作の自動化）" if has_desktop else "スクリプト"
    detail = "手順が定型化されている範囲を自動化する。まず対象範囲の切り出しから着手する。"
    return method, detail, (["Power Automate Desktop"] if has_desktop else ["Python スクリプト"])


def estimate_difficulty(task: TaskDraft) -> tuple[str, float, list[str]]:
    segs = _representative_segments(task)
    systems = {s.context for s in segs}
    has_desktop = any(s.app_category in DESKTOP_CATEGORIES for s in segs)
    has_web = any(s.domain for s in segs)
    reasons: list[str] = []

    score = 0.15
    if len(systems) >= 2:
        score += 0.12 * (len(systems) - 1)
        reasons.append(f"{len(systems)}つのシステムをまたぐ")
    if has_desktop and has_web:
        score += 0.15
        reasons.append("デスクトップアプリとWebシステムが混在する")
    elif has_desktop:
        score += 0.08
        reasons.append("デスクトップアプリの画面操作を伴う")
    if task.category in (CAT_RESEARCH, CAT_MEETING):
        score += 0.25
        reasons.append("人の判断が中心の作業である")
    if task.category in (CAT_MAIL, CAT_DOCUMENT):
        score += 0.10
        reasons.append("社外へ出る成果物のため承認フローが要る")
    if task.cluster.duration_cv > 0.6:
        score += 0.10
        reasons.append("作業内容のばらつきが大きく例外処理が必要")

    score = max(0.05, min(0.98, round(score, 2)))
    level = "低" if score < 0.34 else ("中" if score < 0.67 else "高")
    if not reasons:
        reasons.append("単一ツール内で手順が完結している")
    return level, score, reasons


def estimate_risk(task: TaskDraft) -> tuple[str, str]:
    segs = _representative_segments(task)
    writes_external = any(
        s.action in ("貼り付け", "入力") and s.domain and "kanri" not in (s.domain or "")
        for s in segs
    )
    sends_mail = any(s.app_category == "mail" and s.action == "入力" for s in segs)
    touches_customer = "顧客" in task.name or "顧客" in task.summary
    read_only = all(s.action == "確認" for s in segs) if segs else False

    notes: list[str] = []
    score = 0
    if sends_mail:
        score += 2
        notes.append("社外への送信を含むため誤送信・誤回答が顧客影響に直結する（送信前の人手承認を必須にする）")
    if writes_external:
        score += 2
        notes.append("社外システムへの書き込みを含むため、誤登録時の取り消し手順を用意する")
    if touches_customer:
        score += 1
        notes.append("顧客情報を扱うため、アクセス権限と保存先の管理が必要")
    if read_only:
        notes.append("参照のみの作業であり、データを壊すリスクは小さい")
    if task.cluster.duration_cv > 0.6:
        score += 1
        notes.append("回ごとの差異が大きく、想定外パターンで誤動作する可能性がある")

    level = "低" if score <= 1 else ("中" if score <= 3 else "高")
    if not notes:
        notes.append("既存データを書き換えないため、影響範囲は限定的")
    return level, "。".join(notes) + "。"


def estimate_judgment(task: TaskDraft) -> tuple[str, float]:
    ratio = JUDGMENT_RATIO.get(task.category, 0.35)
    if task.cluster.duration_cv > 0.6:
        ratio = min(0.95, ratio + 0.1)
    label = "不要" if ratio <= 0.15 else ("一部必要" if ratio <= 0.5 else "必須")
    return label, round(ratio, 2)


# ---------------------------------------------------------------- 候補生成
def build_candidates(
    tasks: Sequence[TaskDraft],
    days_observed: int,
    business_days_per_month: float = 20.0,
    hourly_cost_jpy: int = 3500,
    client: ClaudeClient | None = None,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for task in tasks:
        feasibility, feas_reasons = estimate_feasibility(task)
        method, method_detail, tools = choose_method(task)
        difficulty, difficulty_score, diff_reasons = estimate_difficulty(task)
        risk_level, risk_notes = estimate_risk(task)
        judgment, judgment_ratio = estimate_judgment(task)

        per_day = task.count / max(days_observed, 1)
        freq_month = per_day * business_days_per_month
        minutes_per_run = task.avg_duration_sec / 60.0
        current_month = freq_month * minutes_per_run
        overhead = RESIDUAL_OVERHEAD[difficulty]
        saved_month = current_month * feasibility * (1 - judgment_ratio) * (1 - overhead)
        saved_cost = int(saved_month / 60.0 * hourly_cost_jpy)

        # 優先順位: 削減効果が大きく、実現しやすいものを上へ
        priority = saved_month * feasibility / (1.0 + difficulty_score)

        rationale = (
            "【自動化可能性】" + "、".join(feas_reasons) + "。"
            + "【難易度】" + "、".join(diff_reasons) + "。"
            + f"【削減見込み】月{freq_month:.0f}回 × 1回{minutes_per_run:.1f}分 = "
            f"月{current_month:.0f}分の作業のうち、自動化可能性{feasibility:.0%}・"
            f"人の判断が残る割合{judgment_ratio:.0%}・運用オーバーヘッド{overhead:.0%}を"
            f"差し引いて 月{saved_month:.0f}分の削減と試算。"
        )

        candidates.append(
            Candidate(
                task=task,
                feasibility=feasibility,
                method=method,
                method_detail=method_detail,
                tools=tools,
                difficulty=difficulty,
                difficulty_score=difficulty_score,
                risk_level=risk_level,
                risk_notes=risk_notes,
                human_judgment=judgment,
                human_judgment_ratio=judgment_ratio,
                frequency_per_month=round(freq_month, 1),
                est_minutes_per_run=round(minutes_per_run, 1),
                est_current_minutes_month=round(current_month, 1),
                est_saved_minutes_month=round(saved_month, 1),
                est_saved_cost_month_jpy=saved_cost,
                priority_score=round(priority, 2),
                rationale=rationale,
                step2_spec=build_step2_spec(task, method, tools, judgment),
            )
        )

    if client and client.available:
        _refine_with_llm(candidates, client)

    candidates.sort(key=lambda c: c.priority_score, reverse=True)
    for i, c in enumerate(candidates, start=1):
        c.rank = i
    return candidates


def build_step2_spec(
    task: TaskDraft, method: str, tools: list[str], judgment: str
) -> dict[str, Any]:
    """STEP2（自動化実装）へ引き渡す仕様の下書き。STEP1では生成のみ行う。"""
    segs = _representative_segments(task)
    roles = ["参照元" if s.action in ("確認", "コピー") else "書き込み先" for s in segs]
    # 全部が同じ役割だと「どこから取ってどこへ入れるか」が決まらず、
    # STEP2 が接続先を解決できない。その場合は手順の並びで決める。
    if len(roles) >= 2 and len(set(roles)) == 1:
        roles[0] = "参照元"
        roles[-1] = "書き込み先"
    return {
        "task_name": task.name,
        "trigger": _guess_trigger(task),
        "systems": [
            {
                "name": s.context,
                "kind": "web" if s.domain else "desktop",
                "domain": s.domain,
                "role": role,
            }
            for s, role in zip(segs, roles)
        ],
        "steps": [{"seq": i + 1, **s} for i, s in enumerate(task.steps)],
        "method": method,
        "tools": tools,
        "human_in_the_loop": judgment != "不要",
        "guardrails": [
            "実行ログを残し、いつでも人が経緯を追えるようにする",
            "1件ずつのドライラン結果を人が確認してから本番適用する",
            "想定外パターンを検出したら自動処理を止めて人へ引き継ぐ",
        ],
        "open_questions": [
            "対象システムにAPIまたはCSV入出力が用意されているか",
            "自動化対象外にすべき例外パターンは何か",
        ],
    }


def _guess_trigger(task: TaskDraft) -> str:
    segs = _representative_segments(task)
    if segs and segs[0].app_category == "mail":
        return "対象のメールを受信したとき"
    if task.category == CAT_REPORT:
        return "毎営業日の定時"
    if task.category == CAT_CHECK:
        return "定期実行（例: 1時間ごと）"
    return "対象データが登録・更新されたとき"


# ------------------------------------------------- LLM による記述の具体化
REFINE_SYSTEM = """あなたは業務自動化の実装コンサルタントです。
渡された自動化候補について、実装方針・リスク・注意点の記述だけを具体化します。

厳守事項:
- **数値（可能性・削減時間・頻度・難易度スコア）は変更しない。** それらは計測に基づく値です。
- 事実として観測されていないシステム名やAPI名を断定しない。
- 出力は JSON 配列のみ。

形式:
{"id": 入力と同じid, "method_detail": "実装方針。3文以内", "risk_notes": "リスクと対策。3文以内",
 "tools": ["想定ツール", ...]}"""


def _refine_with_llm(candidates: list[Candidate], client: ClaudeClient) -> None:
    payload = [
        {
            "id": i,
            "業務名": c.task.name,
            "業務カテゴリ": c.task.category,
            "手順": c.task.steps,
            "使用ツール": c.task.apps,
            "自動化方法（案）": c.method,
            "難易度": c.difficulty,
            "人間の判断": c.human_judgment,
        }
        for i, c in enumerate(candidates)
    ]
    result = client.complete_json(
        REFINE_SYSTEM,
        "次の自動化候補の実装方針とリスクを具体化してください。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2),
    )
    if not isinstance(result, list):
        return
    for item in result:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item["id"])
            candidate = candidates[idx]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if item.get("method_detail"):
            candidate.method_detail = str(item["method_detail"])[:600]
        if item.get("risk_notes"):
            candidate.risk_notes = str(item["risk_notes"])[:600]
        if isinstance(item.get("tools"), list) and item["tools"]:
            candidate.tools = [str(t)[:60] for t in item["tools"]][:6]
