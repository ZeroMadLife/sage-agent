"""CLI demo script tests."""

from scripts.demo_chat import parse_input


def test_parse_input_extracts_compact_chinese_request() -> None:
    """parse_input handles a compact Chinese travel request."""
    parsed = parse_input("周末去杭州2日游预算500元喜欢美食")

    assert parsed["destination"] == "杭州"
    assert parsed["budget_total"] == 500
    assert "美食" in parsed["preferences"]


def test_parse_input_detects_multiple_preferences() -> None:
    """parse_input maps user keywords into preference labels."""
    parsed = parse_input("想去北京，看历史古迹和博物馆，也想吃小吃，预算800元")

    assert parsed["destination"] == "北京"
    assert parsed["budget_total"] == 800
    assert "人文古迹" in parsed["preferences"]
    assert "美食" in parsed["preferences"]


def test_parse_input_defaults_when_missing_details() -> None:
    """parse_input supplies safe MVP defaults when details are absent."""
    parsed = parse_input("随便安排一个周末")

    assert parsed["destination"] == "杭州"
    assert parsed["budget_total"] == 500
    assert parsed["preferences"] == []
    assert parsed["dates"] == {"start": "2026-07-05", "end": "2026-07-06"}
