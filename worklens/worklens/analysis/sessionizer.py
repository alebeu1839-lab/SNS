"""生イベント列を『作業セグメント』へ組み立てる。

セグメント = 「1つの作業文脈（どのツールで・何をしていたか）に連続して
留まっていた区間」。ここで初めて "Chrome を30分" が
"社内管理システムで参照を40秒" になる。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from ..appcatalog import categorize_app, context_label

# セグメント内で観測された操作から導く「動作」
ACTION_VIEW = "確認"
ACTION_INPUT = "入力"
ACTION_COPY = "コピー"
ACTION_PASTE = "貼り付け"
ACTION_SAVE = "保存"
ACTION_RENAME = "ファイル名変更"
ACTION_CREATE = "ファイル作成"

_FILE_OP_ACTIONS = {
    "save": ACTION_SAVE,
    "create": ACTION_CREATE,
    "rename": ACTION_RENAME,
    "move": "ファイル移動",
    "delete": "ファイル削除",
}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _detail(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


@dataclass
class Segment:
    session_id: str
    started_at: datetime
    ended_at: datetime
    app_name: str
    app_category: str
    domain: str | None = None
    path_shape: str | None = None
    context: str = ""                       # 例: 社内管理システム / Excel
    titles: list[str] = field(default_factory=list)
    keystrokes: int = 0
    clicks: int = 0
    copies: int = 0
    pastes: int = 0
    file_ops: list[str] = field(default_factory=list)
    file_exts: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    idle_before_sec: int = 0

    @property
    def duration_sec(self) -> int:
        return max(1, int((self.ended_at - self.started_at).total_seconds()))

    @property
    def action(self) -> str:
        """このセグメントで人が何をしていたか（1語）。"""
        if ACTION_RENAME in [_FILE_OP_ACTIONS.get(o, "") for o in self.file_ops]:
            return ACTION_RENAME
        if self.pastes:
            return ACTION_PASTE
        if self.copies:
            return ACTION_COPY
        # 1分あたり40打鍵以上なら、保存が同居していても主作業は「入力」
        if self.keystrokes and self.keystrokes / max(self.duration_sec, 1) * 60 >= 40:
            return ACTION_INPUT
        for op in self.file_ops:
            if op in ("create", "save"):
                return _FILE_OP_ACTIONS[op]
        return ACTION_VIEW

    @property
    def token(self) -> str:
        """繰り返し検出に使う正規化トークン。"""
        return f"{self.context}|{self.action}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_sec": self.duration_sec,
            "app_name": self.app_name,
            "app_category": self.app_category,
            "domain": self.domain,
            "path_shape": self.path_shape,
            "context": self.context,
            "action": self.action,
            "token": self.token,
            "titles": self.titles[:3],
            "keystrokes": self.keystrokes,
            "clicks": self.clicks,
            "copies": self.copies,
            "pastes": self.pastes,
            "file_ops": self.file_ops,
            "file_exts": self.file_exts,
            "event_ids": self.event_ids[:20],
        }


class Sessionizer:
    def __init__(self, idle_gap_sec: int = 180, min_segment_sec: int = 3) -> None:
        self.idle_gap_sec = idle_gap_sec
        self.min_segment_sec = min_segment_sec

    def build(self, events: Sequence[dict[str, Any]]) -> list[Segment]:
        """1セッション分のイベント（ts昇順）からセグメント列を作る。"""
        segments: list[Segment] = []
        current: Segment | None = None
        max_end: datetime | None = None
        pending_idle = 0

        def close(at: datetime) -> None:
            nonlocal current, max_end
            if current is None:
                return
            end = max_end or at
            current.ended_at = max(min(end, at), current.started_at + timedelta(seconds=1))
            if current.duration_sec >= self.min_segment_sec:
                segments.append(current)
            current = None
            max_end = None

        for ev in events:
            ts = _parse(ev["ts"])
            etype = ev["event_type"]
            detail = _detail(ev.get("detail"))

            if etype == "work_start":
                continue
            if etype == "work_end":
                close(ts)
                continue
            if etype == "idle_start":
                close(ts)
                pending_idle = int(detail.get("expected_sec", 0))
                continue
            if etype == "idle_end":
                continue

            if etype in ("app_focus", "browser_navigate"):
                app = ev.get("app_name") or "unknown"
                domain = ev.get("url_domain")
                same = (
                    current is not None
                    and current.app_name == app
                    and (current.domain == domain or domain is None)
                )
                # 同一秒に届く app_focus → browser_navigate はドメイン確定として扱う
                if (
                    current is not None
                    and current.app_name == app
                    and current.domain is None
                    and domain is not None
                    and (ts - current.started_at).total_seconds() <= 2
                ):
                    current.domain = domain
                    current.path_shape = ev.get("url_path_shape")
                    current.context = context_label(app, domain)
                    same = True
                if not same:
                    close(ts)
                    current = Segment(
                        session_id=ev["session_id"],
                        started_at=ts,
                        ended_at=ts + timedelta(seconds=1),
                        app_name=app,
                        app_category=ev.get("app_category") or categorize_app(app),
                        domain=domain,
                        path_shape=ev.get("url_path_shape"),
                        context=context_label(app, domain),
                        idle_before_sec=pending_idle,
                    )
                    pending_idle = 0
                    max_end = ts + timedelta(seconds=1)
                dur = int(detail.get("duration_sec", 0))
                if dur:
                    cand = ts + timedelta(seconds=dur)
                    max_end = max(max_end or cand, cand)
                if ev.get("window_title") and ev["window_title"] not in current.titles:
                    current.titles.append(ev["window_title"])
                current.event_ids.append(ev["id"])
                continue

            # 付随イベントは現在のセグメントへ紐づける
            if current is None:
                app = ev.get("app_name") or "unknown"
                current = Segment(
                    session_id=ev["session_id"], started_at=ts,
                    ended_at=ts + timedelta(seconds=1), app_name=app,
                    app_category=ev.get("app_category") or categorize_app(app),
                    domain=ev.get("url_domain"),
                    context=context_label(app, ev.get("url_domain")),
                    idle_before_sec=pending_idle,
                )
                pending_idle = 0
                max_end = ts + timedelta(seconds=1)

            if etype == "window_title":
                if ev.get("window_title") and ev["window_title"] not in current.titles:
                    current.titles.append(ev["window_title"])
            elif etype == "input_burst":
                current.keystrokes += int(detail.get("keystrokes", 0))
                current.clicks += int(detail.get("clicks", 0))
            elif etype == "clipboard_op":
                if detail.get("op") == "copy":
                    current.copies += 1
                elif detail.get("op") == "paste":
                    current.pastes += 1
            elif etype == "file_op":
                if ev.get("file_op"):
                    current.file_ops.append(ev["file_op"])
                if ev.get("file_ext"):
                    current.file_exts.append(ev["file_ext"])
            current.event_ids.append(ev["id"])
            max_end = max(max_end or ts, ts)

        # 最後のセグメントは、宣言された滞在時間の分まで伸ばして閉じる
        if current is not None:
            last_ts = _parse(events[-1]["ts"]) if events else current.started_at
            close(max(last_ts, max_end or last_ts))
        return segments

    def build_all(self, events_by_session: dict[str, list[dict]]) -> list[Segment]:
        out: list[Segment] = []
        for _, evs in sorted(events_by_session.items(), key=lambda kv: kv[1][0]["ts"] if kv[1] else ""):
            out.extend(self.build(evs))
        return out


def group_events_by_session(events: Iterable[dict[str, Any]]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for e in events:
        grouped.setdefault(e["session_id"], []).append(e)
    for evs in grouped.values():
        evs.sort(key=lambda e: e["ts"])
    return grouped
