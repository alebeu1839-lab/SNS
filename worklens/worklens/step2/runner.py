"""STEP2 自動化の実行基盤。

STEP1 が生成した step2_spec の guardrails をコードとして実装する:

  1. 実行ログを残し、いつでも人が経緯を追えるようにする
       → 1実行 = 1 JSONL ファイル。取得・判定・書き込みを全件記録する。
  2. 1件ずつのドライラン結果を人が確認してから本番適用する
       → 既定は dry-run。本番実行は --mode live の明示が必要。
  3. 想定外パターンを検出したら自動処理を止めて人へ引き継ぐ
       → 検証に通らない件は書き込まず「引き継ぎキュー」へ入れる。
         自動処理できる件だけを進め、判断が要る件は人に残す。

レシピ（業務ごとの手順）はこの基盤に差し込む。基盤側は業務を知らない。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

STATUS_OK = "ok"
STATUS_HANDOFF = "handoff"      # 人へ引き継ぎ
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass
class Item:
    """自動化の対象1件。"""

    key: str
    source: dict[str, Any]
    payload: dict[str, Any] | None = None
    status: str = "pending"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "status": self.status, "reason": self.reason,
            "payload": self.payload,
        }


@dataclass
class RunResult:
    mode: str
    started_at: str
    finished_at: str = ""
    items: list[Item] = field(default_factory=list)
    log_path: str = ""
    error: str = ""

    def count(self, status: str) -> int:
        return sum(1 for i in self.items if i.status == status)

    @property
    def processed(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return self.count(STATUS_OK)

    @property
    def handoff(self) -> int:
        return self.count(STATUS_HANDOFF)

    @property
    def failed(self) -> int:
        return self.count(STATUS_FAILED)

    def summary(self, minutes_per_run: float = 0.0) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "handoff": self.handoff,
            "failed": self.failed,
            "skipped": self.count(STATUS_SKIPPED),
            "saved_minutes": round(self.succeeded * minutes_per_run, 1),
            "log_path": self.log_path,
        }


class Recipe(Protocol):
    """業務ごとの自動化手順。基盤はこのインタフェースだけを知る。"""

    name: str

    def fetch(self) -> Iterable[dict[str, Any]]:
        """転記元から対象データを取得する。"""

    def key_of(self, source: dict[str, Any]) -> str:
        """1件を識別するキー。ログと引き継ぎキューで使う。"""

    def validate(self, source: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """自動処理してよいか判定する。

        戻り値: (書き込むデータ, 理由)。データが None なら人へ引き継ぐ。
        """

    def apply(self, payload: dict[str, Any]) -> None:
        """転記先へ書き込む（本番実行時のみ呼ばれる）。"""

    def finalize(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        """書き込み成功後の後処理（転記元に完了印を付ける等）。"""


class AutomationRunner:
    def __init__(
        self,
        recipe: Recipe,
        log_dir: Path,
        mode: str = "dry-run",
        limit: int | None = None,
        stop_on_error: bool = True,
    ) -> None:
        self.recipe = recipe
        self.mode = mode
        self.limit = limit
        self.stop_on_error = stop_on_error
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = None

    @property
    def live(self) -> bool:
        return self.mode == "live"

    # ------------------------------------------------------------ ログ
    def _open_log(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.log_dir / f"{self.recipe.name}_{self.mode}_{stamp}.jsonl"
        self._log_file = path.open("w", encoding="utf-8")
        return path

    def log(self, event: str, **fields: Any) -> None:
        if not self._log_file:
            return
        record = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "event": event,
            **fields,
        }
        self._log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._log_file.flush()

    # ------------------------------------------------------------ 実行
    def run(self) -> RunResult:
        started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        result = RunResult(mode=self.mode, started_at=started)
        log_path = self._open_log()
        result.log_path = str(log_path)
        self.log("run.started", recipe=self.recipe.name, mode=self.mode, limit=self.limit)

        try:
            sources = list(self.recipe.fetch())
            self.log("fetch.completed", count=len(sources))
            if self.limit is not None:
                sources = sources[: self.limit]

            for source in sources:
                item = Item(key=self.recipe.key_of(source), source=source)
                result.items.append(item)

                payload, reason = self.recipe.validate(source)
                if payload is None:
                    # ガードレール3: 想定外は自動処理せず人へ引き継ぐ
                    item.status = STATUS_HANDOFF
                    item.reason = reason
                    self.log("item.handoff", key=item.key, reason=reason)
                    continue

                item.payload = payload
                if not self.live:
                    # ガードレール2: ドライランでは書き込まない
                    item.status = STATUS_OK
                    item.reason = "ドライラン（書き込みは行っていません）"
                    self.log("item.dry_run", key=item.key, payload=payload)
                    continue

                try:
                    self.recipe.apply(payload)
                    self.recipe.finalize(source, payload)
                    item.status = STATUS_OK
                    item.reason = "転記完了"
                    self.log("item.applied", key=item.key, payload=payload)
                except Exception as exc:
                    item.status = STATUS_FAILED
                    item.reason = f"{type(exc).__name__}: {exc}"
                    self.log("item.failed", key=item.key, error=item.reason)
                    if self.stop_on_error:
                        # 続けて壊すより止める。残りは未処理のまま人へ返す。
                        self.log("run.stopped", reason="書き込みに失敗したため中断しました")
                        break
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            self.log("run.error", error=result.error)
        finally:
            result.finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            self.log("run.finished", **result.summary())
            if self._log_file:
                self._log_file.close()
                self._log_file = None
        return result
