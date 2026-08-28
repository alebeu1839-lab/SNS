"""機密情報の除外・マスク層。

設計方針:
  1. 収集スコープが OFF のイベントは **保存前に破棄** する（DBに到達しない）。
  2. パスワードマネージャ・銀行・決済など機密性の高いアプリ／画面は
     イベントごと破棄する（DROP）。
  3. 残ったテキスト（ウィンドウタイトル等）は PII パターンをマスクする。
  4. メール本文・チャット本文はそもそも収集スコープに存在しない。
     本文らしき長文が紛れ込んだ場合は切り詰めて痕跡を残さない。
すべての破棄・マスクは件数のみを redaction_stats に記録する（内容は残さない）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

# ------------------------------------------------------------------ 定数
MAX_TITLE_LEN = 120

# イベントごと破棄するアプリ（プロセス名の部分一致・小文字比較）
SENSITIVE_APPS: tuple[str, ...] = (
    "1password", "bitwarden", "lastpass", "keepass", "dashlane", "keychain",
    "credential manager", "authenticator", "gnome-keyring", "seahorse",
)

# イベントごと破棄するウィンドウタイトルのパターン
SENSITIVE_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"パスワード", r"password", r"passphrase", r"サインイン", r"sign\s?in", r"ログイン",
        r"log\s?in", r"認証", r"二段階", r"2fa", r"one[- ]?time", r"ワンタイム",
        r"クレジットカード", r"credit\s?card", r"カード番号", r"セキュリティコード", r"cvv",
        r"口座", r"振込", r"ネットバンキング", r"銀行", r"bank(ing)?", r"決済", r"payment",
        r"給与明細", r"源泉徴収", r"マイナンバー", r"my\s?number", r"個人番号",
        r"秘密鍵", r"private\s?key", r"api[_ -]?key", r"token", r"シークレット", r"secret",
    )
)

# 機密性の高いドメイン（ブラウザイベントを破棄）
SENSITIVE_DOMAIN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(^|\.)bk\.", r"bank", r"\.jp\.rakuten-bank", r"paypay", r"paypal", r"stripe\.com",
        r"smbc", r"mufg", r"mizuho", r"jp-bank", r"nenkin", r"e-tax", r"myna\.go\.jp",
    )
)

# マスクする PII パターン（値は残さず種別ラベルへ置換）
PII_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<メール>"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "<番号>"),          # カード等の長い数字列
    (re.compile(r"\b0\d{1,4}[- ]?\d{1,4}[- ]?\d{3,4}\b"), "<電話>"),
    (re.compile(r"\b\d{3}-?\d{4}\b(?=\s*(都|道|府|県|$))"), "<郵便番号>"),
    (re.compile(r"\b\d{12}\b"), "<個人番号>"),
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<トークン>"),
)

# 本文らしさの判定（長文＋句点が多い＝メール／チャット本文の可能性）
BODY_LIKE = re.compile(r"[。．\.]\s*\S+[。．\.]")


@dataclass
class Redacted:
    """フィルタ結果。event が None なら破棄されたことを意味する。"""

    event: dict[str, Any] | None
    dropped: bool = False
    reason: str | None = None
    masked: bool = False


def _mask_text(text: str) -> tuple[str, bool]:
    masked = False
    out = text
    for pattern, label in PII_RULES:
        out, n = pattern.subn(label, out)
        masked = masked or n > 0
    if len(out) > MAX_TITLE_LEN:
        out = out[:MAX_TITLE_LEN] + "…"
        masked = True
    if BODY_LIKE.search(out):
        # 本文とみなし、構造だけ残して内容は捨てる
        out = "<本文と判定したため非保存>"
        masked = True
    return out, masked


def normalize_url(url: str) -> tuple[str | None, str | None]:
    """URL をドメインとパス形状へ落とす。クエリ・フラグメントは捨てる。"""
    if not url:
        return None, None
    try:
        parts = urlsplit(url if "//" in url else f"//{url}", scheme="https")
    except ValueError:
        return None, None
    domain = (parts.hostname or "").lower() or None
    segments = [s for s in (parts.path or "/").split("/") if s]
    shaped: list[str] = []
    for seg in segments:
        if re.fullmatch(r"\d+", seg) or len(seg) >= 16 or re.search(r"\d{4,}", seg):
            shaped.append(":id")
        else:
            shaped.append(re.sub(r"\.(html?|php|aspx?)$", "", seg)[:32])
    path_shape = "/" + "/".join(shaped[:6]) if shaped else "/"
    return domain, path_shape


def is_sensitive_app(app_name: str | None) -> bool:
    if not app_name:
        return False
    low = app_name.lower()
    return any(k in low for k in SENSITIVE_APPS)


def is_sensitive_title(title: str | None) -> bool:
    if not title:
        return False
    return any(p.search(title) for p in SENSITIVE_TITLE_PATTERNS)


def is_sensitive_domain(domain: str | None) -> bool:
    if not domain:
        return False
    return any(p.search(domain) for p in SENSITIVE_DOMAIN_PATTERNS)


class Redactor:
    """収集スコープと機密フィルタを適用する。

    consent: {scope_key: bool}
    """

    def __init__(self, consent: dict[str, bool]) -> None:
        self.consent = consent

    def allows(self, scope_key: str) -> bool:
        return bool(self.consent.get(scope_key, False))

    def apply(self, raw: dict[str, Any]) -> Redacted:
        scope_key = raw.get("scope_key", "")

        # 1) 同意していないスコープは保存しない
        if not self.allows(scope_key):
            return Redacted(None, dropped=True, reason="scope_disabled")

        event = dict(raw)
        masked = False

        # 2) 機密アプリ・機密画面はイベントごと破棄
        if is_sensitive_app(event.get("app_name")):
            return Redacted(None, dropped=True, reason="sensitive_app")
        if is_sensitive_title(event.get("window_title")):
            return Redacted(None, dropped=True, reason="sensitive_window")

        # 3) URL はドメイン＋パス形状のみ。機密ドメインは破棄
        if event.get("url"):
            domain, shape = normalize_url(event.pop("url"))
            if is_sensitive_domain(domain):
                return Redacted(None, dropped=True, reason="sensitive_domain")
            event["url_domain"] = domain
            event["url_path_shape"] = shape
        event.pop("url", None)

        # window_title スコープが OFF ならタイトルは載せない
        if event.get("window_title") and not self.allows("window_title"):
            event["window_title"] = None
            masked = True

        # 4) 残テキストの PII マスク
        if event.get("window_title"):
            event["window_title"], m = _mask_text(str(event["window_title"]))
            masked = masked or m

        # 5) ファイル操作はフルパス・ファイル名を保存しない
        if event.get("file_path"):
            path = str(event.pop("file_path"))
            ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else None
            event["file_ext"] = event.get("file_ext") or (ext[:10] if ext else None)
            masked = True
        event.pop("file_name", None)
        event.pop("file_path", None)

        # 6) 入力イベントは「量」だけ。キー内容は構造的に受け取らない
        for forbidden in ("keys", "text", "clipboard", "body", "content", "screenshot"):
            if forbidden in event:
                event.pop(forbidden)
                masked = True

        event["redacted"] = masked
        return Redacted(event, dropped=False, masked=masked)
