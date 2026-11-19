"""Source-backed historical and novelty type-line regression tests."""

import pytest

from mtg_carddb.catalogs import make_type_vocabulary
from mtg_carddb.normalization import NormalizationError, TypeVocabulary, normalize_printing
from test_catalogs import sample_catalogs
from test_normalization import printing


@pytest.mark.parametrize(
    "source, supertypes, types, subtypes",
    [
        ("Host Creature — Horse", {"Host"}, {"Creature"}, {"Horse"}),
        (
            "Host Artifact Creature — Construct",
            {"Host"}, {"Artifact", "Creature"}, {"Construct"},
        ),
        ("Summon Dragon", set(), {"Creature"}, {"Dragon"}),
        ("Summon — Dinosaur", set(), {"Creature"}, {"Dinosaur"}),
        ("Summon Legend", set(), {"Creature"}, {"Legend"}),
        ("Summon Knights", set(), {"Creature"}, {"Knights"}),
        ("Poly Artifact", set(), {"Artifact"}, set()),
        ("instant", set(), {"Instant"}, set()),
        (
            "Elemental Instant — Fire",
            set(), {"Elemental", "Instant"}, {"Fire"},
        ),
        (
            "Legendary Universewalker — Byode",
            {"Legendary"}, {"Universewalker"}, {"Byode"},
        ),
        ("Eaturecray — Igpay", set(), {"Creature"}, {"Igpay"}),
        (
            "Scariest Creature You'll Ever See",
            set(), {"Creature"}, set(),
        ),
        ("Phenome-nom", set(), {"Phenome-nom"}, set()),
        (
            "pLAnE — sECreT LaIR",
            set(), {"Plane"}, {"sECreT LaIR"},
        ),
        (
            "Creature — Human Designer",
            set(), {"Creature"}, {"Human", "Designer"},
        ),
        (
            "Creature — Lady of Proper Etiquette",
            set(), {"Creature"}, {"Lady of Proper Etiquette"},
        ),
        (
            "Creature — Turtle and/or Elephant",
            set(), {"Creature"}, {"Turtle and/or Elephant"},
        ),
        (
            "Creature — The Biggest, Baddest, Nastiest,",
            set(), {"Creature"}, {"The Biggest, Baddest, Nastiest,"},
        ),
        (
            "Land — Ulamog's Power-Plant",
            set(), {"Land"}, {"Ulamog's Power-Plant"},
        ),
        (
            "Battle — Control Point",
            set(), {"Battle"}, {"Control Point"},
        ),
        (
            "Token Artifact — Lander",
            set(), {"Token", "Artifact"}, {"Lander"},
        ),
        (
            "Dungeon — Undercity",
            set(), {"Dungeon"}, {"Undercity"},
        ),
        (
            "Creature — Human?",
            set(), {"Creature"}, {"Human?"},
        ),
        (
            "Sorcery — Vengeance",
            set(), {"Sorcery"}, {"Vengeance"},
        ),
        (
            "Legendary Artifact Creature — Autobot",
            {"Legendary"}, {"Artifact", "Creature"}, {"Autobot"},
        ),
    ],
)
def test_audited_type_lines(source, supertypes, types, subtypes):
    vocabulary = make_type_vocabulary(sample_catalogs())

    actual = vocabulary.parse(source)

    assert actual == (
        frozenset(supertypes),
        frozenset(types),
        frozenset(subtypes),
    )


@pytest.mark.parametrize(
    "source",
    [
        "Creature — NotKnown",
        "Imaginary",
        "Artifact — Definitely Not A Known Subtype",
        "Legendary Creature -- NotARealSubtype",
        "Legendary Creature Human Wizard",
    ],
)
def test_unknown_type_lines_remain_errors(source):
    vocabulary = make_type_vocabulary(sample_catalogs())

    with pytest.raises(NormalizationError):
        vocabulary.parse(source)


def test_printed_type_line_is_preserved_after_classification():
    vocabulary = make_type_vocabulary(sample_catalogs())

    source = printing(
        name="Historical Creature",
        type_line="Summon Dragon",
        cmc=3,
    )

    candidate, = normalize_printing(source, vocabulary)
    card = candidate.to_card()

    assert card.type_line == "Summon Dragon"
    assert card.faces[0].type_line == "Summon Dragon"
    assert card.types == frozenset({"Creature"})
    assert card.subtypes == frozenset({"Dragon"})


def test_unknown_event_alien_perfomer_alias_preserves_printed_type():
    base = make_type_vocabulary(sample_catalogs())
    vocabulary = TypeVocabulary(
        supertypes=base.supertypes,
        card_types=base.card_types,
        subtypes=base.subtypes | {"Alien"},
    )
    source = printing(
        name="The Alien Spellslayer",
        type_line="Legendary Creature — Alien Perfomer",
    )

    candidate, = normalize_printing(source, vocabulary)
    card = candidate.to_card()
    assert card.type_line == "Legendary Creature — Alien Perfomer"
    assert card.faces[0].type_line == "Legendary Creature — Alien Perfomer"
    assert card.subtypes == frozenset({"Alien", "Performer"})


def test_double_hyphen_type_separator_preserves_printed_type():
    base = make_type_vocabulary(sample_catalogs())
    # Minimal synthetic reference catalog does not contain these normal
    # creature subtypes; real Scryfall catalogs do.
    vocabulary = TypeVocabulary(
        supertypes=base.supertypes,
        card_types=base.card_types,
        subtypes=base.subtypes | {"Aetherborn", "Vampire"},
    )
    assert vocabulary.parse("Legendary Creature -- Aetherborn Vampire") == (
        frozenset({"Legendary"}),
        frozenset({"Creature"}),
        frozenset({"Aetherborn", "Vampire"}),
    )
    source = printing(
        name="Source Divider Example",
        type_line="Legendary Creature -- Aetherborn Vampire",
    )

    candidate, = normalize_printing(source, vocabulary)
    card = candidate.to_card()
    assert card.type_line == "Legendary Creature -- Aetherborn Vampire"
    assert card.faces[0].type_line == "Legendary Creature -- Aetherborn Vampire"
    assert card.supertypes == frozenset({"Legendary"})
    assert card.types == frozenset({"Creature"})
    assert card.subtypes == frozenset({"Aetherborn", "Vampire"})


@pytest.mark.parametrize(
    "name, raw, supertypes, types, subtypes",
    [
        (
            "The Mystery Booster",
            "Legendary Artifact Creature — Booster",
            {"Legendary"}, {"Artifact", "Creature"}, {"Booster"},
        ),
        (
            "Critter, Behind the Camera",
            "Legendary Creature — Critter",
            {"Legendary"}, {"Creature"}, {"Critter"},
        ),
        (
            "The Urza Finder",
            "Legendary Creature — Human Scotsman Artificer",
            {"Legendary"}, {"Creature"}, {"Human", "Scotsman", "Artificer"},
        ),
        (
            "Tomik, Devoted to Chaos",
            "Legendary Creature Human Advisor",
            {"Legendary"}, {"Creature"}, {"Human", "Advisor"},
        ),
        (
            "Paul Atreides, Ducal Heir",
            "Legendary Creature Human Noble",
            {"Legendary"}, {"Creature"}, {"Human", "Noble"},
        ),
    ],
)
def test_current_scryfall_type_audit_exceptions(
    name, raw, supertypes, types, subtypes
):
    # The synthetic catalog omits some ordinary creature types present in
    # Scryfall's production catalog, which we supply explicitly here.
    base = make_type_vocabulary(sample_catalogs())
    vocabulary = TypeVocabulary(
        supertypes=base.supertypes,
        card_types=base.card_types,
        subtypes=base.subtypes | {"Artificer", "Noble"},
    )
    expected = (
        frozenset(supertypes),
        frozenset(types),
        frozenset(subtypes),
    )
    assert vocabulary.parse(raw) == expected

    candidate, = normalize_printing(
        printing(name=name, type_line=raw), vocabulary
    )
    card = candidate.to_card()
    assert card.type_line == raw
    assert card.faces[0].type_line == raw
    assert (card.supertypes, card.types, card.subtypes) == expected
