from dataclasses import FrozenInstanceError

import pytest

from core.coding.context.budget import (
    ContextPolicy,
    ContextUsage,
    TokenCount,
    TokenCounter,
)


def test_effective_limit_subtracts_output_reserve():
    policy = ContextPolicy(
        context_window_tokens=200_000,
        output_reserve_tokens=20_000,
    )

    assert policy.effective_limit_tokens == 180_000


@pytest.mark.parametrize(
    ("tokens", "level"),
    [
        (89_999, "normal"),
        (90_000, "budget"),
        (108_000, "snip"),
        (117_000, "compact"),
        (126_000, "high"),
        (153_000, "emergency"),
    ],
)
def test_pressure_levels_use_effective_limit(tokens, level):
    policy = ContextPolicy(
        context_window_tokens=200_000,
        output_reserve_tokens=20_000,
    )

    usage = policy.usage(tokens, estimated=True)

    assert usage.used_tokens == tokens
    assert usage.effective_limit_tokens == 180_000
    assert usage.usage_ratio == pytest.approx(tokens / 180_000)
    assert usage.level == level
    assert usage.estimated is True


def test_invalid_threshold_order_is_rejected():
    with pytest.raises(ValueError, match="thresholds"):
        ContextPolicy(
            context_window_tokens=200_000,
            output_reserve_tokens=20_000,
            compact_ratio=0.40,
        )


def test_equal_thresholds_are_rejected():
    with pytest.raises(ValueError, match="thresholds"):
        ContextPolicy(
            context_window_tokens=200_000,
            output_reserve_tokens=20_000,
            compact_ratio=0.60,
        )


@pytest.mark.parametrize("ratio", [-0.01, 1.01])
def test_thresholds_outside_unit_interval_are_rejected(ratio):
    with pytest.raises(ValueError, match="thresholds"):
        ContextPolicy(
            context_window_tokens=200_000,
            output_reserve_tokens=20_000,
            budget_ratio=ratio,
        )


def test_window_must_exceed_output_reserve():
    with pytest.raises(ValueError, match="context window"):
        ContextPolicy(
            context_window_tokens=20_000,
            output_reserve_tokens=20_000,
        )


def test_negative_usage_is_rejected():
    policy = ContextPolicy(context_window_tokens=200_000)

    with pytest.raises(ValueError, match="used_tokens"):
        policy.usage(-1)


def test_cache_override_does_not_create_a_pressure_level():
    policy = ContextPolicy(
        context_window_tokens=200_000,
        output_reserve_tokens=20_000,
    )

    assert policy.usage(135_000).level == "high"


def test_counter_uses_model_counter_when_available():
    class Model:
        def get_num_tokens(self, text):
            assert text == "context"
            return 7

    count = TokenCounter(Model()).count("context")

    assert count.tokens == 7
    assert count.estimated is False


def test_counter_clamps_model_count_to_one():
    class Model:
        def get_num_tokens(self, text):
            return 0

    assert TokenCounter(Model()).count("").tokens == 1


def test_counter_fallback_is_conservative_and_labeled():
    count = TokenCounter().count("中文 context")

    assert count.tokens == 4
    assert count.estimated is True


def test_counter_falls_back_when_model_counter_raises():
    class Model:
        def get_num_tokens(self, text):
            raise RuntimeError("counter unavailable")

    count = TokenCounter(Model()).count("12345")

    assert count.tokens == 2
    assert count.estimated is True


@pytest.mark.parametrize(
    ("instance", "field", "value"),
    [
        (TokenCount(tokens=1, estimated=True), "tokens", 2),
        (
            ContextUsage(
                used_tokens=1,
                effective_limit_tokens=2,
                usage_ratio=0.5,
                level="budget",
                estimated=False,
            ),
            "level",
            "normal",
        ),
        (ContextPolicy(context_window_tokens=200_000), "budget_ratio", 0.51),
    ],
)
def test_context_contract_dataclasses_are_frozen(instance, field, value):
    with pytest.raises(FrozenInstanceError):
        setattr(instance, field, value)
