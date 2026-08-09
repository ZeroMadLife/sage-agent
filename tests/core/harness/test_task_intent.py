"""TaskIntentEnvelope 的确定性分类和权限边界测试。"""

import pytest

from core.harness.task_intent import TaskIntentAnalyzer, TaskIntentEnvelope


def test_direct_answer_uses_minimal_read_only_shape() -> None:
    envelope = TaskIntentAnalyzer().analyze("1 + 1 等于多少？")

    assert envelope.intent_kind == "answer"
    assert envelope.task_shape == "direct"
    assert envelope.requested_effects == ("read",)
    assert envelope.capability_hints == ()
    assert envelope.risk_hints == ()


def test_code_change_and_dangerous_effects_are_structured_without_prompt() -> None:
    envelope = TaskIntentAnalyzer().analyze("修复这个 bug，运行测试，并执行 rm -rf build。")

    assert envelope.intent_kind == "code_change"
    assert envelope.requested_effects == ("execute", "read", "write")
    assert envelope.capability_hints == ("files", "shell")
    assert envelope.risk_hints == ("destructive",)
    assert "rm -rf build" not in repr(envelope.as_dict())


def test_research_review_and_skill_commands_have_bounded_capabilities() -> None:
    research = TaskIntentAnalyzer().analyze("调研官网最新的 LangGraph checkpoint 资料")
    review = TaskIntentAnalyzer().analyze("审查这个仓库的 diff，不要修改文件")
    skill = TaskIntentAnalyzer().analyze("/review inspect core/harness")

    assert research.intent_kind == "research"
    assert research.capability_hints == ("knowledge", "web")
    assert "no_external" not in research.explicit_constraints
    assert review.intent_kind == "review"
    assert review.requested_effects == ("read",)
    assert review.explicit_constraints == ("read_only",)
    assert skill.intent_kind == "skill"
    assert skill.capability_hints == ("files", "skill")


def test_parallel_candidate_and_no_tools_are_not_execution_authority() -> None:
    envelope = TaskIntentAnalyzer().analyze("同时检查两个目录，不要调用任何工具")

    assert envelope.task_shape == "parallel_candidate"
    assert envelope.explicit_constraints == ("no_tools",)
    assert envelope.requested_effects == ("read",)
    assert envelope.can_only_narrow_capabilities is True


def test_envelope_rejects_free_text_and_unknown_schema_values() -> None:
    with pytest.raises(ValueError, match="unsupported intent kind"):
        TaskIntentEnvelope(intent_kind="private_cot")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="must be bounded"):
        TaskIntentEnvelope(classifier_version="free-text-reasoning-" * 10)
