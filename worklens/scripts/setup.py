#!/usr/bin/env python3
"""自分のPCで WorkLens を動かすための準備を1コマンドで行う。

  python scripts/setup.py

やること:
  1. Python のバージョン確認
  2. 必要なライブラリのインストール（OSに応じて自動で選ぶ）
  3. このPCで何が収集できるかの確認（doctor）
  4. 次にやることの案内

ネットワークやインストールを伴うので、何をするかを都度表示してから実行する。
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OS_REQUIREMENTS = {
    "Windows": "requirements-agent-windows.txt",
    "Darwin": "requirements-agent-macos.txt",
    "Linux": "requirements-agent-linux.txt",
}


def run(cmd: list[str], why: str) -> bool:
    print(f"\n▶ {why}")
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  → 失敗しました（終了コード {result.returncode}）")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-install", action="store_true", help="インストールを飛ばす")
    args = ap.parse_args()

    print("=" * 58)
    print(" WorkLens セットアップ")
    print("=" * 58)

    # --- 1. Python -----------------------------------------------------
    major, minor = sys.version_info[:2]
    print(f"\nPython {major}.{minor} / {platform.system()} {platform.release()}")
    if (major, minor) < (3, 10):
        print("Python 3.10 以上が必要です。python.org から新しい版を入れてください。")
        return 1

    # --- 2. ライブラリ --------------------------------------------------
    if not args.skip_install:
        req = OS_REQUIREMENTS.get(platform.system(), "requirements.txt")
        req_path = ROOT / req
        if not req_path.exists():
            req_path = ROOT / "requirements.txt"
        if not run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
            f"必要なライブラリを入れます（{req_path.name}）",
        ):
            print("\nインストールに失敗しました。社内プロキシ環境の場合は、"
                  "情報システム部門に pip の設定を確認してください。")
            return 1

    # --- 3. 収集可否の確認 ----------------------------------------------
    print("\n" + "=" * 58)
    from worklens.agent.cli import main as agent_main   # noqa: E402

    code = agent_main(["doctor"])

    # --- 4. 次の案内 ----------------------------------------------------
    print("\n" + "=" * 58)
    if code == 0:
        print(" 準備ができました。次はこの順で進めます。")
        print("=" * 58)
        print("""
1) 自分を登録する（会社名・氏名・メールは自由に決めて構いません）
   python -m worklens.agent.cli init --company "会社名" --name "氏名" --email you@example.com

2) 何を収集するかを確認する（収集しない項目も表示されます）
   python -m worklens.agent.cli scopes

3) まず10分だけ試す（普段どおりPCを使ってください）
   python -m worklens.agent.cli collect --source live --minutes 10

4) 結果を見る
   python -m worklens.agent.cli analyze --period-days 1
   python -m uvicorn worklens.api.app:app --port 8000
   → ブラウザで http://localhost:8000

問題なければ、5)以降を毎日動かして2週間ぶん貯めます。
   python -m worklens.agent.cli collect --source live --minutes 480
""")
    else:
        print(" このPCでは実収集ができません。")
        print("=" * 58)
        print("""
上の「対処」を実行してから、もう一度このスクリプトを動かしてください。
先に画面だけ見たい場合は、デモデータで全機能を試せます。

   python scripts/run_demo.py --reset --days 20
   python -m uvicorn worklens.api.app:app --port 8000
""")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
