"""API 用量记账测试。"""

import json
from types import SimpleNamespace

from numerology import api_usage


def test_estimate_cost_uses_pricing_table():
    cost = api_usage.estimate_cost("gpt-5-mini", 1_000_000, 1_000_000)
    assert cost == 0.25 + 2.00
    assert api_usage.estimate_cost("unknown-model", 1_000_000, 0) == 0.0


def test_record_and_summarize(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(api_usage, "USAGE_PATH", path)
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500)
    api_usage.record("gpt-5-mini", usage, task="extract")
    api_usage.record("gpt-5-mini", usage, task="extract")
    api_usage.record("gpt-4o-mini", usage, task="translate")

    report = api_usage.summarize(path)
    assert report["calls"] == 3
    assert report["by_task"]["extract"]["calls"] == 2
    assert report["by_task"]["extract"]["prompt_tokens"] == 2000
    assert report["by_model"]["gpt-4o-mini"]["calls"] == 1
    assert report["total_cost"] > 0


def test_record_tolerates_missing_usage(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(api_usage, "USAGE_PATH", path)
    api_usage.record("gpt-5-mini", None, task="x")     # 不该抛异常
    assert not path.exists()


def test_summarize_empty_file(tmp_path):
    assert api_usage.summarize(tmp_path / "none.jsonl")["calls"] == 0
