"""取り込み中間形式。CSV / JSON / 手動入力 / 将来のタスクマイニングを
すべてこの形に変換してから登録する。変換先を1つにしておくと、
入力経路が増えても登録側は変わらない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.models import ORIGINS
from .base import validate_metrics


@dataclass
class ToolRecord:
    name: str
    # None = 種別を指定しない（マスタに登録済みの種別を尊重する）
    kind: str | None = None
    role: str | None = None
    has_api: int = 2          # 既定は「不明」。分かっているときだけ 0/1 を入れる
    has_csv_io: int = 2
    vendor: str | None = None
    url_domain: str | None = None


@dataclass
class StepRecord:
    seq: int
    action: str
    tool_name: str | None = None
    input_info: str = ""
    output_info: str = ""
    note: str = ""


@dataclass
class ProcessRecord:
    """1業務ぶんの取り込みデータ。"""

    name: str
    summary: str = ""
    category: str = ""
    department: str | None = None
    owner: str | None = None
    has_data_entry: bool = False
    has_transcription: bool = False
    has_judgment: bool = False
    judgment_note: str = ""
    is_person_dependent: bool = False
    security_level: str = "normal"
    origin: str = "manual"
    origin_note: str = ""
    # metric_key -> (値, 出所, 根拠)
    metrics: dict[str, tuple[float, str, str]] = field(default_factory=dict)
    tools: list[ToolRecord] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.name.strip():
            out.append("業務名が空です")
        if self.origin not in ORIGINS:
            out.append(f"取り込み元が不正です: {self.origin}")
        out.extend(
            validate_metrics({k: (v[0], v[1]) for k, v in self.metrics.items()})
        )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "department": self.department, "owner": self.owner,
            "metrics": {k: {"value": v[0], "source": v[1]} for k, v in self.metrics.items()},
            "tools": [t.name for t in self.tools],
        }
