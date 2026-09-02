"""業務カテゴリ → 自動化レシピの対応表。

STEP1 は9種類の業務カテゴリを出す。レシピはそのうち実装済みのものだけを
受け持ち、**未対応のカテゴリでは実行を拒否する**。

ここが無いと、別の業務の候補を指定しても最初のレシピが黙って動いてしまう。
「対応していない」とはっきり言うことのほうが、それらしく動くことより重要。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class RecipeFactory(Protocol):
    def __call__(self, spec: dict[str, Any], endpoints: dict[str, str], **kwargs: Any):
        ...


@dataclass(frozen=True)
class RecipeEntry:
    key: str
    label: str
    categories: tuple[str, ...]
    needs_browser: bool
    description: str
    factory: Callable[..., Any]
    # このレシピが必要とする接続先の名前（--map で渡すキー）。
    # 何が必要かはレシピが知っている。CLI は解決結果を渡すだけ。
    required_endpoints: tuple[str, ...] = ()


_REGISTRY: dict[str, RecipeEntry] = {}


def register(entry: RecipeEntry) -> None:
    _REGISTRY[entry.key] = entry


def all_recipes() -> list[RecipeEntry]:
    return sorted(_REGISTRY.values(), key=lambda e: e.key)


def supported_categories() -> set[str]:
    return {c for e in _REGISTRY.values() for c in e.categories}


class NoRecipeError(Exception):
    """この業務に対応するレシピがまだ無い。"""

    def __init__(self, category: str, task_name: str) -> None:
        self.category = category
        self.task_name = task_name
        available = "、".join(sorted(supported_categories())) or "（なし）"
        super().__init__(
            f"「{task_name}」（{category}）に対応するレシピはまだありません。\n"
            f"  実装済みの業務種別: {available}\n"
            f"  この業務を自動化するには、worklens/step2/recipes/ にレシピを追加してください。"
        )


def select(candidate: dict[str, Any]) -> RecipeEntry:
    """候補からレシピを選ぶ。無ければ NoRecipeError。"""
    category = candidate.get("task_category") or ""
    for entry in all_recipes():
        if category in entry.categories:
            return entry
    raise NoRecipeError(category, candidate.get("task_name", "(名称不明)"))


def bootstrap() -> None:
    """recipes パッケージ内のレシピを全部読み込む。冪等。

    モジュール名を列挙せず自動で探す。レシピを1ファイル足せば、
    ここを触らなくても登録される。レシピ側は import 時に自分を登録する。
    """
    import importlib
    import pkgutil

    from . import recipes

    for module in pkgutil.iter_modules(recipes.__path__):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{recipes.__name__}.{module.name}")
