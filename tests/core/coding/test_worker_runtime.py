"""Coding worker runtime tests."""

from pathlib import Path

from core.coding.context import WorkspaceContext
from core.coding.multiagent import WorkerTask, clean_scope, clean_type, run_worker_task


class FakeModel:
    """Deterministic async model for worker runtime tests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses

    async def complete(self, prompt: str) -> str:
        _ = prompt
        return self.responses.pop(0)


def test_worker_execution_cleans_type_and_scope() -> None:
    """Worker execution helpers normalize public worker tool arguments."""
    assert clean_type("Explore") == "Explore"
    assert clean_scope([" docs ", "", "tests"]) == ["docs", "tests"]


async def test_worker_runtime_returns_final_answer(tmp_path: Path) -> None:
    """Worker runtime runs an isolated engine and returns its final content."""
    (tmp_path / "README.md").write_text("TourSwarm worker runtime\n", encoding="utf-8")
    task = WorkerTask(
        id="agent_1",
        description="read README",
        subagent_type="Explore",
        write_scope=(),
        prompt="读 README",
    )

    result = await run_worker_task(
        task=task,
        workspace=WorkspaceContext(root=tmp_path),
        model_factory=lambda: FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>Read README.</final>",
            ]
        ),
    )

    assert result == "Read README."
