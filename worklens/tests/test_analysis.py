"""業務分析（セグメント化・繰り返し検出・業務単位化）の検証。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from worklens.analysis.pattern import Cluster, Occurrence, estimate_block_gap, mine, normalize_clusters
from worklens.analysis.sessionizer import Segment, Sessionizer, group_events_by_session
from worklens.analysis.task_inference import build_profiles, clean_subject, infer_tasks

BASE = datetime(2025, 5, 1, 9, 0, tzinfo=timezone.utc)


def _event(offset_sec, event_type, scope_key, **fields):
    ev = {
        "id": f"e{offset_sec}-{event_type}",
        "session_id": "S1",
        "ts": (BASE + timedelta(seconds=offset_sec)).isoformat(),
        "event_type": event_type,
        "scope_key": scope_key,
    }
    ev.update(fields)
    return ev


def _segment(start_sec, duration, context, action_marker, session="S1"):
    seg = Segment(
        session_id=session,
        started_at=BASE + timedelta(seconds=start_sec),
        ended_at=BASE + timedelta(seconds=start_sec + duration),
        app_name="app",
        app_category="browser",
        context=context,
    )
    if action_marker == "コピー":
        seg.copies = 1
    elif action_marker == "貼り付け":
        seg.pastes = 1
    elif action_marker == "入力":
        seg.keystrokes = duration * 2
    return seg


# ----------------------------------------------------------- セグメント化
def test_sessionizer_groups_events_into_context_segments():
    events = [
        _event(0, "app_focus", "app_usage", app_name="chrome.exe", app_category="browser",
               detail={"duration_sec": 40}),
        _event(0, "browser_navigate", "browser_usage", app_name="chrome.exe",
               app_category="browser", url_domain="kanri.example.co.jp",
               url_path_shape="/cars/:id", detail={"duration_sec": 40}),
        _event(35, "clipboard_op", "clipboard_meta", app_name="chrome.exe",
               detail={"op": "copy", "length_bucket": "100-999"}),
        _event(40, "app_focus", "app_usage", app_name="EXCEL.EXE", app_category="spreadsheet",
               detail={"duration_sec": 120}),
        _event(90, "input_burst", "input_activity", app_name="EXCEL.EXE",
               detail={"keystrokes": 300, "clicks": 10}),
    ]
    segments = Sessionizer().build(events)
    assert [s.context for s in segments] == ["社内管理システム", "Excel"]
    assert segments[0].action == "コピー"
    assert segments[1].action == "入力"
    assert segments[0].domain == "kanri.example.co.jp"


def test_idle_breaks_a_segment():
    events = [
        _event(0, "app_focus", "app_usage", app_name="EXCEL.EXE", app_category="spreadsheet",
               detail={"duration_sec": 30}),
        _event(30, "idle_start", "work_hours", detail={"expected_sec": 600}),
        _event(630, "idle_end", "work_hours"),
        _event(640, "app_focus", "app_usage", app_name="EXCEL.EXE", app_category="spreadsheet",
               detail={"duration_sec": 30}),
    ]
    segments = Sessionizer().build(events)
    assert len(segments) == 2
    assert segments[1].idle_before_sec == 600


# --------------------------------------------------------- 繰り返し検出
def test_block_gap_threshold_is_estimated_from_the_gap_distribution():
    gaps = [0] * 60 + [300] * 40          # 作業内は詰まっていて、作業間は空く
    threshold = estimate_block_gap(gaps)
    assert 10 <= threshold < 300


def test_repeated_two_step_routine_is_detected_as_one_cluster():
    segments = []
    t = 0
    for _ in range(6):
        segments.append(_segment(t, 40, "社内管理システム", "コピー"))
        segments.append(_segment(t + 40, 200, "掲載サイト", "貼り付け"))
        t += 400                            # 作業間に160秒のあき
    clusters, stats = mine({"S1": segments}, min_support=3)
    top = max(clusters, key=lambda c: c.count)
    assert top.signature == ("社内管理システム|コピー", "掲載サイト|貼り付け")
    assert top.count == 6
    assert top.is_repetitive
    assert stats["blocks"] == 6


def test_periodic_signature_is_folded_into_one_cycle():
    """A→B→A→B は「別の業務」ではなく「A→B を2回」として数える。"""
    doubled = Cluster(
        signature=("A", "B", "A", "B"),
        occurrences=[
            Occurrence([
                _segment(0, 10, "A", "確認"), _segment(10, 10, "B", "入力"),
                _segment(20, 10, "A", "確認"), _segment(30, 10, "B", "入力"),
            ])
        ],
    )
    single = Cluster(
        signature=("A", "B"),
        occurrences=[Occurrence([_segment(100, 10, "A", "確認"), _segment(110, 10, "B", "入力")])],
    )
    merged = normalize_clusters([doubled, single], min_support=3)
    assert len(merged) == 1
    assert merged[0].signature == ("A", "B")
    assert merged[0].count == 3


# --------------------------------------------------------- 業務単位化
def test_clean_subject_strips_noise():
    assert clean_subject("見積書テンプレート.xlsx - Excel") == "見積書"
    assert clean_subject("日報_20250501.docx - Word") == "日報"
    assert clean_subject("車両情報 新規登録 - 掲載管理コンソール") == "車両情報"
    assert clean_subject("受信トレイ - Outlook") is None
    assert clean_subject("<本文と判定したため非保存>") is None


def _cluster_from(steps, repeats=8):
    occurrences = []
    t = 0
    for _ in range(repeats):
        segs = []
        for context, marker, dur, extra in steps:
            seg = _segment(t, dur, context, marker)
            for key, value in (extra or {}).items():
                setattr(seg, key, value)
            segs.append(seg)
            t += dur
        occurrences.append(Occurrence(segs))
        t += 600
    return Cluster(signature=tuple(s.token for s in occurrences[0].segments),
                   occurrences=occurrences, is_repetitive=True)


def test_transfer_between_systems_is_named_as_transcription():
    cluster = _cluster_from([
        ("社内管理システム", "コピー", 40, {"app_category": "business_system",
                                           "domain": "kanri.example.co.jp",
                                           "titles": ["車両詳細 - 在庫管理システム"]}),
        ("掲載サイト", "貼り付け", 200, {"app_category": "listing_site",
                                        "domain": "keisai.example-portal.jp",
                                        "titles": ["車両情報 新規登録 - 掲載管理コンソール"]}),
    ])
    tasks, engine = infer_tasks([cluster], days_observed=5)
    assert engine == "rule-based"
    task = tasks[0]
    assert "転記" in task.name
    assert "社内管理システム" in task.name and "掲載サイト" in task.name
    assert task.category == "データ転記"


def test_mail_then_spreadsheet_is_named_as_entry_from_mail():
    cluster = _cluster_from([
        ("Outlook", "確認", 50, {"app_category": "mail", "titles": ["受信トレイ - Outlook"]}),
        ("Excel", "入力", 160, {"app_category": "spreadsheet",
                                "titles": ["顧客管理.xlsx - Excel"], "file_ops": ["save"]}),
    ])
    tasks, _ = infer_tasks([cluster], days_observed=5)
    assert tasks[0].category == "データ入力"
    assert "メール" in tasks[0].name and "Excel" in tasks[0].name


def test_document_then_mail_is_named_as_create_and_send():
    cluster = _cluster_from([
        ("Excel", "入力", 140, {"app_category": "spreadsheet", "titles": ["見積書.xlsx - Excel"],
                                "file_ops": ["create"], "file_exts": ["pdf"]}),
        ("エクスプローラー", "ファイル名変更", 25, {"app_category": "file_manager",
                                                   "file_ops": ["rename"]}),
        ("Outlook", "入力", 90, {"app_category": "mail", "titles": ["メッセージの作成"]}),
    ])
    tasks, _ = infer_tasks([cluster], days_observed=5)
    assert tasks[0].category == "書類作成・送付"
    assert "PDF" in tasks[0].name and "メール送付" in tasks[0].name


def test_summary_is_business_language_not_raw_log():
    cluster = _cluster_from([
        ("社内管理システム", "コピー", 40, {"domain": "kanri.example.co.jp"}),
        ("掲載サイト", "貼り付け", 200, {"domain": "keisai.example-portal.jp"}),
    ])
    profile = build_profiles([cluster], days_observed=5)[0]
    tasks, _ = infer_tasks([cluster], days_observed=5)
    assert "1日あたり" in tasks[0].summary
    assert profile["1日あたり回数"] > 0
    # 「Chromeを◯分使用」のようなアプリ名＋時間だけの表現になっていないこと
    assert "chrome" not in tasks[0].summary.lower()
