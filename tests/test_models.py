"""Tests for MTG CardDB immutable domain records (no SQLite or network)."""

from dataclasses import FrozenInstanceError

import pytest

from mtg_carddb.models import (
    Card,
    CardFace,
    DatabaseInfo,
    Legality,
    SetInfo,
    SetRarity,
    SourceInfo,
    Tag,
    Tagging,
)


def make_face(index=0, **overrides):
    values = dict(
        face_index=index,
        name="Sample Face",
        type_line="Legendary Creature — Time Lord",
        supertypes={"Legendary"},
        types={"Creature"},
        subtypes={"Time Lord"},
        mana_cost="{2}{U}",
        oracle_text="Draw a card.",
        colors={"U"},
    )
    values.update(overrides)
    return CardFace(**values)


def make_card(**overrides):
    values = dict(
        oracle_id="oracle-1",
        name="Sample Card",
        layout="normal",
        type_line="Legendary Creature — Time Lord",
        mana_value=3,
        colors={"U"},
        color_identity={"U"},
        produced_mana=None,
        reserved=False,
        game_changer=None,
        keywords={"Flying"},
        faces=[make_face()],
    )
    values.update(overrides)
    return Card(**values)


def test_card_normalizes_nested_collections():
    identity = {"U"}
    keywords = {"Flying"}
    faces = [make_face(types={"Creature"})]
    card = make_card(
        color_identity=identity,
        keywords=keywords,
        faces=faces,
    )

    identity.add("G")
    keywords.add("Vigilance")
    faces.clear()

    assert card.color_identity == frozenset({"U"})
    assert card.keywords == frozenset({"Flying"})
    assert isinstance(card.faces, tuple)
    assert len(card.faces) == 1
    assert isinstance(card.faces[0].types, frozenset)
    assert type(card.mana_value) is float


def test_frozen_fields_and_nested_values_cannot_be_mutated():
    card = make_card()

    with pytest.raises(FrozenInstanceError):
        card.name = "Modified"

    with pytest.raises(FrozenInstanceError):
        card.faces[0].name = "Modified"

    with pytest.raises(AttributeError):
        card.keywords.add("Haste")

    with pytest.raises(AttributeError):
        card.faces[0].subtypes.add("Human")


def test_cards_are_value_objects_hashable_and_comparable():
    first = make_card()
    second = make_card()

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert make_card(oracle_id="different") != first


def test_card_requires_faces_and_contiguous_order():
    with pytest.raises(ValueError, match="at least one face"):
        make_card(faces=[])

    with pytest.raises(ValueError, match="contiguous"):
        make_card(faces=[make_face(1)])

    with pytest.raises(ValueError, match="contiguous"):
        make_card(faces=[make_face(0), make_face(2)])

    with pytest.raises(TypeError, match="CardFace"):
        make_card(faces=["not a face"])


def test_union_across_distinct_faces():
    front = make_face(
        0,
        types={"Land"},
        supertypes=set(),
        subtypes=set(),
    )
    back = make_face(
        1,
        types={"Creature", "Artifact"},
        supertypes={"Legendary"},
        subtypes={"Time Lord"},
    )
    card = make_card(faces=[front, back])

    assert card.types == frozenset({
        "Land", "Creature", "Artifact"
    })
    assert card.supertypes == frozenset({"Legendary"})
    assert card.subtypes == frozenset({"Time Lord"})
    assert front.types == frozenset({"Land"})


def test_missing_and_known_colorless_are_distinct():
    unknown = make_card(
        colors=None,
        produced_mana=None,
    )
    colorless = make_card(
        colors=set(),
        produced_mana={"C"},
        color_identity=set(),
    )

    assert unknown.colors is None
    assert colorless.colors == frozenset()
    assert colorless.produced_mana == frozenset({"C"})
    assert colorless.color_identity == frozenset()

    assert make_face(colors=None).colors is None
    assert make_face(colors=set()).colors == frozenset()


def test_preserves_empty_mana_cost_and_symbolic_values():
    face = make_face(
        mana_cost="",
        power="*",
        toughness="1+*",
        loyalty="X",
        defense="8",
        hand_modifier="-1",
        life_modifier="+2",
    )

    assert face.mana_cost == ""
    assert face.power == "*"
    assert face.toughness == "1+*"
    assert face.loyalty == "X"
    assert face.hand_modifier == "-1"
    assert face.life_modifier == "+2"


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), -1],
)
def test_invalid_mana_values(value):
    with pytest.raises(ValueError):
        make_card(mana_value=value)


def test_bool_is_not_a_mana_value_or_face_index():
    with pytest.raises(TypeError):
        make_card(mana_value=True)

    with pytest.raises(TypeError):
        make_face(index=False)


@pytest.mark.parametrize(
    "name,value",
    [
        ("colors", {"C"}),
        ("color_identity", {"X"}),
        ("produced_mana", {"X"}),
        ("color_identity", {"u"}),
    ],
)
def test_invalid_color_symbols(name, value):
    with pytest.raises(ValueError):
        make_card(**{name: value})


def test_malformed_sets_rejected_not_split_into_characters():
    with pytest.raises(TypeError):
        make_face(subtypes="Time Lord")

    with pytest.raises(TypeError):
        make_card(keywords="Flying")

    with pytest.raises(TypeError):
        make_card(color_identity="WUG")


@pytest.mark.parametrize("flag", [True, False, None])
def test_game_changer_preserves_three_states(flag):
    assert make_card(game_changer=flag).game_changer is flag


def test_other_immutable_records_and_nested_tag_sets():
    aliases = {"mana ramp"}
    tag = Tag(
        "ramp",
        "Ramp",
        aliases=aliases,
        parents={"mana"},
        children={"land-ramp"},
    )
    aliases.add("modified")

    assert tag.aliases == frozenset({"mana ramp"})
    assert tag.parents == frozenset({"mana"})

    with pytest.raises(FrozenInstanceError):
        tag.slug = "other"

    assert Legality("vintage", "restricted").status == "restricted"
    assert SetRarity("m21", "common", "paper").game == "paper"
    assert Tagging("ramp", "strong", None).annotation is None
    assert SetInfo(
        "m21",
        "Core Set 2021",
        "core",
        "2020-07-03",
        False,
    ).digital is False


def test_source_metadata_is_copied_sorted_and_immutable():
    late = SourceInfo(
        "oracle_tags",
        None,
        "2026-11-06",
        "https://example.test/tags",
        "a" * 64,
        42,
    )
    early = SourceInfo(
        "default_cards",
        "2026-11-05",
        "2026-11-06",
        "https://example.test/cards",
        "b" * 64,
        100,
    )

    sources = [late, early]
    info = DatabaseInfo(
        1,
        "2026-11-06",
        "0.1.0",
        sources,
    )
    sources.clear()

    assert info.sources == (early, late)
    assert isinstance(info.sources, tuple)

    with pytest.raises(FrozenInstanceError):
        info.schema_version = 2


def test_invalid_legality_and_record_count():
    with pytest.raises(ValueError):
        Legality("modern", "maybe")

    with pytest.raises(ValueError):
        SourceInfo(
            "default_cards",
            None,
            "today",
            "url",
            "a" * 64,
            -1,
        )
