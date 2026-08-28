"""入力量とコピー＆ペーストの発生検出（内容は取らない）。

OSのアクティブウィンドウだけを見ていると、動作がすべて「確認」になり、
転記や入力といった業務を見つけられない。かといってキーロガーは論外。

そこで内容に触れずに「起きた事実」だけを取る:
  - 入力量  : 最後の入力からの経過秒がポーリング間隔より短ければ
              「この区間に入力があった」と数える。押されたキーは取得しない。
  - コピー  : OSが持つクリップボードの**変更カウンタ**だけを見る。
              クリップボードの中身は一度も読まない。

いずれも scopes.py の約束（input_activity / clipboard_meta）の範囲に収まる。
取得できない環境では available() が False を返し、収集は止まらない。
"""
from __future__ import annotations

import platform


class ClipboardChangeWatcher:
    """クリップボードが変更された回数だけを数える。中身は読まない。"""

    def __init__(self) -> None:
        self.system = platform.system()
        self._read = self._pick()
        self._last: int | None = None

    def available(self) -> bool:
        return self._read is not None

    def unsupported_reason(self) -> str:
        return {
            "Windows": "",
            "Darwin": "pyobjc（AppKit）が必要です",
            "Linux": "Linux ではコピー検出に未対応です",
        }.get(self.system, f"{self.system} は未対応です")

    def changed(self) -> bool:
        """前回の確認からクリップボードが更新されたか。"""
        if self._read is None:
            return False
        try:
            current = self._read()
        except Exception:
            return False
        if current is None:
            return False
        if self._last is None:
            self._last = current
            return False
        changed = current != self._last
        self._last = current
        return changed

    def _pick(self):
        for candidate in (self._windows, self._macos):
            impl = candidate()
            if impl is not None:
                return impl
        return None

    def _windows(self):
        """GetClipboardSequenceNumber。中身を開かずに変更回数だけ取れる。"""
        if self.system != "Windows":
            return None
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.GetClipboardSequenceNumber.restype = ctypes.c_uint

            def read() -> int:
                return int(user32.GetClipboardSequenceNumber())

            read()
            return read
        except Exception:
            return None

    def _macos(self):
        """NSPasteboard の changeCount。こちらも中身は読まない。"""
        if self.system != "Darwin":
            return None
        try:
            from AppKit import NSPasteboard

            board = NSPasteboard.generalPasteboard()

            def read() -> int:
                return int(board.changeCount())

            read()
            return read
        except Exception:
            return None


class ActivityProbe:
    """1回のポーリングぶんの「入力があったか」「コピーがあったか」を返す。"""

    def __init__(self, idle_detector, interval_sec: float) -> None:
        self.idle = idle_detector
        self.interval_sec = interval_sec
        self.clipboard = ClipboardChangeWatcher()

    def input_happened(self, seconds_since_input: float | None) -> bool:
        if seconds_since_input is None:
            return False
        # 最後の入力がポーリング間隔以内なら、この区間に入力があった
        return seconds_since_input <= max(self.interval_sec, 1.0)

    def clipboard_changed(self) -> bool:
        return self.clipboard.changed()

    def capability_notes(self) -> list[str]:
        notes: list[str] = []
        if not self.idle.is_supported():
            notes.append(f"離席の検出: 使用不可（{self.idle.unsupported_reason()}）")
        if not self.clipboard.available():
            notes.append(
                f"コピー＆貼り付けの検出: 使用不可（{self.clipboard.unsupported_reason()}）"
                " → 転記業務が検出されにくくなります"
            )
        return notes
