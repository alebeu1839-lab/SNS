"""アプリ全体の設定。環境変数で上書きできる。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = Path(os.environ.get("WORKLENS_HOME", Path.home() / ".worklens"))


@dataclass(frozen=True)
class Settings:
    home: Path = DEFAULT_HOME
    db_path: Path = DEFAULT_HOME / "worklens.db"
    agent_version: str = "0.1.0"
    timezone: str = os.environ.get("WORKLENS_TZ", "Asia/Tokyo")

    # --- 分析エンジン -------------------------------------------------
    # ANTHROPIC_API_KEY があれば Claude を使い、無ければルールベースへ退避する。
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    model: str = os.environ.get("WORKLENS_MODEL", "claude-sonnet-5")
    llm_timeout_sec: float = float(os.environ.get("WORKLENS_LLM_TIMEOUT", "60"))

    # --- 分析パラメータ -----------------------------------------------
    # この秒数以上入力が無ければ「業務の区切り」とみなす
    idle_gap_sec: int = int(os.environ.get("WORKLENS_IDLE_GAP", "180"))
    # 繰り返しと判定する最小出現回数
    min_repetition: int = int(os.environ.get("WORKLENS_MIN_REPETITION", "3"))
    # 1か月あたりの営業日数（月間換算に使う）
    business_days_per_month: float = 20.0

    def resolved(self) -> "Settings":
        home = Path(os.environ.get("WORKLENS_HOME", self.home))
        db = Path(os.environ.get("WORKLENS_DB", home / "worklens.db"))
        return Settings(
            home=home,
            db_path=db,
            agent_version=self.agent_version,
            timezone=self.timezone,
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            model=os.environ.get("WORKLENS_MODEL", self.model),
            llm_timeout_sec=self.llm_timeout_sec,
            idle_gap_sec=self.idle_gap_sec,
            min_repetition=self.min_repetition,
            business_days_per_month=self.business_days_per_month,
        )


def get_settings() -> Settings:
    return Settings().resolved()
