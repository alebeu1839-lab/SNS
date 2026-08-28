"""リクエストごとの依存（DB接続・現在のユーザー・権限）。

MVP のため認証基盤は持たず、ローカルのエージェントプロファイルを
「ログイン中のユーザー」とみなす。SaaS 化時はここを OIDC 等へ差し替える。
権限判定のインタフェースは今の時点で入れてある。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from ..agent.profile import AgentProfile
from ..config import get_settings
from ..storage.db import init_db
from ..storage.repositories import Repositories

ROLE_RANK = {"member": 1, "manager": 2, "admin": 3}


@dataclass
class Principal:
    user_id: str
    company_id: str
    display_name: str
    role: str

    def can(self, minimum: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(minimum, 99)


_conn = None


def get_repos() -> Repositories:
    global _conn
    settings = get_settings()
    if _conn is None:
        _conn = init_db(settings.db_path)
    return Repositories(_conn)


def reset_connection() -> None:
    """テスト用: DB を切り替えるときに接続を捨てる。"""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


def get_principal(x_worklens_user: str | None = Header(default=None)) -> Principal:
    repos = get_repos()
    user = None
    if x_worklens_user:
        user = repos.get_user(x_worklens_user)
    if user is None:
        profile = AgentProfile.load(get_settings().home)
        if profile:
            user = repos.get_user(profile.user_id)
    if user is None:
        users = [u for c in repos.list_companies() for u in repos.list_users(c["id"])]
        user = users[0] if users else None
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="ユーザーが登録されていません。先にエージェントの init を実行してください。",
        )
    return Principal(user["id"], user["company_id"], user["display_name"], user["role"])


def require(principal: Principal, minimum: str) -> None:
    if not principal.can(minimum):
        raise HTTPException(
            status_code=403, detail=f"この操作には {minimum} 以上の権限が必要です。"
        )
