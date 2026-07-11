import json
from pathlib import Path


CONTRACTS_PATH = Path(__file__).with_name("coding_v6_contracts.json")


def load_contracts() -> dict[str, dict[str, list[str]]]:
    return json.loads(CONTRACTS_PATH.read_text(encoding="utf-8"))


def test_context_usage_updated_requires_session_id() -> None:
    contracts = load_contracts()

    assert "session_id" in contracts["context_usage_updated"]["required"]


def test_memory_proposal_ready_requires_proposal_revision_identity() -> None:
    contracts = load_contracts()

    required = contracts["memory_proposal_ready"]["required"]
    assert "proposal_id" in required
    assert "base_revision" in required
