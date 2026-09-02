"""レシピ: 目視確認を「条件に合ったときだけ通知」へ置き換える。

STEP1 が第8位に挙げた「社内管理システムで在庫を確認」の実装。

この業務の削減は、作業を速くすることではなく**やめること**で起きる。
毎朝5分かけて一覧を眺め、たいていは「特に問題なし」で終わる。
その5分は、異常が無いことを確かめるために払われている。

だから機械が代わりに全件を見て、**条件に触れたものだけを通知する**。
通知が来ない日は「見なくてよい」という情報になる。

大半の対象は「異常なし」で何もしない。それは失敗でも引き継ぎでもないので、
SKIP（対象外）として数える。ここを引き継ぎに入れると、未処理の山が
毎日積み上がって誰も見なくなる。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..runner import SKIP

LEVEL_INFO = "info"
LEVEL_WARN = "warn"


@dataclass(frozen=True)
class Rule:
    """通知する条件。実運用では会社の基準に合わせて書き換える。"""

    key: str
    label: str
    level: str
    check: Callable[[dict[str, Any]], bool]
    message: Callable[[dict[str, Any]], str]


def _price(car: dict[str, Any]) -> int | None:
    value = car.get("price_yen")
    return int(value) if value is not None else None


DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(
        key="no_price",
        label="価格が未設定",
        level=LEVEL_WARN,
        check=lambda c: _price(c) is None,
        message=lambda c: (
            f"車両 {c['id']}（{c.get('maker','')} {c.get('model','')}）の価格が未設定です。"
            "掲載できない状態です。"
        ),
    ),
    Rule(
        key="missing_mileage",
        label="走行距離が未入力",
        level=LEVEL_WARN,
        check=lambda c: c.get("mileage_km") is None,
        message=lambda c: (
            f"車両 {c['id']}（{c.get('maker','')} {c.get('model','')}）の走行距離が未入力です。"
        ),
    ),
    Rule(
        key="needs_review",
        label="備考に確認事項",
        level=LEVEL_WARN,
        check=lambda c: bool(str(c.get("note") or "").strip()),
        message=lambda c: f"車両 {c['id']} の備考: {str(c.get('note'))[:60]}",
    ),
    Rule(
        key="high_mileage",
        label="走行距離が多い",
        level=LEVEL_INFO,
        check=lambda c: (c.get("mileage_km") or 0) >= 100000,
        message=lambda c: (
            f"車両 {c['id']} の走行距離が {int(c['mileage_km']):,}km です。価格の見直し対象です。"
        ),
    ),
)


class InventoryMonitorRecipe:
    """在庫の全件チェック → 条件に触れたものだけ通知。"""

    name = "inventory_monitor"

    def __init__(
        self,
        inventory_base_url: str,
        notify_base_url: str,
        rules: tuple[Rule, ...] = DEFAULT_RULES,
        timeout: float = 20.0,
    ) -> None:
        self.inventory_base_url = inventory_base_url.rstrip("/")
        self.notify_base_url = notify_base_url.rstrip("/")
        self.rules = rules
        self.timeout = timeout

    # ------------------------------------------------------------ 入力
    def fetch(self) -> Iterable[dict[str, Any]]:
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(f"{self.inventory_base_url}/api/cars")
            res.raise_for_status()
            return res.json()

    def key_of(self, source: dict[str, Any]) -> str:
        return str(source.get("id", "unknown"))

    # ------------------------------------------------------------ 判定
    def validate(self, source: dict[str, Any]) -> tuple[Any, str]:
        hits = [r for r in self.rules if r.check(source)]
        if not hits:
            # 異常なし。何もしないことが正しい結果。
            return SKIP, "条件に該当しません（通知不要）"

        worst = LEVEL_WARN if any(r.level == LEVEL_WARN for r in hits) else LEVEL_INFO
        return {
            "car_id": self.key_of(source),
            "level": worst,
            "rules": [r.key for r in hits],
            "title": f"[{'要対応' if worst == LEVEL_WARN else '参考'}] 車両 {self.key_of(source)}",
            "body": "\n".join(r.message(source) for r in hits),
        }, "、".join(r.label for r in hits)

    # ------------------------------------------------------------ 通知
    def apply(self, payload: dict[str, Any]) -> None:
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(
                f"{self.notify_base_url}/api/notifications",
                json={
                    "title": payload["title"], "body": payload["body"],
                    "level": payload["level"], "ref": f"car:{payload['car_id']}",
                },
            )
            if res.status_code >= 400:
                raise RuntimeError(f"通知を送れませんでした: {res.text[:200]}")

    def finalize(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        return None


# ------------------------------------------------------------ 登録
def _build(spec, endpoints, **_):
    """参照元＝監視対象のシステム、書き込み先＝通知先。"""
    notify = endpoints.get("notify") or endpoints.get("target")
    if not notify or not str(notify).startswith("http"):
        raise SystemExit(
            "通知先が必要です。\n"
            '  例: --map "notify=http://127.0.0.1:9104"'
        )
    inventory = endpoints.get("inventory") or endpoints.get("source")
    if not inventory or not str(inventory).startswith("http"):
        raise SystemExit(
            "監視対象システムの接続先が必要です。\n"
            '  例: --map "inventory=http://127.0.0.1:9101"'
        )
    return InventoryMonitorRecipe(
        inventory_base_url=inventory, notify_base_url=notify
    )


def _register() -> None:
    from ..registry import RecipeEntry, register

    register(RecipeEntry(
        key="inventory_monitor",
        required_endpoints=("inventory", "notify"),
        label="目視確認を条件通知へ置き換え",
        categories=("確認・モニタリング",),
        needs_browser=False,
        description=(
            "全件を機械が確認し、条件に触れたものだけ通知する。"
            "通知が来ない日は見なくてよい、という状態を作る。"
        ),
        factory=_build,
    ))


_register()
