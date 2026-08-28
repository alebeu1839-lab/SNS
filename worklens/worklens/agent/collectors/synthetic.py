"""デモ／テスト用の合成ワークロード生成。

実PCコレクタ（pywin32 等）が使えない環境でも、パイプライン全体
（収集 → 分析 → 自動化候補 → ダッシュボード）を実データ相当で検証できるようにする。

生成されるのは *生イベント* であり、Redactor を必ず通してから保存される。
機密イベント（パスワードマネージャ、銀行サイト等）もあえて混ぜてあり、
除外が実際に効いていることを確認できる。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator

from ...appcatalog import categorize_app
from .base import make_event

JST = timezone(timedelta(hours=9))


@dataclass
class Step:
    app: str
    seconds: int
    title: str | None = None
    url: str | None = None
    keystrokes: int = 0
    clicks: int = 0
    clipboard: str | None = None          # copy / paste
    clipboard_len: int = 0
    file_op: str | None = None
    file_path: str | None = None


@dataclass
class Routine:
    """1つの業務ルーチン（分析側はこの区切りを知らされない）。"""

    label: str                 # 正解ラベル。テストの検証にのみ使う
    steps: list[Step]
    per_day: tuple[int, int]   # 1日あたりの実行回数（最小, 最大）
    jitter: float = 0.25       # 所要時間のばらつき
    weekday_only: bool = True


def _bucket(n: int) -> str:
    if n < 10:
        return "1-9"
    if n < 100:
        return "10-99"
    if n < 1000:
        return "100-999"
    return "1000+"


# --------------------------------------------------------------------------
# 中古車販売店のバックオフィス業務を想定したルーチン定義
# --------------------------------------------------------------------------
ROUTINES: list[Routine] = [
    Routine(
        label="中古車情報を管理システムから掲載サイトへ転記",
        per_day=(10, 14),
        steps=[
            Step("chrome.exe", 45, "車両詳細 - 在庫管理システム",
                 "https://kanri.example.co.jp/cars/48213", clicks=6),
            Step("chrome.exe", 12, "車両詳細 - 在庫管理システム",
                 "https://kanri.example.co.jp/cars/48213",
                 clipboard="copy", clipboard_len=180, clicks=4),
            Step("chrome.exe", 210, "車両情報 新規登録 - 掲載管理コンソール",
                 "https://keisai.example-portal.jp/vehicles/new",
                 keystrokes=420, clicks=28, clipboard="paste", clipboard_len=180),
            Step("chrome.exe", 30, "登録完了 - 掲載管理コンソール",
                 "https://keisai.example-portal.jp/vehicles/48213/done", clicks=3),
        ],
    ),
    Routine(
        label="問い合わせ顧客情報をExcelへ入力",
        per_day=(5, 8),
        steps=[
            Step("OUTLOOK.EXE", 55, "受信トレイ - 問い合わせ", clicks=5),
            Step("EXCEL.EXE", 165, "顧客管理.xlsx - Excel", keystrokes=260, clicks=14),
            Step("EXCEL.EXE", 10, "顧客管理.xlsx - Excel",
                 file_op="save", file_path=r"C:\業務\顧客管理.xlsx"),
        ],
    ),
    Routine(
        label="見積書をPDF化してメール送付",
        per_day=(3, 5),
        steps=[
            Step("EXCEL.EXE", 140, "見積書テンプレート.xlsx - Excel", keystrokes=180, clicks=12),
            Step("EXCEL.EXE", 15, "名前を付けて保存 - Excel",
                 file_op="create", file_path=r"C:\業務\見積\見積書_20250501.pdf"),
            Step("explorer.exe", 25, "見積 - エクスプローラー",
                 file_op="rename", file_path=r"C:\業務\見積\見積書_田中様_20250501.pdf", clicks=6),
            Step("OUTLOOK.EXE", 95, "メッセージの作成 - 見積書送付", keystrokes=140, clicks=9),
        ],
    ),
    Routine(
        label="問い合わせメールへの定型返信",
        per_day=(6, 10),
        steps=[
            Step("OUTLOOK.EXE", 35, "受信トレイ - Outlook", clicks=4),
            Step("OUTLOOK.EXE", 85, "返信 - お問い合わせありがとうございます",
                 keystrokes=150, clicks=6),
        ],
    ),
    Routine(
        label="日報を作成して提出",
        per_day=(1, 1),
        steps=[
            Step("chrome.exe", 60, "本日の実績 - 在庫管理システム",
                 "https://kanri.example.co.jp/reports/daily", clicks=5),
            Step("WINWORD.EXE", 320, "日報_20250501.docx - Word", keystrokes=520, clicks=10),
            Step("WINWORD.EXE", 10, "日報_20250501.docx - Word",
                 file_op="save", file_path=r"C:\業務\日報\日報_20250501.docx"),
        ],
    ),
    Routine(
        label="朝の在庫状況確認",
        per_day=(1, 1),
        steps=[
            Step("chrome.exe", 280, "在庫一覧 - 在庫管理システム",
                 "https://kanri.example.co.jp/cars", clicks=22),
        ],
    ),
    # --- 自動化しづらい／すべきでない業務（ランキングの妥当性検証用） -------
    Routine(
        label="オンライン商談",
        per_day=(0, 2),
        steps=[
            Step("Teams.exe", 2400, "商談 - Microsoft Teams", clicks=8),
        ],
        jitter=0.4,
    ),
    Routine(
        label="仕入れ車両の相場調査",
        per_day=(0, 2),
        steps=[
            Step("chrome.exe", 240, "検索 - Google", "https://www.google.com/search", clicks=14),
            Step("chrome.exe", 380, "相場情報 - 中古車ポータル",
                 "https://market.example-portal.jp/prices", clicks=26),
            Step("EXCEL.EXE", 200, "相場メモ.xlsx - Excel", keystrokes=90, clicks=8),
        ],
        jitter=0.5,
    ),
]

# 収集されてはいけないイベント（Redactor が落とすことの確認用）
SENSITIVE_NOISE: list[Step] = [
    Step("1Password.exe", 40, "1Password - 保管庫"),
    Step("chrome.exe", 120, "ログイン - ネットバンキング", "https://www.smbc.co.jp/login"),
    Step("chrome.exe", 60, "パスワードの変更 - アカウント設定",
         "https://kanri.example.co.jp/account/password"),
]


@dataclass
class SyntheticCollector:
    """合成ワークロード生成器。"""

    seed: int = 20250501
    routines: list[Routine] = field(default_factory=lambda: list(ROUTINES))
    workday_start: time = time(9, 5)
    workday_end: time = time(18, 15)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    name = "synthetic"

    def available(self) -> bool:
        return True

    # ------------------------------------------------------------------
    def generate_days(
        self, days: int, end_date: date | None = None
    ) -> Iterator[tuple[datetime, datetime, list[dict[str, Any]]]]:
        """(セッション開始, セッション終了, 生イベント列) を日ごとに返す。"""
        end_date = end_date or date.today()
        dates = [end_date - timedelta(days=i) for i in range(days)][::-1]
        for day in dates:
            if day.weekday() >= 5:      # 土日はスキップ
                continue
            yield self._generate_one_day(day)

    def _generate_one_day(
        self, day: date
    ) -> tuple[datetime, datetime, list[dict[str, Any]]]:
        rng = self.rng
        start = datetime.combine(day, self.workday_start, tzinfo=JST) + timedelta(
            minutes=rng.randint(-10, 25)
        )
        hard_end = datetime.combine(day, self.workday_end, tzinfo=JST)

        # その日に実行するルーチンの並びを作る（実務同様、種類は入り混じる）
        queue: list[Routine] = []
        for routine in self.routines:
            lo, hi = routine.per_day
            for _ in range(rng.randint(lo, hi)):
                queue.append(routine)
        rng.shuffle(queue)

        events: list[dict[str, Any]] = []
        cursor = start
        events.append(
            make_event(_iso(cursor), "work_start", "work_hours", detail={"source": "agent"})
        )

        for routine in queue:
            if cursor >= hard_end:
                break
            cursor = self._emit_routine(events, routine, cursor)
            # 業務と業務の間の小休止（分析側の区切り判定に使われる）
            gap = rng.choice([20, 45, 90, 150, 240, 600])
            if gap >= 240:
                events.append(
                    make_event(_iso(cursor), "idle_start", "work_hours",
                               detail={"expected_sec": gap})
                )
                events.append(
                    make_event(_iso(cursor + timedelta(seconds=gap)), "idle_end", "work_hours")
                )
            cursor += timedelta(seconds=gap)

        # 機密ノイズを1日1〜2件混ぜる（保存されないことの確認用）
        for step in rng.sample(SENSITIVE_NOISE, k=rng.randint(1, 2)):
            noise_at = start + timedelta(seconds=rng.randint(600, 20000))
            self._emit_step(events, step, noise_at)

        end = min(max(cursor, start + timedelta(hours=1)), hard_end)
        events.append(make_event(_iso(end), "work_end", "work_hours", detail={"source": "agent"}))
        events.sort(key=lambda e: e["ts"])
        return start, end, events

    def _emit_routine(
        self, events: list[dict[str, Any]], routine: Routine, cursor: datetime
    ) -> datetime:
        for step in routine.steps:
            factor = 1.0 + self.rng.uniform(-routine.jitter, routine.jitter)
            duration = max(5, int(step.seconds * factor))
            self._emit_step(events, step, cursor, duration)
            cursor += timedelta(seconds=duration)
        return cursor

    def _emit_step(
        self,
        events: list[dict[str, Any]],
        step: Step,
        at: datetime,
        duration: int | None = None,
    ) -> None:
        duration = duration or step.seconds
        cat = categorize_app(step.app)
        events.append(
            make_event(
                _iso(at), "app_focus", "app_usage",
                app_name=step.app, app_category=cat,
                detail={"duration_sec": duration},
            )
        )
        if step.title:
            events.append(
                make_event(
                    _iso(at), "window_title", "window_title",
                    app_name=step.app, app_category=cat, window_title=step.title,
                )
            )
        if step.url:
            events.append(
                make_event(
                    _iso(at), "browser_navigate", "browser_usage",
                    app_name=step.app, app_category=cat, url=step.url,
                    window_title=step.title,
                    detail={"duration_sec": duration},
                )
            )
        if step.keystrokes or step.clicks:
            events.append(
                make_event(
                    _iso(at + timedelta(seconds=max(1, duration // 2))),
                    "input_burst", "input_activity",
                    app_name=step.app, app_category=cat,
                    detail={
                        "keystrokes": step.keystrokes,
                        "clicks": step.clicks,
                        "window_sec": duration,
                    },
                )
            )
        if step.clipboard:
            events.append(
                make_event(
                    _iso(at + timedelta(seconds=max(1, duration - 2))),
                    "clipboard_op", "clipboard_meta",
                    app_name=step.app, app_category=cat,
                    detail={
                        "op": step.clipboard,
                        "length_bucket": _bucket(step.clipboard_len),
                    },
                )
            )
        if step.file_op:
            events.append(
                make_event(
                    _iso(at + timedelta(seconds=max(1, duration - 1))),
                    "file_op", "file_ops",
                    app_name=step.app, app_category=cat,
                    file_op=step.file_op, file_path=step.file_path,
                )
            )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
