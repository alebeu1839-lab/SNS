#!/usr/bin/env python3
"""練習用モックサイトを2つ同時に立ち上げる。

  python scripts/run_mocks.py
    → 社内管理システム http://127.0.0.1:9101  （読み取りAPIあり）
    → 掲載サイト       http://127.0.0.1:9102  （APIなし・フォームのみ）
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402


def serve(app_path: str, port: int) -> None:
    uvicorn.run(app_path, host="127.0.0.1", port=port, log_level="warning")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory-port", type=int, default=9101)
    ap.add_argument("--listing-port", type=int, default=9102)
    ap.add_argument("--mail-port", type=int, default=9103)
    ap.add_argument("--notify-port", type=int, default=9104)
    args = ap.parse_args()

    threading.Thread(
        target=serve, args=("mock.inventory_system:app", args.inventory_port), daemon=True
    ).start()
    threading.Thread(
        target=serve, args=("mock.mail_system:app", args.mail_port), daemon=True
    ).start()
    threading.Thread(
        target=serve, args=("mock.notification_sink:app", args.notify_port), daemon=True
    ).start()
    print(f"社内管理システム : http://127.0.0.1:{args.inventory_port}/cars")
    print(f"問い合わせ受信箱 : http://127.0.0.1:{args.mail_port}/inbox")
    print(f"　└ 下書き       : http://127.0.0.1:{args.mail_port}/drafts")
    print(f"通知先           : http://127.0.0.1:{args.notify_port}/notifications")
    print(f"掲載サイト       : http://127.0.0.1:{args.listing_port}/vehicles")
    print("Ctrl+C で停止します。")
    try:
        serve("mock.listing_site:app", args.listing_port)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
