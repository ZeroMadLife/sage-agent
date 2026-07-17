"""Pydantic schemas for coding tools."""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ListFilesArgs(BaseModel):
    """Arguments for list_files."""

    path: str = "."


class ReadFileArgs(BaseModel):
    """Arguments for read_file."""

    path: str
    start: int = 1
    end: int = 200

    @field_validator("start")
    @classmethod
    def start_ge_one(cls, value: int) -> int:
        if value < 1:
            raise ValueError("start must be >= 1")
        return value

    @model_validator(mode="after")
    def end_ge_start(self) -> ReadFileArgs:
        if self.end < self.start:
            raise ValueError("invalid line range")
        return self


class SearchArgs(BaseModel):
    """Arguments for search."""

    pattern: str
    path: str = "."

    @field_validator("pattern")
    @classmethod
    def pattern_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pattern must not be empty")
        return value


class RunShellArgs(BaseModel):
    """Arguments for run_shell."""

    command: str
    timeout: int = 20

    @field_validator("command")
    @classmethod
    def command_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command must not be empty")
        return value

    @field_validator("timeout")
    @classmethod
    def timeout_in_range(cls, value: int) -> int:
        if value < 1 or value > 120:
            raise ValueError("timeout must be in [1, 120]")
        return value


class WriteFileArgs(BaseModel):
    """Arguments for write_file."""

    path: str
    content: str


class PatchFileArgs(BaseModel):
    """Arguments for patch_file."""

    path: str
    old_text: str
    new_text: str

    @field_validator("old_text")
    @classmethod
    def old_text_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("old_text must not be empty")
        return value


class TodoAddArgs(BaseModel):
    """Arguments for todo_add."""

    content: str
    status: str = "pending"
    priority: str = "normal"
    note: str = ""

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class TodoUpdateArgs(BaseModel):
    """Arguments for todo_update."""

    todo_id: str
    status: str | None = None
    content: str | None = None
    priority: str | None = None
    note: str | None = None

    @field_validator("todo_id")
    @classmethod
    def todo_id_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("todo_id must not be empty")
        return value


class TodoListArgs(BaseModel):
    """Arguments for todo_list."""


class EnterPlanModeArgs(BaseModel):
    """Arguments for enter_plan_mode."""

    topic: str
    path: str | None = None

    @field_validator("topic")
    @classmethod
    def topic_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("topic must not be empty")
        return value


class ExitPlanModeArgs(BaseModel):
    """Arguments for exit_plan_mode."""


class AgentArgs(BaseModel):
    """Arguments for agent."""

    description: str
    prompt: str
    subagent_type: str = "worker"
    write_scope: list[str] | str | None = None

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be empty")
        return value

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be empty")
        return value

    @field_validator("subagent_type")
    @classmethod
    def valid_subagent_type(cls, value: str) -> str:
        if value not in {"worker", "Explore"}:
            raise ValueError("subagent_type must be worker or Explore")
        return value


class SendMessageArgs(BaseModel):
    """Arguments for send_message."""

    to: str
    message: str

    @field_validator("to")
    @classmethod
    def to_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("to must not be empty")
        return value

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty")
        return value


class TaskStopArgs(BaseModel):
    """Arguments for task_stop."""

    task_id: str

    @field_validator("task_id")
    @classmethod
    def task_id_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_id must not be empty")
        return value


class ToolSearchArgs(BaseModel):
    """Arguments for tool_search."""

    query: str

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value


class KnowledgeSearchArgs(BaseModel):
    """Arguments for evidence-only knowledge retrieval."""

    query: str
    top_k: int = Field(default=8, ge=1, le=20)
    token_budget: int = Field(default=3000, ge=256, le=20000)

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        if len(value) > 2_000:
            raise ValueError("query must not exceed 2000 characters")
        return value


class KnowledgeLearnArgs(BaseModel):
    """Arguments for an extractive, citation-gated learning deposit."""

    topic: str
    citation_ids: list[str] = Field(min_length=1, max_length=8)

    @field_validator("topic")
    @classmethod
    def topic_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("topic must not be empty")
        if len(value) > 160:
            raise ValueError("topic must not exceed 160 characters")
        return value


class RememberArgs(BaseModel):
    """Arguments for remember."""

    fact: str
    topic: str = "project-conventions"

    @field_validator("fact")
    @classmethod
    def fact_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fact must not be empty")
        return value

    @field_validator("topic")
    @classmethod
    def topic_valid(cls, value: str) -> str:
        if value not in {"project-conventions", "decisions"}:
            raise ValueError("topic must be project-conventions or decisions")
        return value


class DreamArgs(BaseModel):
    """Arguments for dream."""

    topic: str | None = None

    @field_validator("topic")
    @classmethod
    def topic_valid(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in {"project-conventions", "decisions"}:
            raise ValueError("topic must be project-conventions or decisions")
        return value


def first_error_message(exc: ValidationError) -> str:
    """Extract a compact validation message."""
    errors = exc.errors(include_url=False)
    if not errors:
        return str(exc)
    error = errors[0]
    if error.get("type") == "missing":
        location = error.get("loc", ())
        field = location[-1] if location else ""
        if field:
            return f"'{field}'"
    return str(error.get("msg", "")).removeprefix("Value error, ")
