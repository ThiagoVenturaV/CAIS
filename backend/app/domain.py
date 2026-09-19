from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


class Role(StrEnum):
    MANAGER = "manager"
    GUARD = "guard"


class OperationalStatus(StrEnum):
    PROCESSING = "processing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISTRIBUTED = "distributed"
    UNASSIGNED = "unassigned"
    COMPLETED = "completed"


class DispatchStatus(StrEnum):
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"


class Criticality(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(slots=True)
class DomainEvent:
    event_type: str
    aggregate_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor_id: str | None = None
    event_id: str = field(default_factory=lambda: make_id("evt"))
    occurred_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
