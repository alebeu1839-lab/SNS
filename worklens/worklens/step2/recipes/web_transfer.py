"""レシピ: Webシステム間のデータ転記。

STEP1 が第1位に挙げた「車両情報を社内管理システムから掲載サイトへ転記」の実装。
step2_spec の systems（参照元 / 書き込み先）をそのまま入力に使う。

実務でよくある組み合わせを想定している:
  - 参照元 : 読み取りAPIがある      → HTTP で取得（速い・壊れにくい）
  - 書き込み先: APIが無い           → ブラウザ自動操作でフォーム入力

「まずAPIを探し、無いところだけRPAにする」のが原則。全部RPAにすると
画面変更のたびに壊れる。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# 人の確認が要るとみなす備考のパターン
NEEDS_REVIEW = re.compile(r"要確認|要相談|保留|修復歴|事故歴", re.IGNORECASE)

# 掲載サイト側の必須項目（フォームのバリデーションに対応）
REQUIRED_FIELDS = ("vehicle_id", "maker", "model", "year", "mileage", "price", "color")


class VehicleTransferRecipe:
    """在庫管理システム → 掲載サイト の転記。"""

    name = "vehicle_transfer"

    def __init__(
        self,
        source_base_url: str,
        target_base_url: str,
        browser: "FormBrowser | None" = None,
        timeout: float = 20.0,
    ) -> None:
        self.source_base_url = source_base_url.rstrip("/")
        self.target_base_url = target_base_url.rstrip("/")
        self.browser = browser
        self.timeout = timeout

    # ------------------------------------------------------ 転記元(API)
    def fetch(self) -> Iterable[dict[str, Any]]:
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(f"{self.source_base_url}/api/cars", params={"listed": "false"})
            res.raise_for_status()
            return res.json()

    def key_of(self, source: dict[str, Any]) -> str:
        return str(source.get("id", "unknown"))

    # -------------------------------------------------------- 判定
    def validate(self, source: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """自動転記してよいかを判定する。

        判定に通らないものは書き込まず、理由を付けて人へ引き継ぐ。
        ここを甘くすると、転記先に誤った情報が入る。
        """
        note = str(source.get("note") or "")
        if NEEDS_REVIEW.search(note):
            return None, f"備考に確認事項があります（{note}）"

        if source.get("price_yen") is None:
            price_text = source.get("price_text") or "未設定"
            return None, f"価格が数値ではありません（{price_text}）。掲載価格の判断が必要です"

        if source.get("mileage_km") is None:
            return None, "走行距離が未入力です。実車の確認が必要です"

        payload = {
            "vehicle_id": str(source["id"]),
            "maker": str(source.get("maker") or ""),
            "model": str(source.get("model") or ""),
            "year": str(source.get("year") or ""),
            "mileage": str(source.get("mileage_km")),
            "price": str(source.get("price_yen")),
            "color": str(source.get("color") or ""),
            "inspection": str(source.get("inspection") or ""),
            "equipment": str(source.get("equipment") or ""),
            "comment": note,
        }
        missing = [f for f in REQUIRED_FIELDS if not payload[f]]
        if missing:
            return None, f"必須項目が不足しています: {', '.join(missing)}"
        return payload, "自動転記の条件を満たしています"

    # -------------------------------------------- 転記先(フォーム操作)
    def apply(self, payload: dict[str, Any]) -> None:
        if self.browser is None:
            raise RuntimeError("本番実行にはブラウザが必要です")
        self.browser.submit_form(
            url=f"{self.target_base_url}/vehicles/new",
            values=payload,
            submit_selector="#submit",
            success_check=lambda page_url: "/vehicles" in page_url
            and "error" not in page_url,
        )

    def finalize(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        """転記元に「掲載済み」の印を付ける（二重登録の防止）。"""
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(f"{self.source_base_url}/api/cars/{source['id']}/listed")
            res.raise_for_status()


class FormBrowser:
    """Playwright によるフォーム入力。

    RPAの実体はこれだけ。画面を開き、項目を埋め、送信し、結果を確かめる。
    確かめずに「送ったから成功」とみなす実装にすると、
    バリデーションで弾かれたことに気付けない。
    """

    def __init__(self, headless: bool = True, executable_path: str | None = None) -> None:
        self.headless = headless
        self.executable_path = executable_path
        self._playwright = None
        self._browser = None
        self._page = None

    def __enter__(self) -> "FormBrowser":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        launch: dict[str, Any] = {"headless": self.headless, "args": ["--no-sandbox"]}
        if self.executable_path:
            launch["executable_path"] = self.executable_path
        self._browser = self._playwright.chromium.launch(**launch)
        self._page = self._browser.new_page()
        return self

    def __exit__(self, *exc: Any) -> None:
        for closer in (self._browser, self._playwright):
            try:
                closer.close() if closer is self._browser else closer.stop()
            except Exception:
                pass

    def submit_form(
        self,
        url: str,
        values: dict[str, Any],
        submit_selector: str,
        success_check,
    ) -> None:
        page = self._page
        if page is None:
            raise RuntimeError("ブラウザが起動していません")
        page.goto(url, wait_until="domcontentloaded")
        for name, value in values.items():
            field = f"#{name}"
            if page.query_selector(field) is None:
                continue          # 画面に無い項目は黙って飛ばさず、後で検証で気付ける
            page.fill(field, str(value))
        page.click(submit_selector)
        page.wait_for_load_state("domcontentloaded")

        if not success_check(page.url):
            raise RuntimeError(f"転記先が登録を受け付けませんでした: {page.url}")
        # 送信後の画面にエラー表示が残っていないかも確かめる
        error = page.query_selector(".err")
        if error is not None:
            raise RuntimeError(f"転記先のエラー: {error.inner_text()[:200]}")



# ------------------------------------------------------------ 登録
def _build(spec, endpoints, browser=None, **_):
    """step2_spec とエンドポイント対応から、このレシピを組み立てる。"""
    return VehicleTransferRecipe(
        source_base_url=endpoints["source"],
        target_base_url=endpoints["target"],
        browser=browser,
    )


def _register() -> None:
    from ..registry import RecipeEntry, register

    register(RecipeEntry(
        key="web_transfer",
        label="Webシステム間のデータ転記",
        categories=("データ転記",),
        needs_browser=True,
        description=(
            "参照元から取得し、書き込み先のフォームへ入力する。"
            "参照元にAPIがあればHTTPで取得し、書き込み先だけブラウザ操作にする。"
        ),
        factory=_build,
    ))


_register()
