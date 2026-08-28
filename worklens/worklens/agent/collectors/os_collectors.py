"""OS別のアクティブウィンドウ収集。

依存が入っていない環境では available() が False を返すだけで、
エージェント本体は落ちない（graceful degrade）。

必要な追加ライブラリ:
  Windows : pywin32
  macOS   : pyobjc-framework-Quartz
  Linux   : python-xlib（または xdotool コマンド）
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Iterable

from ...appcatalog import categorize_app
from .base import make_event


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class _WindowCollectorBase:
    name = "os-window"

    def available(self) -> bool:  # pragma: no cover - 環境依存
        return False

    def active_window(self) -> tuple[str | None, str | None]:  # pragma: no cover
        return None, None

    def poll(self) -> Iterable[dict[str, Any]]:
        app, title = self.active_window()
        if not app:
            return []
        ts = _now()
        events = [
            make_event(
                ts, "app_focus", "app_usage",
                app_name=app, app_category=categorize_app(app),
            )
        ]
        if title:
            events.append(
                make_event(
                    ts, "window_title", "window_title",
                    app_name=app, app_category=categorize_app(app), window_title=title,
                )
            )
        return events


class WindowsWindowCollector(_WindowCollectorBase):
    name = "windows-window"

    def available(self) -> bool:  # pragma: no cover - Windows のみ
        if platform.system() != "Windows":
            return False
        try:
            import win32gui  # noqa: F401
            import win32process  # noqa: F401
        except Exception:
            return False
        return True

    def active_window(self) -> tuple[str | None, str | None]:  # pragma: no cover
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None, None
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            app = psutil.Process(pid).name()
        except Exception:
            app = None
        return app, title


class MacWindowCollector(_WindowCollectorBase):
    name = "macos-window"

    def available(self) -> bool:  # pragma: no cover - macOS のみ
        if platform.system() != "Darwin":
            return False
        try:
            from AppKit import NSWorkspace  # noqa: F401
        except Exception:
            return False
        return True

    def active_window(self) -> tuple[str | None, str | None]:  # pragma: no cover
        from AppKit import NSWorkspace

        info = NSWorkspace.sharedWorkspace().activeApplication() or {}
        app = info.get("NSApplicationName")
        # ウィンドウタイトルは画面収録権限が必要。未許可なら None のまま返す。
        title = None
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )

            for w in CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            ):
                if w.get("kCGWindowOwnerName") == app and w.get("kCGWindowName"):
                    title = w["kCGWindowName"]
                    break
        except Exception:
            title = None
        return app, title


class LinuxWindowCollector(_WindowCollectorBase):
    name = "linux-window"

    def available(self) -> bool:
        return (
            platform.system() == "Linux"
            and bool(shutil.which("xdotool"))
            and bool(__import__("os").environ.get("DISPLAY"))
        )

    def active_window(self) -> tuple[str | None, str | None]:  # pragma: no cover - X11 必要
        try:
            title = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.strip()
            pid = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowpid"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.strip()
            app = None
            if pid.isdigit():
                with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
                    app = fh.read().strip()
            return app, title
        except Exception:
            return None, None


def detect_collector() -> _WindowCollectorBase | None:
    """この環境で使えるコレクタを返す。無ければ None。"""
    for cls in (WindowsWindowCollector, MacWindowCollector, LinuxWindowCollector):
        c = cls()
        if c.available():
            return c
    return None


def missing_dependency_hint() -> str:
    system = platform.system()
    return {
        "Windows": "pip install pywin32 psutil",
        "Darwin": "pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa",
        "Linux": "sudo apt install xdotool （X11 セッションが必要）",
    }.get(system, "この OS 向けのコレクタは未対応です")
