"""Source-backed historical and novelty type-line regression tests."""

import pytest

from mtg_carddb.catalogs import make_type_vocabulary
from mtg_carddb.normalization import NormalizationError, normalize_printing
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
