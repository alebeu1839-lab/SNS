"""SQLite 接続とスキーマ適用。

MVP はローカル SQLite。SaaS 化時はこのモジュールだけを差し替えれば
上位（repositories 以上）は変更不要になるよう、生SQLをここに閉じ込める。
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> str:
    """全テーブルの時刻は UTC ISO8601（秒精度）で統一する。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_utc_iso(ts: str) -> str:
    """タイムゾーン付き／無しの ISO 文字列を UTC の ISO 文字列へ正規化する。

    保存時刻の表現が混ざると、期間の絞り込み（文字列比較）が壊れる。
    DB へ入る時刻はすべてここを通す。
    """
    if not ts:
        return ts
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Web ワーカースレッドから同じ接続を使うため check_same_thread=False。
    # SQLite 自体は直列化モードで動くため、接続の共有は安全。
    conn = sqlite3.connect(
        str(path), isolation_level=None, timeout=30.0, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
