"""Analyzer protocol: derived records computed from an interpretation, never from raw
payloads directly, and always labelled with analyzer version and maturity."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from panoptis.analysis.maturity import Maturity
from panoptis.events.model import ComputationalEvent
from panoptis.events.normalize import ArtifactContent


@dataclass
class AnalysisInput:
    tenant_id: str
    interp_id: str
    events: Sequence[ComputationalEvent]
    run_of: dict[str, str | None]
    runs: dict[str, dict[str, Any]]
    relations: Sequence[dict[str, Any]]
    artifacts: dict[str, ArtifactContent] = field(default_factory=dict)

    def run_events(self, run_id: str) -> list[ComputationalEvent]:
        return [e for e in self.events if self.run_of.get(e.event_id) == run_id]

    def run_relations(self, run_id: str) -> list[dict[str, Any]]:
        return [r for r in self.relations if r.get("run_id") == run_id]


class Analyzer:
    name: ClassVar[str] = "analyzer"
    version: ClassVar[str] = "1"
    maturity: ClassVar[Maturity] = Maturity.EXPERIMENTAL
    record_type: ClassVar[str] = "record"

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def analyze(self, data: AnalysisInput) -> list[dict[str, Any]]:
        raise NotImplementedError  # abstract

    def record(self, record_id: str, scope: str, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            **doc,
            "record_id": record_id,
            "record_type": self.record_type,
            "scope": scope,
            "analyzer": self.ref,
            "maturity": self.maturity.value,
        }
