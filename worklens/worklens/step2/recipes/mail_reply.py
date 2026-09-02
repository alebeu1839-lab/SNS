"""レシピ: 問い合わせメールへの返信下書きを作る（送信は人）。

STEP1 が第4位に挙げた「問い合わせメールへの返信」の実装。

見積書レシピと同じく、**下書きで止める**。返信の文面は相手に直接届くもので、
間違えても取り消せない。自動化して得られるのは「書く時間」であって
「送る判断」ではない。

分類と文面生成は Claude を使い、APIキーが無ければ定型文で作る。
どちらの場合も、定型に当てはまらないものは下書きを作らず人へ回す。
中途半端な下書きは、ゼロから書くより手間が増えることがある。
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ...analysis.llm import ClaudeClient
from ..runner import SKIP

# 定型返信できる問い合わせの種類
INQUIRY_STOCK = "在庫確認"
INQUIRY_QUOTE = "見積依頼"
INQUIRY_TESTDRIVE = "試乗希望"
INQUIRY_OTHER = "その他"

NEEDS_HUMAN = re.compile(r"クレーム|苦情|至急|責任者|返金|解約|訴|弁護士")

RULE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (INQUIRY_TESTDRIVE, re.compile(r"試乗|現車確認|実車を見")),
    (INQUIRY_QUOTE, re.compile(r"見積|お見積|価格を教え|いくら")),
    (INQUIRY_STOCK, re.compile(r"在庫|まだあり|残って|購入を検討|探して")),
)

TEMPLATES: dict[str, str] = {
    INQUIRY_STOCK: (
        "{name} 様\n\n"
        "お問い合わせいただきありがとうございます。\n"
        "お問い合わせの車両について、在庫状況を確認のうえ改めてご連絡いたします。\n"
        "ご希望の条件（年式・走行距離・ご予算など）がございましたら、\n"
        "あわせてお知らせいただけますと幸いです。\n"
    ),
    INQUIRY_QUOTE: (
        "{name} 様\n\n"
        "お問い合わせいただきありがとうございます。\n"
        "お見積のご依頼を承りました。車両本体価格に加え、登録・整備等の\n"
        "諸費用を含めた総額をお出しし、追ってご案内いたします。\n"
    ),
    INQUIRY_TESTDRIVE: (
        "{name} 様\n\n"
        "お問い合わせいただきありがとうございます。\n"
        "試乗のご希望を承りました。ご来店可能な日時を2〜3候補いただけますと、\n"
        "車両を準備してお待ちいたします。\n"
    ),
}

FOOTER = (
    "\n引き続きよろしくお願いいたします。\n"
    "――――――――――――\n"
    "（この下書きは自動作成されました。送信前に担当者が確認します）\n"
)

SYSTEM_PROMPT = """あなたは中古車販売店の事務担当です。
問い合わせメールを分類し、返信の下書きを作ります。

厳守事項:
- 在庫の有無・価格・納期など、**事実を約束しない**。確認して連絡する、と書く。
- クレームや苦情、至急の対応要請は自分で返信せず needs_human を true にする。
- 定型的に返せない内容も needs_human を true にする。
- 出力は JSON オブジェクトのみ。

形式:
{"category": "在庫確認|見積依頼|試乗希望|その他",
 "needs_human": true/false,
 "reason": "人が対応すべき場合の理由",
 "reply_body": "返信本文。宛名から始め、署名は入れない"}"""


class MailReplyDraftRecipe:
    """未返信の問い合わせ → 返信下書き。"""

    name = "mail_reply_draft"

    def __init__(
        self, mail_base_url: str, client: ClaudeClient | None = None, timeout: float = 20.0
    ) -> None:
        self.mail_base_url = mail_base_url.rstrip("/")
        self.client = client
        self.timeout = timeout
        self._llm_failed = False

    # ------------------------------------------------------------ 入力
    def fetch(self) -> Iterable[dict[str, Any]]:
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(f"{self.mail_base_url}/api/messages")
            res.raise_for_status()
            return res.json()

    def key_of(self, source: dict[str, Any]) -> str:
        return str(source.get("id", "unknown"))

    # ------------------------------------------------------------ 判定
    def validate(self, source: dict[str, Any]) -> tuple[Any, str]:
        if source.get("replied"):
            return SKIP, "返信済みのため対象外です"

        body = str(source.get("body") or "")
        subject = str(source.get("subject") or "")
        # 用件が件名にしか書かれていないメールは多い。両方を見て分類する。
        classified, how = self._classify(f"{subject}\n{body}")

        if classified["needs_human"]:
            return None, (
                classified.get("reason")
                or "定型返信では対応できない内容です。担当者が読んでください"
            )
        if not classified.get("reply_body"):
            return None, f"返信文を作成できませんでした（{how}）"

        name = self._guess_name(body) or "ご担当者"
        return {
            "message_id": self.key_of(source),
            "to": str(source.get("from") or ""),
            "subject": f"Re: {source.get('subject') or 'お問い合わせ'}",
            "category": classified["category"],
            "body": classified["reply_body"].format(name=name) + FOOTER,
            "source": how,
        }, f"{how}で「{classified['category']}」として下書きを作成しました"

    def _classify(self, body: str) -> tuple[dict[str, Any], str]:
        if self.client and self.client.available and not self._llm_failed:
            result = self.client.complete_json(
                SYSTEM_PROMPT, f"次のメールを分類し、返信の下書きを作ってください。\n\n---\n{body}\n---",
                max_tokens=1500,
            )
            if isinstance(result, dict) and result.get("category"):
                return {
                    "category": str(result.get("category") or INQUIRY_OTHER),
                    "needs_human": bool(result.get("needs_human")),
                    "reason": str(result.get("reason") or ""),
                    "reply_body": str(result.get("reply_body") or ""),
                }, "AI生成"
            self._llm_failed = True
        return self._classify_by_rules(body), "定型文"

    def _classify_by_rules(self, body: str) -> dict[str, Any]:
        if NEEDS_HUMAN.search(body):
            return {
                "category": INQUIRY_OTHER, "needs_human": True,
                "reason": "クレーム・至急の対応要請の可能性があります。担当者が読んでください",
                "reply_body": "",
            }
        for category, pattern in RULE_PATTERNS:
            if pattern.search(body):
                return {
                    "category": category, "needs_human": False, "reason": "",
                    "reply_body": TEMPLATES[category],
                }
        return {
            "category": INQUIRY_OTHER, "needs_human": True,
            "reason": "定型の問い合わせに当てはまりません。担当者が読んでください",
            "reply_body": "",
        }

    @staticmethod
    def _guess_name(body: str) -> str | None:
        match = re.search(r"([一-龥ぁ-んァ-ヶーA-Za-z]{2,12})\s*(?:と申します|です[。\n])", body)
        return f"{match.group(1)}" if match else None

    # ------------------------------------------------------ 下書き作成
    def apply(self, payload: dict[str, Any]) -> None:
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(
                f"{self.mail_base_url}/api/drafts",
                json={
                    "to": payload["to"], "subject": payload["subject"],
                    "body": payload["body"], "ref": f"reply:{payload['message_id']}",
                },
            )
            if res.status_code >= 400:
                raise RuntimeError(f"下書きを作成できませんでした: {res.text[:200]}")

    def finalize(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        import httpx

        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(f"{self.mail_base_url}/api/messages/{source['id']}/replied")
            res.raise_for_status()


# ------------------------------------------------------------ 登録
def _build(spec, endpoints, client=None, **_):
    """受信箱＝下書き先。単一システムの業務なので接続先は1つ。"""
    mail = (
        endpoints.get("mail") or endpoints.get("source") or endpoints.get("target")
    )
    if not mail or not str(mail).startswith("http"):
        raise SystemExit(
            "受信箱の接続先が必要です。\n"
            '  例: --map "mail=http://127.0.0.1:9103"'
        )
    return MailReplyDraftRecipe(mail_base_url=mail, client=client)


def _register() -> None:
    from ..registry import RecipeEntry, register

    register(RecipeEntry(
        key="mail_reply_draft",
        required_endpoints=("mail",),
        label="問い合わせメールの返信下書き",
        categories=("メール対応",),
        needs_browser=False,
        description=(
            "未返信の問い合わせを分類し、返信の下書きを作る。"
            "**送信はしない**。定型に当てはまらない内容とクレームは人へ回す。"
        ),
        factory=_build,
    ))


_register()
