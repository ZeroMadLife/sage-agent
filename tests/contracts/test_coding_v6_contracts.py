import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from core.coding.engine.events import (
    ContextUsageUpdatedEvent,
    MemoryProposalReadyEvent,
)

CONTRACTS_PATH = Path(__file__).with_name("coding_v6_contracts.json")


def load_contracts() -> dict[str, dict[str, list[str]]]:
    return cast(
        dict[str, dict[str, list[str]]],
        json.loads(CONTRACTS_PATH.read_text(encoding="utf-8")),
    )


def test_context_usage_updated_required_fields_are_frozen() -> None:
    contracts = load_contracts()

    assert contracts["context_usage_updated"]["required"] == [
        "session_id",
        "run_id",
        "used_tokens",
        "model_limit_tokens",
        "output_reserve_tokens",
        "effective_limit_tokens",
        "usage_ratio",
        "level",
        "estimated",
        "compactable",
    ]


def test_memory_proposal_ready_required_fields_are_frozen() -> None:
    contracts = load_contracts()

    assert contracts["memory_proposal_ready"]["required"] == [
        "session_id",
        "run_id",
        "reflection_id",
        "proposal_id",
        "candidate_count",
        "base_revision",
    ]


def test_runtime_event_models_require_the_frozen_contract_fields() -> None:
    contracts = load_contracts()
    models: dict[str, type[BaseModel]] = {
        "context_usage_updated": ContextUsageUpdatedEvent,
        "memory_proposal_ready": MemoryProposalReadyEvent,
    }

    for event_type, model in models.items():
        schema_required = set(model.model_json_schema().get("required", []))
        assert schema_required == set(contracts[event_type]["required"])
