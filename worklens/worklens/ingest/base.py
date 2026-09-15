"""取り込みの共通部分。

観測機能は自作しない。CSV / JSON / 手動入力 / サンプルの4経路だけを受け、
将来 Power Automate Task Mining や UiPath Task Mining からの出力を
同じ ProcessRecord に変換して流し込めるようにしておく。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.models import METRIC_BY_KEY, REQUIRED_METRICS, VALID_SOURCES


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    process_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created, "updated": self.updated, "skipped": self.skipped,
            "errors": self.errors, "processes": len(self.process_ids),
        }


TRUE_WORDS = {"1", "true", "yes", "y", "はい", "あり", "有", "○", "◯", "o", "TRUE"}
FALSE_WORDS = {"0", "false", "no", "n", "いいえ", "なし", "無", "×", "x", "", "FALSE"}


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {w.lower() for w in TRUE_WORDS}:
        return True
    if text in {w.lower() for w in FALSE_WORDS}:
        return False
    raise ValueError(f"はい/いいえで解釈できません: {value!r}")


def to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").replace("％", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"数値として読めません: {value!r}") from exc


def normalize_source(value: Any, default: str = "declared") -> str:
    """数値の出所を正規化する。日本語表記も受ける。"""
    text = str(value or "").strip().lower()
    if not text:
        return default
    mapping = {
        "measured": "measured", "実測": "measured", "計測": "measured", "ログ": "measured",
        "declared": "declared", "申告": "declared", "ヒアリング": "declared",
        "estimated": "estimated", "推定": "estimated", "概算": "estimated",
    }
    resolved = mapping.get(text)
    if resolved is None:
        raise ValueError(
            f"数値の出所が不明です: {value!r}（実測 / 申告 / 推定 のいずれか）"
        )
    return resolved


def validate_metrics(metrics: dict[str, tuple[float, str]]) -> list[str]:
    """必須の数値が揃っているかを見る。足りなければ理由を返す。"""
    problems: list[str] = []
    for key in REQUIRED_METRICS:
        if key not in metrics:
            problems.append(f"{METRIC_BY_KEY[key].label} が未入力です")
    for key, (_, source) in metrics.items():
        if key not in METRIC_BY_KEY:
            problems.append(f"未知の指標です: {key}")
        if source not in VALID_SOURCES:
            problems.append(f"{key} の出所が不正です: {source}")
    return problems
