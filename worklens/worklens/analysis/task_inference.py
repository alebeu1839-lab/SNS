"""業務分析AI：クラスタを「人間が理解できる業務単位」へ変換する。

2系統を持つ:
  - LLM エンジン（Claude）: 集計済みプロファイルを渡し、業務名・説明・手順を生成
  - ルールエンジン: LLM が使えない／失敗した場合の決定論的フォールバック

どちらの結果も同じ TaskDraft 構造になる。ダッシュボードは出所を
inference_source で区別して表示する。
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..appcatalog import CATEGORY_LABELS
from .llm import ClaudeClient
from .pattern import Cluster

# 業務カテゴリ
CAT_TRANSFER = "データ転記"
CAT_ENTRY = "データ入力"
CAT_DOCUMENT = "書類作成・送付"
CAT_MAIL = "メール対応"
CAT_REPORT = "報告・レポート作成"
CAT_RESEARCH = "情報収集・調査"
CAT_CHECK = "確認・モニタリング"
CAT_MEETING = "会議・打合せ"
CAT_OTHER = "その他"

# タイトルから業務対象を取り出せない汎用語
GENERIC_TITLES = {
    "受信トレイ", "検索", "無題", "ホーム", "ダッシュボード", "メール", "新しいタブ",
    "outlook", "excel", "word", "chrome", "teams",
}
_TITLE_SPLIT = re.compile(r"\s*[-—|｜–]\s*")
_TRIM_SUFFIX = re.compile(
    r"(テンプレート|一覧|詳細|新規登録|登録|作成|編集|画面|フォーム|コンソール)$"
)
_TRIM_DATE = re.compile(r"[_\s]?\d{6,8}$")
_EXT = re.compile(r"\.(xlsx?|docx?|pptx?|pdf|csv|txt)$", re.IGNORECASE)


@dataclass
class StepProfile:
    context: str
    action: str
    app_category: str
    domain: str | None
    avg_sec: int
    keystrokes_per_min: float
    clicks: int
    copies: int
    pastes: int
    file_ops: list[str]
    file_exts: list[str]
    subject: str | None
    titles: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "使用ツール": self.context,
            "動作": self.action,
            "ツール種別": CATEGORY_LABELS.get(self.app_category, self.app_category),
            "平均秒数": self.avg_sec,
            "1分あたり打鍵数": round(self.keystrokes_per_min, 1),
            "クリック数": self.clicks,
            "コピー回数": self.copies,
            "貼り付け回数": self.pastes,
            "ファイル操作": self.file_ops,
            "拡張子": self.file_exts,
            "作業対象": self.subject,
        }


@dataclass
class TaskDraft:
    """1つの業務単位。"""

    name: str
    summary: str
    category: str
    steps: list[dict[str, str]]
    apps: list[str]
    cluster: Cluster
    inference_source: str = "rule"
    confidence: float = 0.6

    @property
    def count(self) -> int:
        return self.cluster.count

    @property
    def avg_duration_sec(self) -> int:
        return self.cluster.avg_duration_sec

    @property
    def total_duration_sec(self) -> int:
        return self.cluster.total_duration_sec


# ---------------------------------------------------------------- 補助
def clean_subject(title: str | None) -> str | None:
    """ウィンドウタイトルから作業対象の名前を取り出す。"""
    if not title:
        return None
    head = _TITLE_SPLIT.split(title.strip())[0].strip()
    head = _EXT.sub("", head)
    head = _TRIM_DATE.sub("", head)
    prev = None
    while prev != head:
        prev = head
        head = _TRIM_SUFFIX.sub("", head).strip()
    if not head or head.lower() in GENERIC_TITLES or len(head) <= 1:
        return None
    if head.startswith("<"):        # マスク済みタイトル
        return None
    return head[:24]


def build_profiles(clusters: Sequence[Cluster], days_observed: int) -> list[dict[str, Any]]:
    """クラスタを LLM／ルールの双方が読める集計プロファイルへ変換する。"""
    profiles: list[dict[str, Any]] = []
    for idx, cluster in enumerate(clusters):
        width = len(cluster.signature)
        steps: list[StepProfile] = []
        for i in range(width):
            segs = [o.segments[i] for o in cluster.occurrences if len(o.segments) > i]
            if not segs:
                continue
            titles = [t for s in segs for t in s.titles]
            title_counts = Counter(titles)
            subject = None
            for title, _ in title_counts.most_common(4):
                subject = clean_subject(title)
                if subject:
                    break
            avg_sec = int(statistics.fmean(s.duration_sec for s in segs))
            keys = statistics.fmean(s.keystrokes for s in segs)
            steps.append(
                StepProfile(
                    context=segs[0].context,
                    action=segs[0].action,
                    app_category=segs[0].app_category,
                    domain=segs[0].domain,
                    avg_sec=avg_sec,
                    keystrokes_per_min=keys / max(avg_sec, 1) * 60,
                    clicks=int(statistics.fmean(s.clicks for s in segs)),
                    copies=sum(s.copies for s in segs),
                    pastes=sum(s.pastes for s in segs),
                    file_ops=sorted({op for s in segs for op in s.file_ops}),
                    file_exts=sorted({e for s in segs for e in s.file_exts}),
                    subject=subject,
                    titles=[t for t, _ in title_counts.most_common(3)],
                )
            )
        profiles.append(
            {
                "id": idx,
                "手順": [s.to_dict() for s in steps],
                "_steps": steps,
                "発生回数": cluster.count,
                "1日あたり回数": round(cluster.count / max(days_observed, 1), 2),
                "1回あたり平均秒": cluster.avg_duration_sec,
                "合計秒": cluster.total_duration_sec,
                "所要時間のばらつき": round(cluster.duration_cv, 2),
                "繰り返し度": cluster.repetition_score,
                "繰り返し作業か": cluster.is_repetitive,
            }
        )
    return profiles


# ------------------------------------------------------------ ルールエンジン
class RuleTaskNamer:
    """操作の構造から業務名・説明を組み立てる決定論エンジン。"""

    def name_task(self, profile: dict[str, Any]) -> tuple[str, str, str, list[dict[str, str]]]:
        steps: list[StepProfile] = profile["_steps"]
        contexts = [s.context for s in steps]
        actions = [s.action for s in steps]
        subjects = [s.subject for s in steps if s.subject]
        exts = sorted({e for s in steps for e in s.file_exts})
        ops = {op for s in steps for op in s.file_ops}

        # 転記の「元」はコピー操作を最優先で選ぶ（単なる閲覧より根拠が強い）
        src = next((s for s in steps if s.action == "コピー"), None) or next(
            (s for s in steps if s.action == "確認"), None
        )
        dst = next((s for s in steps if s.action == "貼り付け"), None) or next(
            (s for s in steps if s.action == "入力"), None
        )
        subject = self._pick_subject(steps, dst, src)

        name, category = self._compose(steps, contexts, actions, src, dst, subject, exts, ops)
        summary = self._summarize(profile, steps, name, subject)
        step_rows = [
            {
                "action": s.action,
                "app_name": s.context,
                "detail": self._step_detail(s),
            }
            for s in steps
        ]
        return name, summary, category, step_rows

    # -- 名前づけ本体 --------------------------------------------------
    def _compose(self, steps, contexts, actions, src, dst, subject, exts, ops):
        has_copy = any(a == "コピー" for a in actions)
        has_paste = any(a == "貼り付け" for a in actions)
        mail_steps = [s for s in steps if s.app_category == "mail"]
        meeting = [s for s in steps if s.app_category in ("meeting",) or s.context == "Teams"]
        obj = f"{subject}を" if subject else ""
        doc_idx = next(
            (i for i, s in enumerate(steps)
             if s.app_category in ("spreadsheet", "document", "presentation")
             and (set(s.file_ops) & {"create", "save"})),
            None,
        )
        mail_idx = next((i for i, s in enumerate(steps) if s.app_category == "mail"), None)
        last_mail_idx = next(
            (i for i in range(len(steps) - 1, -1, -1) if steps[i].app_category == "mail"), None
        )

        # 1) 別システム間の転記
        #    「コピーした先が別システムで、そこで書き込みが起きている」を転記とみなす。
        #    貼り付け操作は実PCでは取得できない（クリップボードの中身を読まないため）
        #    ので、入力でも成立させる。
        writes_elsewhere = bool(
            dst and src and src.context != dst.context and dst.action in ("貼り付け", "入力")
        )
        if has_copy and (has_paste or writes_elsewhere) and writes_elsewhere:
            return f"{obj}{src.context}から{dst.context}へ転記", CAT_TRANSFER

        # 2) 書類を作ってメールで送る（作成 → メール、の順序であること）
        if (
            doc_idx is not None
            and last_mail_idx is not None
            and last_mail_idx > doc_idx
            and steps[last_mail_idx].action == "入力"
        ):
            fmt = "PDF" if "pdf" in exts else (exts[0].upper() if exts else "ファイル")
            return f"{obj or '書類を'}{fmt}で作成しメール送付", CAT_DOCUMENT

        # 3) メールで受けた内容を別ツールへ入力（メール → 入力、の順序であること）
        if (
            mail_idx is not None
            and dst is not None
            and dst.app_category in ("spreadsheet", "business_system", "crm")
            and steps.index(dst) > mail_idx
        ):
            return (
                f"メールで受けた内容を{dst.context}へ入力"
                f"{f'（{subject}）' if subject else ''}",
                CAT_ENTRY,
            )

        # 4) メール内での返信作業（ファイル操作を伴わないこと）
        if len(steps) <= 2 and mail_steps and any(a == "入力" for a in actions) and not ops:
            return "問い合わせメールへの返信", CAT_MAIL

        # 4b) 書類を作ってファイル名を整える（送付は観測されていない）
        if doc_idx is not None and "rename" in ops:
            fmt = "PDF" if "pdf" in exts else (exts[0].upper() if exts else "ファイル")
            return f"{obj or '書類を'}{fmt}で作成しファイル名を整備", CAT_DOCUMENT

        # 5) 参照して文書を作る（日報・報告書）
        if dst and dst.app_category in ("document", "presentation"):
            ref = src.context if src and src is not dst else None
            base = f"{obj or '文書を'}作成"
            return (f"{ref}を参照して{base}" if ref else base), CAT_REPORT

        # 6) 複数の情報源を横断して確認する調査作業
        if len(steps) >= 2 and all(a == "確認" for a in actions):
            return f"{'・'.join(dict.fromkeys(contexts))[:40]}を横断して情報を確認", CAT_RESEARCH

        # 7) 会議
        if meeting and len(steps) == 1:
            return "オンライン会議・打合せ", CAT_MEETING

        # 8) 単一ツールでの入力
        if len(steps) == 1 and steps[0].action in ("入力", "貼り付け"):
            return f"{steps[0].context}へ{obj or 'データを'}入力", CAT_ENTRY

        # 8b) 単一ツールでのファイル作成・保存
        if len(steps) == 1 and steps[0].action in ("ファイル作成", "保存"):
            fmt = exts[0].upper() if exts else "ファイル"
            return f"{steps[0].context}で{obj or '書類を'}{fmt}として作成・保存", CAT_DOCUMENT

        # 9) 単一ツールでの確認
        if len(steps) == 1:
            return f"{steps[0].context}で{obj or '状況を'}確認", CAT_CHECK

        return f"{'→'.join(dict.fromkeys(contexts))[:40]} の一連作業", CAT_OTHER

    def _pick_subject(self, steps, dst, src) -> str | None:
        for candidate in (dst, src):
            if candidate and candidate.subject:
                return candidate.subject
        for s in steps:
            if s.subject:
                return s.subject
        return None

    def _step_detail(self, s: StepProfile) -> str:
        bits = [f"平均{_fmt_sec(s.avg_sec)}"]
        if s.keystrokes_per_min >= 40:
            bits.append(f"入力 約{int(s.keystrokes_per_min)}打鍵/分")
        if s.copies:
            bits.append("コピー操作あり")
        if s.pastes:
            bits.append("貼り付け操作あり")
        if s.file_ops:
            bits.append("ファイル操作: " + "/".join(s.file_ops))
        if s.domain:
            bits.append(f"接続先 {s.domain}")
        if s.subject:
            bits.append(f"対象「{s.subject}」")
        return "、".join(bits)

    def _summarize(self, profile, steps, name, subject) -> str:
        flow = " → ".join(f"{s.context}で{s.action}" for s in steps)
        per_day = profile["1日あたり回数"]
        avg = _fmt_sec(profile["1回あたり平均秒"])
        total_min = profile["合計秒"] // 60
        stability = (
            "所要時間が安定した定型作業"
            if profile["所要時間のばらつき"] < 0.4
            else "回ごとに所要時間の差がある作業"
        )
        return (
            f"{flow}という流れで行われている。"
            f"1日あたり約{per_day}回、1回あたり平均{avg}、観測期間の合計は約{total_min}分。"
            f"{stability}。"
        )


# ------------------------------------------------------------- LLM エンジン
SYSTEM_PROMPT = """あなたは企業の業務分析コンサルタントです。
PC操作から機械的に抽出された「作業パターン」を受け取り、
それが業務として何をしているのかを日本語で言語化します。

厳守事項:
- 操作ログの言い換え（例:「Chromeを30分使用」）は禁止。業務として意味のある単位で書く。
  良い例:「中古車情報を管理システムからコピーし、掲載サイトへ入力する作業」
- 与えられたデータから読み取れないことを断定しない。推測が入る場合は confidence を下げる。
- 出力は JSON 配列のみ。説明文やコードフェンスを付けない。

各要素の形式:
{
  "id": 入力と同じid(整数),
  "name": "業務名。25文字以内。何を何処から何処へ、が分かる具体的な名前",
  "summary": "現在の作業内容の説明。2〜3文。頻度と所要時間に触れる",
  "category": "データ転記|データ入力|書類作成・送付|メール対応|報告・レポート作成|情報収集・調査|確認・モニタリング|会議・打合せ|その他 のいずれか",
  "steps": [{"action":"確認|入力|コピー|貼り付け|保存|ファイル名変更","app_name":"ツール名","detail":"その手順の説明"}],
  "confidence": 0.0〜1.0
}"""


class LLMTaskNamer:
    def __init__(self, client: ClaudeClient) -> None:
        self.client = client

    def name_tasks(self, profiles: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        if not self.client.available or not profiles:
            return {}
        payload = [
            {k: v for k, v in p.items() if not k.startswith("_")} for p in profiles
        ]
        import json

        user = (
            "次の作業パターンそれぞれについて、業務として何をしているかを推定してください。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        result = self.client.complete_json(SYSTEM_PROMPT, user)
        if not isinstance(result, list):
            return {}
        out: dict[int, dict[str, Any]] = {}
        for item in result:
            if not isinstance(item, dict) or "id" not in item:
                continue
            try:
                out[int(item["id"])] = item
            except (TypeError, ValueError):
                continue
        return out


# ------------------------------------------------------------------ 本体
def infer_tasks(
    clusters: Sequence[Cluster],
    days_observed: int,
    client: ClaudeClient | None = None,
    min_total_sec: int = 300,
) -> tuple[list[TaskDraft], str]:
    """クラスタ群から業務単位を推定する。戻り値は (業務一覧, 使用エンジン)。"""
    targets = [c for c in clusters if c.total_duration_sec >= min_total_sec and c.count >= 2]
    profiles = build_profiles(targets, days_observed)
    rule = RuleTaskNamer()

    llm_results: dict[int, dict[str, Any]] = {}
    engine = "rule-based"
    if client and client.available:
        llm_results = LLMTaskNamer(client).name_tasks(profiles)
        if llm_results:
            engine = f"llm:{client.model}"

    drafts: list[TaskDraft] = []
    for profile, cluster in zip(profiles, targets):
        name, summary, category, steps = rule.name_task(profile)
        source = "rule"
        confidence = _rule_confidence(cluster)
        llm = llm_results.get(profile["id"])
        if llm and llm.get("name"):
            name = str(llm["name"])[:60]
            summary = str(llm.get("summary") or summary)
            category = _valid_category(llm.get("category"), category)
            llm_steps = llm.get("steps")
            if isinstance(llm_steps, list) and llm_steps:
                steps = [
                    {
                        "action": str(s.get("action", ""))[:20],
                        "app_name": str(s.get("app_name", ""))[:40],
                        "detail": str(s.get("detail", ""))[:200],
                    }
                    for s in llm_steps
                    if isinstance(s, dict)
                ]
            source = "llm"
            try:
                confidence = max(0.0, min(1.0, float(llm.get("confidence", confidence))))
            except (TypeError, ValueError):
                pass
        drafts.append(
            TaskDraft(
                name=name,
                summary=summary,
                category=category,
                steps=steps,
                apps=list(dict.fromkeys(s.context for s in profile["_steps"])),
                cluster=cluster,
                inference_source=source,
                confidence=round(confidence, 2),
            )
        )
    drafts = merge_equivalent(drafts, days_observed, rule)
    drafts.sort(key=lambda d: d.total_duration_sec, reverse=True)
    return drafts, engine


def merge_equivalent(
    drafts: list[TaskDraft], days_observed: int, rule: "RuleTaskNamer"
) -> list[TaskDraft]:
    """同じ業務と判定されたドラフトを1件へ統合する。

    パターンマイニングは「割り込みが入った回」を別クラスタとして拾うため、
    同名の業務が複数出てくる。ユーザーから見れば同じ業務なのでまとめる。
    """
    grouped: dict[tuple[str, str], list[TaskDraft]] = {}
    for d in drafts:
        grouped.setdefault((d.name, d.category), []).append(d)

    out: list[TaskDraft] = []
    for group in grouped.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        primary = max(group, key=lambda d: d.total_duration_sec)
        merged_cluster = Cluster(signature=primary.cluster.signature)
        for d in group:
            merged_cluster.occurrences.extend(d.cluster.occurrences)
        merged_cluster.occurrences.sort(key=lambda o: o.started_at)
        merged_cluster.is_repetitive = any(d.cluster.is_repetitive for d in group)
        primary.cluster = merged_cluster
        if primary.inference_source == "rule":
            profile = build_profiles([merged_cluster], days_observed)[0]
            _, primary.summary, _, _ = rule.name_task(profile)
        out.append(primary)
    return out


VALID_CATEGORIES = {
    CAT_TRANSFER, CAT_ENTRY, CAT_DOCUMENT, CAT_MAIL, CAT_REPORT,
    CAT_RESEARCH, CAT_CHECK, CAT_MEETING, CAT_OTHER,
}


def _valid_category(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value in VALID_CATEGORIES else fallback


def _rule_confidence(cluster: Cluster) -> float:
    """観測回数と安定性から、推定の確からしさを決める。"""
    base = min(0.9, 0.4 + cluster.count / 60.0)
    base -= min(0.25, cluster.duration_cv * 0.25)
    return max(0.2, round(base, 2))


def _fmt_sec(sec: int) -> str:
    if sec < 60:
        return f"{sec}秒"
    minutes = sec / 60
    if minutes < 60:
        return f"{minutes:.1f}分"
    return f"{minutes / 60:.1f}時間"
