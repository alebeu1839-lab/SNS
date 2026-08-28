"""STEP2 実行基盤（ガードレール）の検証。"""
from __future__ import annotations

import json

from worklens.step2.runner import AutomationRunner


class FakeRecipe:
    """業務を持たない試験用レシピ。基盤の振る舞いだけを見る。"""

    name = "fake"

    def __init__(self, sources, invalid=(), explode=()):
        self.sources = sources
        self.invalid = set(invalid)
        self.explode = set(explode)
        self.applied: list[str] = []
        self.finalized: list[str] = []

    def fetch(self):
        return self.sources

    def key_of(self, source):
        return str(source["id"])

    def validate(self, source):
        if source["id"] in self.invalid:
            return None, "人の判断が必要です"
        return {"id": source["id"]}, "OK"

    def apply(self, payload):
        if payload["id"] in self.explode:
            raise RuntimeError("転記先が受け付けませんでした")
        self.applied.append(payload["id"])

    def finalize(self, source, payload):
        self.finalized.append(source["id"])


SOURCES = [{"id": f"c{i}"} for i in range(1, 6)]


def test_dry_run_never_writes(tmp_path):
    """ガードレール2: ドライランでは転記先へ書き込まない。"""
    recipe = FakeRecipe(SOURCES)
    result = AutomationRunner(recipe, tmp_path, mode="dry-run").run()
    assert result.succeeded == 5
    assert recipe.applied == []          # 一度も書いていない
    assert recipe.finalized == []
    assert all("ドライラン" in i.reason for i in result.items)


def test_live_run_writes_and_finalizes(tmp_path):
    recipe = FakeRecipe(SOURCES)
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.succeeded == 5
    assert recipe.applied == [s["id"] for s in SOURCES]
    assert recipe.finalized == [s["id"] for s in SOURCES]


def test_unexpected_items_are_handed_to_a_human_not_forced(tmp_path):
    """ガードレール3: 想定外は自動処理せず人へ引き継ぐ。"""
    recipe = FakeRecipe(SOURCES, invalid={"c2", "c4"})
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    assert result.succeeded == 3
    assert result.handoff == 2
    assert "c2" not in recipe.applied and "c4" not in recipe.applied
    handed = [i for i in result.items if i.status == "handoff"]
    assert all(i.reason for i in handed), "引き継ぎには必ず理由が付くこと"


def test_run_stops_on_write_failure_instead_of_continuing(tmp_path):
    """失敗したまま走り続けて壊すより、止めて人へ返す。"""
    recipe = FakeRecipe(SOURCES, explode={"c3"})
    result = AutomationRunner(recipe, tmp_path, mode="live", stop_on_error=True).run()
    assert result.failed == 1
    assert recipe.applied == ["c1", "c2"]      # c4, c5 には手を付けていない
    assert result.processed == 3


def test_run_can_continue_past_failures_when_asked(tmp_path):
    recipe = FakeRecipe(SOURCES, explode={"c3"})
    result = AutomationRunner(recipe, tmp_path, mode="live", stop_on_error=False).run()
    assert result.failed == 1
    assert result.succeeded == 4


def test_limit_caps_the_number_processed(tmp_path):
    recipe = FakeRecipe(SOURCES)
    result = AutomationRunner(recipe, tmp_path, mode="live", limit=2).run()
    assert result.processed == 2


def test_every_item_is_written_to_the_execution_log(tmp_path):
    """ガードレール1: 経緯を人が追えること。"""
    recipe = FakeRecipe(SOURCES, invalid={"c2"}, explode={"c5"})
    result = AutomationRunner(recipe, tmp_path, mode="live", stop_on_error=False).run()

    records = [
        json.loads(line)
        for line in open(result.log_path, encoding="utf-8")
        if line.strip()
    ]
    events = [r["event"] for r in records]
    assert events[0] == "run.started" and events[-1] == "run.finished"
    assert "fetch.completed" in events
    logged_keys = {r["key"] for r in records if "key" in r}
    assert logged_keys == {"c1", "c2", "c3", "c4", "c5"}
    handoff = next(r for r in records if r["event"] == "item.handoff")
    assert handoff["key"] == "c2" and handoff["reason"]
    assert any(r["event"] == "item.failed" for r in records)


def test_summary_estimates_saved_time_from_successes_only(tmp_path):
    recipe = FakeRecipe(SOURCES, invalid={"c1", "c2"})
    result = AutomationRunner(recipe, tmp_path, mode="live").run()
    summary = result.summary(minutes_per_run=5.4)
    assert summary["succeeded"] == 3
    assert summary["saved_minutes"] == round(3 * 5.4, 1)
