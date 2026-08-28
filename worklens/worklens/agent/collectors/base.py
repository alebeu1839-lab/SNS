"""コレクタの共通インタフェース。

コレクタは「生イベント」を返すだけ。同意チェックとマスクは Redactor が担当し、
コレクタ自身は保存も判断もしない（責務の分離＝監査しやすさ）。
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol


class Collector(Protocol):
    name: str

    def available(self) -> bool:
        """この環境で動作可能か。"""

    def poll(self) -> Iterable[dict[str, Any]]:
        """現時点の生イベントを返す。"""


def make_event(
    ts: str,
    event_type: str,
    scope_key: str,
    **fields: Any,
) -> dict[str, Any]:
    ev: dict[str, Any] = {"ts": ts, "event_type": event_type, "scope_key": scope_key}
    ev.update({k: v for k, v in fields.items() if v is not None})
    return ev
