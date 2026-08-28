"""アプリ・ドメインの業務的な意味づけ辞書。

「Chrome を30分」ではなく「管理システムを閲覧」と言えるようにするための、
アプリ／ドメイン → 業務カテゴリ・役割のマッピング。
エージェントと分析の双方が参照する。
"""
from __future__ import annotations

import re

# アプリ名（部分一致・小文字） -> (カテゴリ, 表示名)
APP_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("excel", "spreadsheet", "Excel"),
    ("libreoffice calc", "spreadsheet", "LibreOffice Calc"),
    ("numbers", "spreadsheet", "Numbers"),
    ("word", "document", "Word"),
    ("writer", "document", "LibreOffice Writer"),
    ("powerpoint", "presentation", "PowerPoint"),
    ("outlook", "mail", "Outlook"),
    ("thunderbird", "mail", "Thunderbird"),
    ("gmail", "mail", "Gmail"),
    ("mail", "mail", "メール"),
    ("slack", "chat", "Slack"),
    ("teams", "chat", "Teams"),
    ("chatwork", "chat", "Chatwork"),
    ("zoom", "meeting", "Zoom"),
    ("acrobat", "pdf", "Acrobat"),
    ("preview", "pdf", "プレビュー"),
    ("chrome", "browser", "Chrome"),
    ("edge", "browser", "Edge"),
    ("firefox", "browser", "Firefox"),
    ("safari", "browser", "Safari"),
    ("explorer", "file_manager", "エクスプローラー"),
    ("finder", "file_manager", "Finder"),
    ("nautilus", "file_manager", "ファイル"),
    ("notepad", "editor", "メモ帳"),
    ("code", "editor", "VS Code"),
    ("freee", "accounting", "freee"),
    ("弥生", "accounting", "弥生会計"),
    ("salesforce", "crm", "Salesforce"),
    ("kintone", "business_system", "kintone"),
)

# ドメイン正規表現 -> (システム種別, 表示名)
DOMAIN_ROLES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"kanri|zaiko|inventory|erp|kintone|salesforce"), "business_system", "社内管理システム"),
    (re.compile(r"keisai|listing|portal|carsensor|goo-net|market"), "listing_site", "掲載サイト"),
    (re.compile(r"mail\.|gmail|outlook\.office"), "mail", "Webメール"),
    (re.compile(r"docs\.google|sheets\.google|drive\.google"), "cloud_doc", "Googleドキュメント"),
    (re.compile(r"slack\.com|chatwork"), "chat", "チャット"),
    (re.compile(r"freee|yayoi|moneyforward"), "accounting", "会計システム"),
    (re.compile(r"google\.com|bing\.com|yahoo\.co\.jp"), "search", "検索"),
)

CATEGORY_LABELS: dict[str, str] = {
    "spreadsheet": "表計算",
    "document": "文書作成",
    "presentation": "資料作成",
    "mail": "メール",
    "chat": "チャット",
    "meeting": "会議",
    "pdf": "PDF",
    "browser": "ブラウザ",
    "file_manager": "ファイル操作",
    "editor": "テキスト編集",
    "accounting": "会計",
    "crm": "顧客管理",
    "business_system": "業務システム",
    "listing_site": "掲載サイト",
    "cloud_doc": "クラウド文書",
    "search": "検索",
    "other": "その他",
}


def categorize_app(app_name: str | None) -> str:
    if not app_name:
        return "other"
    low = app_name.lower()
    for key, category, _ in APP_CATEGORIES:
        if key in low:
            return category
    return "other"


def display_app(app_name: str | None) -> str:
    if not app_name:
        return "不明なアプリ"
    low = app_name.lower()
    for key, _, label in APP_CATEGORIES:
        if key in low:
            return label
    return app_name


def domain_role(domain: str | None) -> tuple[str, str] | None:
    """ドメインから業務システムの役割を判定する。"""
    if not domain:
        return None
    for pattern, role, label in DOMAIN_ROLES:
        if pattern.search(domain):
            return role, label
    return "web_system", domain


def context_label(app_name: str | None, domain: str | None) -> str:
    """ユーザーに見せる『どのツールで作業しているか』の名前。"""
    role = domain_role(domain)
    if role and categorize_app(app_name) == "browser":
        return role[1]
    return display_app(app_name)
