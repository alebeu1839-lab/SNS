"""レシピ: 書類を作ってメールで送る（送信は人が承認）。

STEP1 が第3位に挙げた「見積書をPDFで作成しメール送付」の実装。

**この業務の要点は、自動化してよい範囲を正しく切ることにある。**
帳票の作成は完全に機械の仕事だが、送信は違う。宛先や金額を間違えたメールは
取り消せず、相手にそのまま届く。だからこのレシピは
**下書きを作るところで必ず止まる**。送信APIは呼ばない。
step2_spec の human_in_the_loop がそのまま構造になっている。

入力は前工程（mail_to_ledger）が作った顧客台帳。
台帳の希望車種・予算と在庫を突き合わせ、条件に合う車両があるときだけ
見積書を作る。合う車両が無いのは「担当者が提案を考える」仕事であって、
機械が適当な代替を選ぶ場面ではない。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .mail_to_sheet import LedgerWriter

QUOTE_VALID_DAYS = 14
# 車両価格に対する諸費用の目安（実運用では会社の料率に差し替える）
FEE_RATE = 0.08


def _safe_filename(text: str) -> str:
    """ファイル名に使えない文字を落とす。"""
    normalized = unicodedata.normalize("NFKC", str(text))
    return re.sub(r'[\\/:*?"<>|\s]+', "_", normalized).strip("_")[:60] or "no_name"


class QuoteAndDraftRecipe:
    """顧客台帳 → 見積書PDF ＋ メール下書き。"""

    name = "quote_and_draft"

    def __init__(
        self,
        ledger_path: Path | str,
        inventory_base_url: str,
        mail_base_url: str,
        output_dir: Path | str,
        timeout: float = 20.0,
    ) -> None:
        self.ledger = LedgerWriter(ledger_path)
        self.inventory_base_url = inventory_base_url.rstrip("/")
        self.mail_base_url = mail_base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self._inventory: list[dict] | None = None

    # ------------------------------------------------------------ 入力
    def fetch(self) -> Iterable[dict[str, Any]]:
        """台帳のうち、まだ見積書を作っていない行。"""
        return [r for r in self.ledger.read_rows() if not r.get("見積書")]

    def key_of(self, source: dict[str, Any]) -> str:
        return str(source.get("メールID", "unknown"))

    def _cars(self) -> list[dict]:
        if self._inventory is None:
            import httpx

            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(f"{self.inventory_base_url}/api/cars")
                res.raise_for_status()
                self._inventory = res.json()
        return self._inventory

    # ------------------------------------------------------------ 判定
    def validate(self, source: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        wanted = str(source.get("希望車種") or "").strip()
        budget = source.get("予算(円)")
        name = str(source.get("氏名") or "").strip()
        mail_to = str(source.get("メールアドレス") or "").strip()

        if not (wanted and name and mail_to):
            return None, "台帳の項目が欠けています（氏名・希望車種・宛先のいずれか）"
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            return None, "予算が数値ではありません"

        matches = [
            c for c in self._cars()
            if wanted.lower() in f"{c.get('maker','')} {c.get('model','')}".lower()
            and c.get("price_yen")
        ]
        if not matches:
            return None, f"「{wanted}」に該当する在庫がありません。担当者の提案が必要です"

        affordable = [c for c in matches if int(c["price_yen"]) <= budget]
        if not affordable:
            cheapest = min(int(c["price_yen"]) for c in matches)
            return None, (
                f"在庫はありますが予算({budget:,}円)を超えます（最安 {cheapest:,}円）。"
                "値引きや代替提案は担当者の判断です"
            )

        car = min(affordable, key=lambda c: budget - int(c["price_yen"]))
        vehicle_price = int(car["price_yen"])
        fees = int(vehicle_price * FEE_RATE)
        return {
            "mail_id": self.key_of(source),
            "customer_name": name,
            "mail_to": mail_to,
            "car_id": car["id"],
            "car_label": f"{car.get('maker','')} {car.get('model','')}".strip(),
            "year": car.get("year"),
            "mileage_km": car.get("mileage_km"),
            "vehicle_price": vehicle_price,
            "fees": fees,
            "total": vehicle_price + fees,
            "budget": budget,
        }, f"在庫 {car['id']} が予算内で見つかりました"

    # ---------------------------------------------------- 生成と下書き
    def apply(self, payload: dict[str, Any]) -> None:
        pdf_path = self._render_pdf(payload)
        self._create_draft(payload, pdf_path)
        payload["pdf_path"] = str(pdf_path)

    def _render_pdf(self, payload: dict[str, Any]) -> Path:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas

        font = "HeiseiKakuGo-W5"
        pdfmetrics.registerFont(UnicodeCIDFont(font))

        self.output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()
        filename = (
            f"見積書_{_safe_filename(payload['customer_name'])}様_"
            f"{today:%Y%m%d}_{payload['mail_id']}.pdf"
        )
        path = self.output_dir / filename

        width, height = A4
        pdf = canvas.Canvas(str(path), pagesize=A4)
        pdf.setFont(font, 20)
        pdf.drawCentredString(width / 2, height - 70, "御 見 積 書")

        pdf.setFont(font, 12)
        y = height - 120
        pdf.drawString(60, y, f"{payload['customer_name']} 様")
        pdf.drawRightString(width - 60, y, f"発行日: {today:%Y年%m月%d日}")
        y -= 20
        pdf.drawRightString(
            width - 60, y,
            f"有効期限: {today.replace(day=min(today.day, 28)):%Y年%m月}"
            f"より{QUOTE_VALID_DAYS}日間",
        )

        y -= 40
        pdf.setFont(font, 14)
        pdf.drawString(60, y, f"お見積車両: {payload['car_label']}（車両番号 {payload['car_id']}）")
        pdf.setFont(font, 11)
        y -= 22
        pdf.drawString(
            70, y,
            f"年式 {payload.get('year', '-')}年 ／ 走行距離 "
            f"{int(payload['mileage_km']):,}km" if payload.get("mileage_km") else
            f"年式 {payload.get('year', '-')}年",
        )

        y -= 40
        rows = [
            ("車両本体価格", payload["vehicle_price"]),
            (f"諸費用（登録・整備等 概算{int(FEE_RATE * 100)}%）", payload["fees"]),
        ]
        for label, amount in rows:
            pdf.drawString(70, y, label)
            pdf.drawRightString(width - 70, y, f"{amount:,} 円")
            y -= 22
        pdf.line(60, y + 8, width - 60, y + 8)
        pdf.setFont(font, 13)
        pdf.drawString(70, y - 12, "お支払総額")
        pdf.drawRightString(width - 70, y - 12, f"{payload['total']:,} 円")

        pdf.setFont(font, 9)
        pdf.drawString(60, 70, "※ 本見積は概算です。実際の登録費用は車検残・地域により変動します。")
        pdf.drawString(60, 55, "※ 自動生成された下書きです。送付前に担当者が内容を確認します。")
        pdf.save()
        return path

    def _create_draft(self, payload: dict[str, Any], pdf_path: Path) -> None:
        """メールの**下書き**を作る。送信はしない。"""
        import httpx

        body = (
            f"{payload['customer_name']} 様\n\n"
            "お問い合わせいただきありがとうございます。\n"
            f"ご希望に近い車両として「{payload['car_label']}」のお見積を作成いたしました。\n\n"
            f"　車両本体価格　{payload['vehicle_price']:,} 円\n"
            f"　諸費用（概算）{payload['fees']:,} 円\n"
            f"　お支払総額　　{payload['total']:,} 円\n\n"
            "詳細は添付の見積書をご確認ください。\n"
            "現車のご確認をご希望でしたら、ご都合の良い日程をお知らせください。\n\n"
            "――――――――――――\n"
            "（この下書きは自動作成されました。送信前に担当者が確認します）\n"
        )
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(
                f"{self.mail_base_url}/api/drafts",
                json={
                    "to": payload["mail_to"],
                    "subject": f"【お見積】{payload['car_label']} のご案内",
                    "body": body,
                    "attachment": pdf_path.name,
                    "ref": f"quote:{payload['mail_id']}",
                },
            )
            if res.status_code >= 400:
                raise RuntimeError(f"下書きを作成できませんでした: {res.text[:200]}")

    def finalize(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        """台帳に見積書のファイル名を書き、二重作成を防ぐ。"""
        self.ledger.update_cell(
            payload["mail_id"], "見積書", Path(payload.get("pdf_path", "")).name
        )


# ------------------------------------------------------------ 登録
def _build(spec, endpoints, output_dir=None, **_):
    """参照元=顧客台帳、書き込み先=メール、在庫は --map inventory= で渡す。"""
    ledger_path = endpoints.get("ledger") or endpoints["source"]
    inventory = endpoints.get("inventory")
    if not inventory:
        raise SystemExit(
            "在庫を照会する接続先が必要です。\n"
            '  例: --map "inventory=http://127.0.0.1:9101"'
        )
    return QuoteAndDraftRecipe(
        ledger_path=ledger_path,
        inventory_base_url=inventory,
        mail_base_url=endpoints["target"],
        output_dir=output_dir or Path(ledger_path).parent / "見積書",
    )


def _register() -> None:
    from ..registry import RecipeEntry, register

    register(RecipeEntry(
        key="quote_and_draft",
        required_endpoints=("source", "target", "inventory"),
        label="見積書を作成しメール下書きまで",
        categories=("書類作成・送付",),
        needs_browser=False,
        description=(
            "台帳の希望と在庫を突き合わせ、予算内の車両があれば見積書PDFを生成し、"
            "メールの下書きを作る。**送信はしない**（誤送信を構造的に防ぐため）。"
        ),
        factory=_build,
    ))


_register()
