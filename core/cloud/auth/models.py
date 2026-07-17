"""Public value objects for the V7 cloud control plane."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CloudUser:
    """Authenticated user identity safe to return beyond the repository."""

    user_id: str
    email: str
    display_name: str


@dataclass(frozen=True, slots=True)
class CloudLoginSession:
    """Persisted session metadata without the browser token."""

    session_id: str
    user_id: str
    token_hash: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CloudProject:
    """A user-owned cloud project."""

    project_id: str
    owner_user_id: str
    name: str


@dataclass(frozen=True, slots=True)
class CloudWorkspace:
    """Control-plane metadata for a future isolated workspace."""

    workspace_id: str
    project_id: str
    provider: str
    lifecycle_state: str
