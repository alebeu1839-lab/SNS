"""収集イベントを安全に取り込み、保存するレコーダ。

責務:
  - セッション（作業の開始・終了）の管理
  - Redactor を必ず通す（コレクタからDBへの直通経路は存在しない）
  - 破棄・マスク件数の記録（透明性画面のデータ源）
  - app_focus イベントからアプリ使用時間を組み立てる
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from ..appcatalog import categorize_app
from ..storage.repositories import Repositories
from .privacy import Redactor
from .scopes import ALL_SCOPE_KEYS


@dataclass
class IngestStats:
    stored: int = 0
    dropped: int = 0
    masked: int = 0
    drop_reasons: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stored": self.stored,
            "dropped": self.dropped,
            "masked": self.masked,
            "drop_reasons": dict(self.drop_reasons),
        }


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class Recorder:
    def __init__(
        self,
        repos: Repositories,
        company_id: str,
        user_id: str,
        device_id: str,
        consent: dict[str, bool] | None = None,
        agent_version: str = "0.1.0",
        idle_gap_sec: int = 180,
    ) -> None:
        self.repos = repos
        self.company_id = company_id
        self.user_id = user_id
        self.device_id = device_id
        self.agent_version = agent_version
        self.idle_gap_sec = idle_gap_sec
        self.consent = consent if consent is not None else repos.consent_map(user_id, device_id)
        self.redactor = Redactor(self.consent)
        self.paused = False

    # ------------------------------------------------------------ 停止制御
    def pause(self) -> None:
        """収集停止ボタン。以降 ingest は一切保存しない。"""
        self.paused = True
        self.repos.audit(
            f"agent:{self.device_id}", "collection.paused", self.company_id,
            "device", self.device_id,
        )

    def resume(self) -> None:
        self.paused = False
        self.repos.audit(
            f"agent:{self.device_id}", "collection.resumed", self.company_id,
            "device", self.device_id,
        )

    def reload_consent(self) -> None:
        self.consent = self.repos.consent_map(self.user_id, self.device_id)
        self.redactor = Redactor(self.consent)

    # ---------------------------------------------------------- セッション
    def start_session(self, started_at: str) -> str:
        session_id = self.repos.start_session(
            self.company_id, self.user_id, self.device_id, started_at, self.agent_version
        )
        self.repos.touch_device(self.device_id)
        self.repos.audit(
            f"agent:{self.device_id}", "session.started", self.company_id,
            "session", session_id,
            {"scopes_enabled": [k for k in ALL_SCOPE_KEYS if self.consent.get(k)]},
        )
        return session_id

    def end_session(self, session_id: str, ended_at: str, paused_sec: int = 0) -> None:
        self.repos.end_session(session_id, ended_at, paused_sec)
        self.repos.audit(
            f"agent:{self.device_id}", "session.ended", self.company_id, "session", session_id
        )

    # -------------------------------------------------------------- 取り込み
    def ingest(self, session_id: str, raw_events: Iterable[dict[str, Any]]) -> IngestStats:
        stats = IngestStats()
        if self.paused:
            for _ in raw_events:
                stats.dropped += 1
                stats.drop_reasons["collection_paused"] += 1
            self.repos.bump_redaction(
                self.company_id, session_id, "collection_paused", None, stats.dropped
            )
            return stats

        rows: list[dict[str, Any]] = []
        for raw in raw_events:
            result = self.redactor.apply(raw)
            if result.event is None:
                stats.dropped += 1
                stats.drop_reasons[result.reason or "unknown"] += 1
                self.repos.bump_redaction(
                    self.company_id, session_id, result.reason or "unknown",
                    raw.get("scope_key"), 1,
                )
                continue
            if result.masked:
                stats.masked += 1
            event = result.event
            event["company_id"] = self.company_id
            event["session_id"] = session_id
            event.setdefault("app_category", categorize_app(event.get("app_name")))
            rows.append(event)

        if rows:
            self.repos.insert_events(rows)
            stats.stored = len(rows)
        if stats.masked:
            self.repos.bump_redaction(
                self.company_id, session_id, "pii_masked", None, stats.masked
            )
        return stats

    # ------------------------------------------------------- アプリ使用時間
    def build_app_usage(self, session_id: str) -> int:
        """保存済み app_focus から、アプリごとの連続使用区間を組み立てる。"""
        events = [
            e
            for e in self.repos.list_events(session_id=session_id)
            if e["event_type"] == "app_focus"
        ]
        if not events:
            return 0

        segments: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for e in events:
            ts = _parse(e["ts"])
            dur = _detail_int(e.get("detail"), "duration_sec", 0)
            end = ts + timedelta(seconds=dur) if dur else ts
            if (
                current
                and current["app_name"] == e["app_name"]
                and (ts - current["_end"]).total_seconds() <= self.idle_gap_sec
            ):
                current["_end"] = max(current["_end"], end)
                continue
            if current:
                segments.append(current)
            current = {
                "company_id": self.company_id,
                "session_id": session_id,
                "app_name": e["app_name"],
                "app_category": e.get("app_category") or categorize_app(e["app_name"]),
                "_start": ts,
                "_end": end,
            }
        if current:
            segments.append(current)

        rows: list[dict[str, Any]] = []
        for s in segments:
            duration = max(1, int((s["_end"] - s["_start"]).total_seconds()))
            rows.append(
                {
                    "company_id": s["company_id"],
                    "session_id": s["session_id"],
                    "app_name": s["app_name"],
                    "app_category": s["app_category"],
                    "started_at": s["_start"].isoformat(),
                    "ended_at": s["_end"].isoformat(),
                    "duration_sec": duration,
                }
            )
        self.repos.insert_app_usage(rows)
        return len(rows)


def _detail_int(detail: Any, key: str, default: int = 0) -> int:
    import json

    if not detail:
        return default
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            return default
    if isinstance(detail, dict):
        try:
            return int(detail.get(key, default))
        except (TypeError, ValueError):
            return default
    return default


def ingest_sessions(
    recorder: Recorder,
    sessions: Sequence[tuple[datetime, datetime, list[dict[str, Any]]]],
) -> dict[str, Any]:
    """(開始, 終了, 生イベント) の列をまとめて取り込む。"""
    total = IngestStats()
    session_ids: list[str] = []
    for start, end, events in sessions:
        sid = recorder.start_session(start.isoformat())
        stats = recorder.ingest(sid, events)
        recorder.build_app_usage(sid)
        recorder.end_session(sid, end.isoformat())
        session_ids.append(sid)
        total.stored += stats.stored
        total.dropped += stats.dropped
        total.masked += stats.masked
        total.drop_reasons.update(stats.drop_reasons)
    return {"sessions": session_ids, **total.as_dict()}
