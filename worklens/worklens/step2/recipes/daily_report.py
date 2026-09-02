"""レシピ: 日報の自動集計（所感は人が書く）。

STEP1 が第5位に挙げた「社内管理システムを参照して日報を作成」の実装。

この業務の中身は2つに分かれる。
  - 数値を拾って書き写す作業 … 機械の仕事。転記ミスもここで起きる
  - その日どうだったかの所感 … 人の仕事。機械が書くと嘘になる

なので**数値部分だけを自動生成し、所感欄は空けて残す**。
LLMに所感まで書かせることもできるが、読んだ人が「本当にこの人が書いたのか」を
判断できなくなるほうが損失が大きい。事実の要約までに留める。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ...analysis.llm import ClaudeClient

SUMMARY_PROMPT = """あなたは中古車販売店の業務担当です。
その日の在庫数値から、日報の「本日の状況」欄に入れる要約を2文で書きます。

厳守事項:
- 与えられた数値だけを使う。売上・来客数など渡されていない指標に触れない。
- 所感・意見・見通しは書かない。事実の要約だけにする。
- 出力は JSON オブジェクトのみ。 形式: {"summary": "要約2文"}"""


class DailyReportRecipe:
    """在庫システムの集計 → 日報ファイル。"""

    name = "daily_report"

    def __init__(
        self,
        inventory_base_url: str,
        output_dir: Path | str,
        client: ClaudeClient | None = None,
        target_date: date | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.inventory_base_url = inventory_base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.client = client
        self.target_date = target_date or date.today()
        self.timeout = timeout

    # ------------------------------------------------------------ 入力
    def fetch(self) -> Iterable[dict[str, Any]]:
        """日報は1日1件。対象日を1件だけ返す。"""
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(f"{self.inventory_base_url}/api/cars")
            res.raise_for_status()
            cars = res.json()
        return [{"date": self.target_date.isoformat(), "cars": cars}]

    def key_of(self, source: dict[str, Any]) -> str:
        return str(source.get("date", "unknown"))

    def _path(self, day: str) -> Path:
        return self.output_dir / f"日報_{day.replace('-', '')}.md"

    # ------------------------------------------------------------ 判定
    def validate(self, source: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        cars = source.get("cars") or []
        if not cars:
            return None, "在庫データを取得できませんでした。手で確認してください"

        path = self._path(str(source["date"]))
        if path.exists():
            return None, f"{path.name} は既に作成されています。上書きは行いません"

        listed = [c for c in cars if c.get("listed")]
        unlisted = [c for c in cars if not c.get("listed")]
        priced = [int(c["price_yen"]) for c in cars if c.get("price_yen")]
        no_price = [c["id"] for c in cars if not c.get("price_yen")]

        return {
            "date": str(source["date"]),
            "total": len(cars),
            "listed": len(listed),
            "unlisted": len(unlisted),
            "unlisted_ids": [c["id"] for c in unlisted][:20],
            "avg_price": int(sum(priced) / len(priced)) if priced else 0,
            "no_price_ids": no_price,
        }, f"在庫{len(cars)}台の集計ができました"

    # ------------------------------------------------------------ 生成
    def apply(self, payload: dict[str, Any]) -> None:
        summary = self._summarize(payload)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(payload["date"])
        path.write_text(self._render(payload, summary), encoding="utf-8")
        payload["path"] = str(path)

    def _summarize(self, payload: dict[str, Any]) -> str:
        if self.client and self.client.available:
            import json

            result = self.client.complete_json(
                SUMMARY_PROMPT,
                json.dumps(
                    {k: v for k, v in payload.items() if k != "unlisted_ids"},
                    ensure_ascii=False,
                ),
                max_tokens=500,
            )
            if isinstance(result, dict) and result.get("summary"):
                return str(result["summary"])
        return (
            f"在庫は{payload['total']}台で、うち掲載済みが{payload['listed']}台、"
            f"未掲載が{payload['unlisted']}台です。"
            f"在庫の平均価格は{payload['avg_price']:,}円です。"
        )

    def _render(self, payload: dict[str, Any], summary: str) -> str:
        unlisted = (
            "、".join(str(i) for i in payload["unlisted_ids"]) if payload["unlisted_ids"]
            else "なし"
        )
        no_price = (
            "、".join(str(i) for i in payload["no_price_ids"]) if payload["no_price_ids"]
            else "なし"
        )
        return f"""# 日報　{payload['date']}

## 本日の状況（自動集計）

{summary}

| 項目 | 値 |
|---|---|
| 在庫総数 | {payload['total']} 台 |
| 掲載済み | {payload['listed']} 台 |
| 未掲載 | {payload['unlisted']} 台 |
| 平均価格 | {payload['avg_price']:,} 円 |

- 未掲載の車両: {unlisted}
- 価格未設定の車両: {no_price}

## 所感

<!-- ここは担当者が記入してください。自動生成では埋めていません。 -->


---
この日報の数値部分は {datetime.now():%Y-%m-%d %H:%M} に自動生成されました。
所感は記入されていません。提出前に確認してください。
"""

    def finalize(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        """ファイルを作った時点で完了。二重作成は validate 側で防いでいる。"""
        return None


# ------------------------------------------------------------ 登録
def _build(spec, endpoints, output_dir=None, client=None, **_):
    """参照元＝集計元のシステム。出力先はファイル。"""
    source = endpoints.get("inventory") or endpoints.get("source")
    if not source or not str(source).startswith("http"):
        raise SystemExit(
            "集計元システムの接続先が必要です。\n"
            '  例: --map "inventory=http://127.0.0.1:9101"'
        )
    target = endpoints.get("report_dir") or endpoints.get("target") or "."
    return DailyReportRecipe(
        inventory_base_url=source,
        output_dir=output_dir or (Path(target) if not target.startswith("http") else Path("日報")),
        client=client,
    )


def _register() -> None:
    from ..registry import RecipeEntry, register

    register(RecipeEntry(
        key="daily_report",
        required_endpoints=("inventory",),
        label="日報の数値部分を自動集計",
        categories=("報告・レポート作成",),
        needs_browser=False,
        description=(
            "参照元システムから数値を集計して日報を生成する。"
            "**所感欄は空けて残す**（機械が書くと、誰が書いたか分からなくなるため）。"
        ),
        factory=_build,
    ))


_register()
