"""収集スコープの定義。

「何を収集しているか」をユーザーへ提示する唯一の情報源。
ここに書かれていないものは収集しない。UI・エージェント・監査ログの
すべてがこの定義を参照する。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scope:
    key: str
    label: str                 # 画面表示名
    description: str           # 何を取るか
    collected: list[str]       # 実際に保存される項目
    not_collected: list[str]   # 明示的に保存しない項目
    default_enabled: bool
    required: bool = False     # これを切ると分析が成立しない中核スコープ
    sensitivity: str = "low"   # low / medium


SCOPES: tuple[Scope, ...] = (
    Scope(
        key="app_usage",
        label="使用アプリケーションと使用時間",
        description="前面に表示されているアプリ名と、その滞在時間を記録します。",
        collected=["アプリ名", "アプリ種別", "開始・終了時刻", "使用秒数"],
        not_collected=["アプリ内の入力内容", "スクリーンショット", "画面の中身"],
        default_enabled=True,
        required=True,
    ),
    Scope(
        key="window_title",
        label="ウィンドウタイトル",
        description=(
            "業務の中身を推定するためにウィンドウタイトルを記録します。"
            "機密パターンに一致するものは保存前に破棄します。"
        ),
        collected=["マスク処理済みのウィンドウタイトル"],
        not_collected=["パスワード入力画面のタイトル", "個人名・番号などのPII", "本文テキスト"],
        default_enabled=True,
        sensitivity="medium",
    ),
    Scope(
        key="browser_usage",
        label="Webブラウザの利用",
        description="業務システムやWebサイトの利用時間を、ドメイン単位で記録します。",
        collected=["ドメイン名", "URLのパス形状（IDは :id へ正規化）", "滞在時間"],
        not_collected=["完全なURL", "クエリ文字列", "ページ本文", "フォーム入力値", "Cookie"],
        default_enabled=True,
        sensitivity="medium",
    ),
    Scope(
        key="file_ops",
        label="ファイル操作",
        description="ファイルの作成・保存・リネーム・移動といった操作の種別を記録します。",
        collected=["操作の種別", "拡張子", "保存先フォルダの種別", "時刻"],
        not_collected=["ファイルの中身", "フルパス", "ファイル名そのもの"],
        default_enabled=True,
    ),
    Scope(
        key="input_activity",
        label="操作イベント（量のみ）",
        description=(
            "キー入力・クリックの『発生量』のみを記録します。"
            "キーロギングは行いません。押されたキーの種類は一切保存しません。"
        ),
        collected=["一定時間あたりの入力回数", "クリック回数", "アクティブ／アイドルの別"],
        not_collected=["押下したキー", "入力された文字列", "マウス座標"],
        default_enabled=True,
    ),
    Scope(
        key="clipboard_meta",
        label="コピー＆ペーストの発生",
        description=(
            "転記作業の検出のために『コピー／貼り付けが起きた事実』と"
            "文字数の桁だけを記録します。クリップボードの中身は保存しません。"
        ),
        collected=["操作種別（コピー／貼り付け）", "文字数の桁（例: 10-99）", "アプリ間の移動"],
        not_collected=["クリップボードの内容", "コピーされた文字列"],
        default_enabled=True,
        sensitivity="medium",
    ),
    Scope(
        key="work_hours",
        label="作業の開始・終了",
        description="PCでの作業開始・終了、離席（アイドル）の時刻を記録します。",
        collected=["作業開始時刻", "作業終了時刻", "アイドル区間"],
        not_collected=["業務時間外の私的利用の詳細"],
        default_enabled=True,
        required=True,
    ),
)

SCOPES_BY_KEY: dict[str, Scope] = {s.key: s for s in SCOPES}
ALL_SCOPE_KEYS: tuple[str, ...] = tuple(s.key for s in SCOPES)
DEFAULT_CONSENT: dict[str, bool] = {s.key: s.default_enabled for s in SCOPES}


def scope(key: str) -> Scope:
    return SCOPES_BY_KEY[key]
