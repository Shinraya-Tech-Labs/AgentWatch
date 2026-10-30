"""Declared entity aliases: the only way two keys become one entity (no fuzzy merging)."""

from __future__ import annotations

import json

import pytest

from panoptis import instrument as aw
from panoptis.entities.resolve import load_aliases
from panoptis.runtime.engine import Engine
from panoptis.storage.store import Store


def _record(sink) -> None:
    @aw.model("gpt-4o")
    def a(p: str) -> str:
        return "one"

    @aw.model("openai/gpt-4o")
    def b(p: str) -> str:
        return "two"

    with aw.run("two-sensors"):
        a("x")
        b("y")


def _entities(engine: Engine) -> dict[str, dict]:
    return {e["canonical_key"]: e for e in engine.store.entities(engine.interp_id_for("default"))}


def test_without_aliases_keys_stay_separate(engine, sink):
    _record(sink)
    engine.ingest(sink.drafts)
    engine.process()
    ents = _entities(engine)
    assert {"model:gpt-4o", "model:openai/gpt-4o"} <= set(ents)
    assert all(e["resolution"]["basis"] == "EXACT_KEY" for e in ents.values())
    assert all("aliases" not in e for e in ents.values())


def test_declared_alias_merges_and_says_so(store: Store, sink, tmp_path):
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({"model:gpt-4o": "model:openai/gpt-4o"}), encoding="utf-8")
    engine = Engine(store, entity_aliases=str(path))
    _record(sink)
    engine.ingest(sink.drafts)
    engine.process()
    ents = _entities(engine)
    assert "model:gpt-4o" not in ents
    merged = ents["model:openai/gpt-4o"]
    assert merged["resolution"]["basis"] == "DECLARED_ALIAS"
    assert merged["aliases"] == ["model:gpt-4o"]
    assert merged["event_count"] == 2
    # events keep the names as observed
    objects = {e["object"] for e in store.events(engine.interp_id_for("default"))}
    assert {"model:gpt-4o", "model:openai/gpt-4o"} <= objects


def test_aliases_are_part_of_the_interpretation_identity(store: Store):
    plain = Engine(store)
    aliased = Engine(store, entity_aliases={"model:a": "model:b"})
    assert plain.interp_id_for("default") != aliased.interp_id_for("default")
    assert Engine(store, entity_aliases={}).interp_id_for("default") == plain.interp_id_for(
        "default"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"model:a": "model:a"},  # to itself
        {"model:a": "model:b", "model:b": "model:c"},  # a chain
        {"gpt-4o": "model:gpt-4o"},  # not kind:name
    ],
)
def test_invalid_alias_declarations_are_rejected(bad):
    with pytest.raises(ValueError):
        load_aliases(bad)


def test_incremental_equals_full_with_aliases(store: Store, sink):
    engine = Engine(store, entity_aliases={"model:gpt-4o": "model:openai/gpt-4o"})
    for _ in range(3):
        _record(sink)
        engine.ingest(sink.drafts)
        sink.drafts.clear()
        engine.process()
    incremental = _entities(engine)
    engine.process(force=True)
    assert _entities(engine) == incremental
    assert incremental["model:openai/gpt-4o"]["event_count"] == 6


def test_read_model_describes_aliases_on_both_names(store: Store, sink):
    from panoptis.query.workspace import Workspace

    engine = Engine(store, entity_aliases={"model:gpt-4o": "model:openai/gpt-4o"})
    _record(sink)
    engine.ingest(sink.drafts)
    ws = Workspace(engine)
    canonical = ws.describe_node("entity:model:openai/gpt-4o")
    assert canonical["resolution"] == "DECLARED_ALIAS"
    assert canonical["aliases"] == ["model:gpt-4o"] and "alias_of" not in canonical
    observed = ws.describe_node("entity:model:gpt-4o")  # graph nodes keep observed names
    assert observed["alias_of"] == "model:openai/gpt-4o"
