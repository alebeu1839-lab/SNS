"""実PC収集の検出部（離席・入力・コピー）の検証。

内容を取らない約束が守られているかを主に見る。
"""
from __future__ import annotations

from worklens.agent.activity import ActivityProbe, ClipboardChangeWatcher
from worklens.agent.idle import IdleDetector


class StubIdle:
    def __init__(self, supported=True, seconds=1.0):
        self._supported = supported
        self._seconds = seconds

    def is_supported(self):
        return self._supported

    def unsupported_reason(self):
        return "" if self._supported else "この環境では取得できません"

    def seconds_since_input(self):
        return self._seconds if self._supported else None


def test_idle_detector_degrades_without_crashing():
    """取得できない環境でも例外を投げず、収集を止めない。"""
    detector = IdleDetector()
    assert isinstance(detector.is_supported(), bool)
    value = detector.seconds_since_input()
    assert value is None or value >= 0


def test_input_is_judged_by_recency_not_by_key_content():
    probe = ActivityProbe(StubIdle(), interval_sec=5.0)
    assert probe.input_happened(0.5) is True      # 直前に操作があった
    assert probe.input_happened(4.9) is True
    assert probe.input_happened(60.0) is False    # しばらく触っていない
    assert probe.input_happened(None) is False    # 取得できない環境


def test_input_detection_uses_at_least_one_second_window():
    """ポーリング間隔が極端に短くても判定が壊れないこと。"""
    probe = ActivityProbe(StubIdle(), interval_sec=0.1)
    assert probe.input_happened(0.9) is True


def test_clipboard_watcher_only_counts_changes():
    """クリップボードの中身は一切読まないこと。"""
    watcher = ClipboardChangeWatcher()
    watcher._read = lambda: watcher._fake            # type: ignore[attr-defined]
    watcher._fake = 10                               # type: ignore[attr-defined]
    watcher._last = None

    assert watcher.changed() is False                # 初回は基準値を取るだけ
    assert watcher.changed() is False                # 変化なし
    watcher._fake = 11                               # type: ignore[attr-defined]
    assert watcher.changed() is True                 # コピーが起きた
    assert watcher.changed() is False

    # 公開APIに内容を返すものが無いこと
    assert not any(
        name for name in dir(watcher)
        if not name.startswith("_") and "content" in name.lower()
    )


def test_capability_notes_warn_when_transfer_detection_would_break():
    probe = ActivityProbe(StubIdle(supported=False), interval_sec=5.0)
    probe.clipboard._read = None
    notes = " ".join(probe.capability_notes())
    assert "離席" in notes
    assert "転記業務が検出されにくくなります" in notes


def test_capability_notes_are_empty_when_everything_works():
    probe = ActivityProbe(StubIdle(supported=True), interval_sec=5.0)
    probe.clipboard._read = lambda: 1
    assert probe.capability_notes() == []
