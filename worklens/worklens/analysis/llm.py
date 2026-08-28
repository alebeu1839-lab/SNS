"""Claude API の薄いクライアント。

方針:
  - SDK 依存を避け httpx で直接呼ぶ（エージェント配布時の依存を減らすため）。
  - **キーが無い／失敗した場合は必ず None を返す**。呼び出し側は
    ルールベースへフォールバックし、分析が止まらないようにする。
  - 送信するのは集計済みの匿名サマリのみ。生イベントもタイトル全文も送らない。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    pass


class ClaudeClient:
    def __init__(self, api_key: str | None, model: str, timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(
        self, system: str, user: str, max_tokens: int = 8000
    ) -> Any | None:
        """JSON を返させて parse する。失敗したら None。"""
        if not self.available:
            return None
        try:
            import httpx
        except ImportError:  # pragma: no cover
            log.warning("httpx が無いため LLM を使用できません")
            return None

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    API_URL,
                    headers={
                        "x-api-key": self.api_key or "",
                        "anthropic-version": API_VERSION,
                        "content-type": "application/json",
                    },
                    json=payload,
                )
            if res.status_code >= 400:
                log.warning("Claude API エラー %s: %s", res.status_code, res.text[:300])
                return None
            body = res.json()
            text = "".join(
                block.get("text", "")
                for block in body.get("content", [])
                if block.get("type") == "text"
            )
            return extract_json(text)
        except Exception as exc:  # ネットワーク断でも分析は続行する
            log.warning("Claude API 呼び出しに失敗: %s", exc)
            return None


def extract_json(text: str) -> Any | None:
    """コードフェンス等を剥がして最初の JSON を取り出す。"""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
