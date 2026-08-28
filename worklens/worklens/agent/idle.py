"""離席（アイドル）の検出。

実PCで収集するとき、これが無いと昼休みや会議で離席した時間まで
「作業していた」ことになり、作業時間と削減見込みが水増しされる。

OS の「最後に入力があってからの経過秒数」だけを見る。
**押されたキーの種類は取得しない**（input_activity スコープの約束どおり）。
追加ライブラリは使わず、標準の ctypes / 既存の依存だけで済ませる。
取得できない環境では is_supported() が False を返し、収集は止まらない。
"""
from __future__ import annotations

import platform
import subprocess


class IdleDetector:
    """最後の入力からの経過秒数を返す。"""

    def __init__(self) -> None:
        self.system = platform.system()
        self._impl = self._pick()

    # ------------------------------------------------------------ 判定
    def is_supported(self) -> bool:
        return self._impl is not None

    def seconds_since_input(self) -> float | None:
        if self._impl is None:
            return None
        try:
            return self._impl()
        except Exception:
            return None

    def unsupported_reason(self) -> str:
        return {
            "Windows": "",
            "Darwin": "Quartz（pyobjc）が必要です",
            "Linux": "libXss（X11）が必要です",
        }.get(self.system, f"{self.system} は離席検出に未対応です")

    # ---------------------------------------------------------- 実装選択
    def _pick(self):
        """この OS で使える実装を選ぶ。無ければ None（収集は続行する）。"""
        for candidate in (self._windows, self._macos, self._linux):
            impl = candidate()
            if impl is not None:
                return impl
        return None

    # --- Windows: GetLastInputInfo（標準DLL・追加依存なし） -------------
    def _windows(self):
        if self.system != "Windows":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            def seconds() -> float:
                info = LASTINPUTINFO()
                info.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if not user32.GetLastInputInfo(ctypes.byref(info)):
                    raise OSError("GetLastInputInfo に失敗しました")
                # GetTickCount は約49日でラップするため差分を32bitで扱う
                elapsed_ms = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
                return elapsed_ms / 1000.0

            seconds()          # 一度呼んで使えることを確かめる
            return seconds
        except Exception:
            return None

    # --- macOS: Quartz のイベント経過秒 -------------------------------
    def _macos(self):
        if self.system != "Darwin":
            return None
        try:
            from Quartz import (
                CGEventSourceSecondsSinceLastEventType,
                kCGAnyInputEventType,
                kCGEventSourceStateCombinedSessionState,
            )

            def seconds() -> float:
                return float(
                    CGEventSourceSecondsSinceLastEventType(
                        kCGEventSourceStateCombinedSessionState, kCGAnyInputEventType
                    )
                )

            seconds()
            return seconds
        except Exception:
            return self._macos_ioreg()

    def _macos_ioreg(self):
        """Quartz が無い場合のフォールバック（標準コマンド）。"""
        if self.system != "Darwin":
            return None

        def seconds() -> float:
            out = subprocess.run(
                ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True,
                timeout=5, check=True,
            ).stdout
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    return int(line.split("=")[-1].strip()) / 1_000_000_000.0
            raise RuntimeError("HIDIdleTime が取得できませんでした")

        try:
            seconds()
            return seconds
        except Exception:
            return None

    # --- Linux: XScreenSaver -----------------------------------------
    def _linux(self):
        if self.system != "Linux":
            return None
        try:
            import ctypes
            import os

            if not os.environ.get("DISPLAY"):
                return None

            class XScreenSaverInfo(ctypes.Structure):
                _fields_ = [
                    ("window", ctypes.c_ulong), ("state", ctypes.c_int),
                    ("kind", ctypes.c_int), ("since", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong),
                ]

            xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
            xss = ctypes.cdll.LoadLibrary("libXss.so.1")
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XScreenSaverInfo)
            display = xlib.XOpenDisplay(None)
            if not display:
                return None
            root = xlib.XDefaultRootWindow(display)
            info = xss.XScreenSaverAllocInfo()

            def seconds() -> float:
                xss.XScreenSaverQueryInfo(display, root, info)
                return info.contents.idle / 1000.0

            seconds()
            return seconds
        except Exception:
            return None
