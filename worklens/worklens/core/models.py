"""登録制の汎用データモデル。

特定業種を前提にしない。業種も部署も使用ソフトも「登録するもの」として扱う。
中古車販売店のサンプルは samples/ に**データとして**置き、コードには埋め込まない。

数値は必ず出所（実測 / 推定 / 申告）とセットで持つ。
「月15時間削減できます」と言うとき、その15がどこから来たのかを
画面に出せないものは、意思決定に使えないため。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ------------------------------------------------------------ 数値の出所
SOURCE_MEASURED = "measured"    # 実測（ログ・タスクマイニング等の計測値）
SOURCE_DECLARED = "declared"    # 申告（担当者へのヒアリング）
SOURCE_ESTIMATED = "estimated"  # 推定（システムが他の値から計算した）

SOURCE_LABELS = {
    SOURCE_MEASURED: "実測",
    SOURCE_DECLARED: "申告",
    SOURCE_ESTIMATED: "推定",
}
# 確からしさ。スコアの信頼度に効く。
SOURCE_CONFIDENCE = {
    SOURCE_MEASURED: 1.0,
    SOURCE_DECLARED: 0.7,
    SOURCE_ESTIMATED: 0.5,
}
VALID_SOURCES = tuple(SOURCE_LABELS)


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


# ------------------------------------------------------------ 指標の定義
@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    minimum: float | None = None
    maximum: float | None = None
    required: bool = False
    help_text: str = ""


METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec("minutes_per_run", "1回あたり作業時間", "分", 0, 24 * 60, True,
               "その業務を1回行うのにかかる時間"),
    MetricSpec("runs_per_month", "月間実行回数", "回/月", 0, 100000, True,
               "1か月に何回発生するか"),
    MetricSpec("manual_ratio", "手作業率", "0-1", 0, 1, False,
               "作業時間のうち、人が手を動かしている割合"),
    MetricSpec("pc_operation_ratio", "PC操作比率", "0-1", 0, 1, False,
               "PC上で完結する割合。電話・来客対応が混ざると下がる"),
    MetricSpec("data_entry_ratio", "データ入力比率", "0-1", 0, 1, False,
               "作業時間のうち入力に費やす割合"),
    MetricSpec("transcription_fields", "転記項目数", "項目", 0, 1000, False,
               "1回あたり何項目を別システムへ写すか"),
    MetricSpec("error_rate", "エラー発生率", "0-1", 0, 1, False,
               "やり直し・修正が発生する割合"),
)
METRIC_BY_KEY: dict[str, MetricSpec] = {m.key: m for m in METRIC_SPECS}
REQUIRED_METRICS = tuple(m.key for m in METRIC_SPECS if m.required)


@dataclass
class Metric:
    """出所つきの数値。出所の無い数値は作れない。"""

    key: str
    value: float
    source: str
    unit: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"数値の出所が不正です: {self.source!r}（{'/'.join(VALID_SOURCES)} のいずれか）"
            )
        spec = METRIC_BY_KEY.get(self.key)
        if spec:
            self.unit = self.unit or spec.unit
            if spec.minimum is not None and self.value < spec.minimum:
                raise ValueError(f"{spec.label} が範囲外です: {self.value}（最小 {spec.minimum}）")
            if spec.maximum is not None and self.value > spec.maximum:
                raise ValueError(f"{spec.label} が範囲外です: {self.value}（最大 {spec.maximum}）")

    @property
    def label(self) -> str:
        spec = METRIC_BY_KEY.get(self.key)
        return spec.label if spec else self.key

    @property
    def source_label(self) -> str:
        return source_label(self.source)

    @property
    def confidence(self) -> float:
        return SOURCE_CONFIDENCE.get(self.source, 0.5)


# ------------------------------------------------------------ エンティティ
TOOL_KINDS = ("software", "web_service", "system")
TOOL_KIND_LABELS = {
    "software": "インストール型ソフト",
    "web_service": "Webサービス",
    "system": "社内システム",
}

SECURITY_LEVELS = ("low", "normal", "high")
SECURITY_LABELS = {"low": "低", "normal": "通常", "high": "高（機密）"}

ORIGINS = ("manual", "csv", "json", "sample", "task_mining")
ORIGIN_LABELS = {
    "manual": "手動入力",
    "csv": "CSV取り込み",
    "json": "JSON取り込み",
    "sample": "サンプルデータ",
    "task_mining": "タスクマイニング連携",
}


@dataclass
class Company:
    id: str
    name: str
    industry_id: str | None = None
    industry_name: str | None = None
    employee_count: int | None = None
    hourly_cost_jpy: int = 3500
    note: str | None = None


@dataclass
class Tool:
    id: str
    name: str
    kind: str = "software"
    vendor: str | None = None
    has_api: int = 0          # 0=無 1=有 2=不明
    has_csv_io: int = 0
    url_domain: str | None = None
    note: str | None = None

    @property
    def kind_label(self) -> str:
        return TOOL_KIND_LABELS.get(self.kind, self.kind)

    @property
    def api_label(self) -> str:
        return {0: "なし", 1: "あり", 2: "不明"}.get(self.has_api, "不明")


@dataclass
class ProcessStep:
    seq: int
    action: str
    tool_id: str | None = None
    tool_name: str | None = None
    input_info: str = ""
    output_info: str = ""
    note: str = ""


@dataclass
class ProcessIssue:
    kind: str                 # error / attribution / problem
    description: str


ISSUE_LABELS = {"error": "起きるミス", "attribution": "属人化", "problem": "困りごと"}


@dataclass
class Process:
    """業務。観測でも登録でも、同じ形に落とす。"""

    id: str
    company_id: str
    name: str
    summary: str = ""
    category: str = ""
    department_id: str | None = None
    department_name: str | None = None
    owner_staff_id: str | None = None
    owner_name: str | None = None
    has_data_entry: bool = False
    has_transcription: bool = False
    has_judgment: bool = False
    judgment_note: str = ""
    is_person_dependent: bool = False
    security_level: str = "normal"
    origin: str = "manual"
    origin_note: str = ""
    metrics: dict[str, Metric] = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    steps: list[ProcessStep] = field(default_factory=list)
    issues: list[ProcessIssue] = field(default_factory=list)

    # ---------------------------------------------------------- 導出値
    def metric(self, key: str) -> Metric | None:
        return self.metrics.get(key)

    def value(self, key: str, default: float = 0.0) -> float:
        m = self.metrics.get(key)
        return m.value if m else default

    @property
    def monthly_minutes(self) -> float:
        """月間作業時間。1回あたり × 月間回数。"""
        return self.value("minutes_per_run") * self.value("runs_per_month")

    @property
    def monthly_minutes_source(self) -> str:
        """導出値の出所は、元になった数値のうち最も弱いものに合わせる。"""
        sources = [
            self.metrics[k].source for k in ("minutes_per_run", "runs_per_month")
            if k in self.metrics
        ]
        if not sources:
            return SOURCE_ESTIMATED
        order = [SOURCE_ESTIMATED, SOURCE_DECLARED, SOURCE_MEASURED]
        return min(sources, key=order.index)

    @property
    def confidence(self) -> float:
        """この業務のデータをどれだけ信じてよいか（0-1）。"""
        if not self.metrics:
            return 0.0
        required = [self.metrics[k] for k in REQUIRED_METRICS if k in self.metrics]
        if not required:
            return 0.0
        base = sum(m.confidence for m in required) / len(required)
        # 任意項目が埋まっているほど、分析の精度は上がる
        coverage = len(self.metrics) / len(METRIC_SPECS)
        return round(min(1.0, base * (0.7 + 0.3 * coverage)), 2)

    @property
    def missing_metrics(self) -> list[MetricSpec]:
        return [m for m in METRIC_SPECS if m.key not in self.metrics]

    @property
    def security_label(self) -> str:
        return SECURITY_LABELS.get(self.security_level, self.security_level)

    @property
    def origin_label(self) -> str:
        return ORIGIN_LABELS.get(self.origin, self.origin)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "summary": self.summary,
            "category": self.category, "department": self.department_name,
            "owner": self.owner_name,
            "has_data_entry": self.has_data_entry,
            "has_transcription": self.has_transcription,
            "has_judgment": self.has_judgment,
            "is_person_dependent": self.is_person_dependent,
            "security_level": self.security_level,
            "monthly_minutes": round(self.monthly_minutes, 1),
            "monthly_minutes_source": self.monthly_minutes_source,
            "confidence": self.confidence,
            "metrics": {
                k: {"value": m.value, "unit": m.unit, "source": m.source,
                    "evidence": m.evidence}
                for k, m in self.metrics.items()
            },
            "tools": [t.name for t in self.tools],
            "steps": [
                {"seq": s.seq, "action": s.action, "tool": s.tool_name,
                 "input": s.input_info, "output": s.output_info}
                for s in self.steps
            ],
            "issues": [{"kind": i.kind, "description": i.description} for i in self.issues],
        }
