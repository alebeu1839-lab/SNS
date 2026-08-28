"""PCエージェントのローカルプロファイル（どの企業・ユーザー・PCか）。"""
from __future__ import annotations

import json
import platform
import socket
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class AgentProfile:
    company_id: str
    user_id: str
    device_id: str
    company_name: str
    user_name: str
    hostname: str

    @staticmethod
    def path(home: Path) -> Path:
        return home / "profile.json"

    @classmethod
    def load(cls, home: Path) -> "AgentProfile | None":
        p = cls.path(home)
        if not p.exists():
            return None
        return cls(**json.loads(p.read_text(encoding="utf-8")))

    def save(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self.path(home).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def host_info() -> tuple[str, str, str]:
    return socket.gethostname(), platform.system(), platform.release()
