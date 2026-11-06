"""Pure-Python correctness tests for the internal inverted-index engine."""

from dataclasses import dataclass

import pytest

from mtg_carddb.indexes import IndexStore, build_index


@dataclass(frozen=True)
class Sample:
    types: frozenset[str]
    keywords: frozenset[str]
    identity: str


CARDS = {
    "a": Sample(frozenset({"Creature"}), frozenset({"Flying"}), "WU"),
    "b": Sample(frozenset({"Creature", "Artifact"}), frozenset(), "G"),
    "c": Sample(frozenset({"Land"}), frozenset(), ""),
    "d": Sample(frozenset({"Creature"}), frozenset({"Trample"}), "RG"),
}


def rows_for(attribute):
    for oracle_id, card in CARDS.items():
        for value in getattr(card, attribute):
            yield value, oracle_id


def store():
    return IndexStore(
        {
            "type": lambda: rows_for("types"),
            "keyword": lambda: rows_for("keywords"),
        },
        valid_ids=CARDS,
    )


def test_build_index_deduplicates_and_freezes():
    index = build_index([("Creature", "a"), ("Creature", "a"), ("Land", "c")])
    assert index["Creature"] == frozenset({"a"})
    assert isinstance(index["Land"], frozenset)
    with pytest.raises(TypeError):
        index["Creature"] = frozenset({"b"})
    with pytest.raises(AttributeError):
        index["Creature"].add("b")


def test_loads_one_category_only_when_requested_and_once():
    calls = {"type": 0, "keyword": 0}

    def loader(name):
        def load():
            calls[name] += 1
            return list(rows_for("types" if name == "type" else "keywords"))
        return load

    index = IndexStore({name: loader(name) for name in calls})
    assert calls == {"type": 0, "keyword": 0}
    creatures = index.get("type", "Creature")
    assert creatures == frozenset({"a", "b", "d"})
    assert index.get("type", "Creature") is creatures
    assert index.get("type", "Artifact") == frozenset({"b"})
    assert calls == {"type": 1, "keyword": 0}
    assert index.get("keyword", "Flying") == frozenset({"a"})
    assert calls == {"type": 1, "keyword": 1}


def test_unknown_values_return_empty_but_unknown_category_raises():
    index = store()
    assert index.get("type", "Wizard") == frozenset()
    with pytest.raises(KeyError, match="unknown index category"):
        index.get("missing", "Creature")


def test_discovery_is_immutable_and_category_lazy():
    index = store()
    assert index.values("keyword") == frozenset({"Flying", "Trample"})
    with pytest.raises(AttributeError):
        index.values("keyword").add("Reach")


def test_compound_keys_preserve_relationships():
    index = build_index([
        (("m21", "common", "paper"), "a"),
        (("m21", "rare", "paper"), "b"),
        (("eld", "common", "paper"), "b"),
        (("m21", "common", "paper"), "a"),
    ])
    assert index[("m21", "common", "paper")] == frozenset({"a"})
    assert index[("m21", "rare", "paper")] == frozenset({"b"})
    assert index[("eld", "common", "paper")] == frozenset({"b"})


def test_derived_results_are_cached_immutable_and_isolated():
    index = store()
    calls = 0
    produced = {"a", "b"}

    def derive():
        nonlocal calls
        calls += 1
        return produced

    result = index.derive("sample_union", "AB", derive)
    produced.add("c")
    assert result == frozenset({"a", "b"})
    assert index.derive("sample_union", "AB", lambda: {"d"}) is result
    assert calls == 1
    assert index.derive("other_namespace", "AB", lambda: {"d"}) == frozenset({"d"})
    assert index.get("type", "Creature") == frozenset({"a", "b", "d"})


def test_failed_load_is_not_published_and_can_retry():
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            def broken():
                yield "Creature", "a"
                raise RuntimeError("failed halfway")
            return broken()
        return [("Creature", "b")]

    index = IndexStore({"type": loader})
    with pytest.raises(RuntimeError, match="halfway"):
        index.get("type", "Creature")
    assert index.get("type", "Creature") == frozenset({"b"})
    assert calls == 2


def test_failed_derived_result_is_not_cached():
    index = store()
    with pytest.raises(ValueError):
        index.derive("derived", "bad", lambda: ["unknown"])
    assert index.derive("derived", "bad", lambda: ["a"]) == frozenset({"a"})


def test_unknown_ids_and_non_string_members_rejected():
    with pytest.raises(ValueError, match="outside the known universe"):
        IndexStore({"type": lambda: [("Creature", "missing")]}, valid_ids=CARDS).get(
            "type", "Creature"
        )
    with pytest.raises(TypeError, match="string"):
        build_index([("Creature", 1)])
    with pytest.raises(TypeError, match="hashable"):
        build_index([(["Creature"], "a")])


def test_set_operations_equal_naive_filtering():
    index = store()
    creature = index.get("type", "Creature")
    flying = index.get("keyword", "Flying")
    artifact = index.get("type", "Artifact")

    expected = frozenset(
        oracle_id for oracle_id, card in CARDS.items()
        if "Creature" in card.types and "Flying" not in card.keywords
    )
    assert creature - flying == expected
    assert creature & artifact == frozenset({"b"})
    assert creature | index.get("type", "Land") == frozenset(CARDS)
    assert creature ^ artifact == frozenset({"a", "d"})


def test_derived_union_can_use_base_memberships():
    index = store()
    result = index.derive(
        "type_group",
        "permanents",
        lambda: index.get("type", "Creature") | index.get("type", "Land"),
    )
    assert result == frozenset(CARDS)


def test_original_loader_mapping_cannot_change_store_categories():
    loaders = {"type": lambda: [("Creature", "a")]}
    index = IndexStore(loaders)
    loaders["type"] = lambda: [("Creature", "b")]
    assert index.get("type", "Creature") == frozenset({"a"})
