import datetime
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheEntry:
    query: str
    response: str
    embedding: list[float]
    metadata: dict[str, Any] | None = None
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
