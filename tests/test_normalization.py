"""A Scryfall printing produces candidate(s), not persistent print records."""

import pytest

from mtg_carddb.normalization import (
    Candidate,
    NormalizationError,
    TypeVocabulary,
    normalize_printing,
)


@pytest.fixture
def vocab():
    return TypeVocabulary(
        supertypes={"Legendary", "Basic", "Snow"},
        card_types={
            "Creature", "Artifact", "Sorcery", "Land", "Instant"
        },
        subtypes={
            "Time Lord", "Time", "Lord", "Doctor", "Human",
            "Swamp", "Mountain",
        },
    )


def printing(**updates):
    raw = dict(
        object="card",
        id="printing-1",
        oracle_id="oracle-1",
        lang="en",
        released_at="2024-06-01",
        layout="normal",
        name="Test Doctor",
        type_line="Legendary Creature — Time Lord Doctor",
        cmc=2.5,
        colors=["U"],
        color_identity=["U", "R"],
        produced_mana=["C"],
        keywords=["Flying"],
        reserved=False,
        game_changer=False,
        mana_cost="{1}{U}",
        oracle_text="Draw a card.",
        power="*",
        toughness="2",
        legalities={"modern": "legal"},
        border_color="black",
        set_type="expansion",
    )
    raw.update(updates)
    return raw


def test_ordinary_printing_reconstructs_model_and_types(vocab):
    candidate, = normalize_printing(printing(), vocab)
    card = candidate.to_card()

    assert isinstance(candidate, Candidate)
    assert card.oracle_id == "oracle-1"
    assert card.mana_value == 2.5
    assert card.colors == frozenset("U")
    assert card.color_identity == frozenset("UR")
    assert card.produced_mana == frozenset("C")
    assert card.reserved is False
    assert card.game_changer is False
    assert card.faces[0].subtypes == frozenset({
        "Time Lord", "Doctor"
    })
    assert card.faces[0].power == "*"
    assert candidate.legalities[0].status == "legal"


def test_single_face_missing_and_empty_values_are_different(vocab):
    source = printing(
        colors=[],
        color_identity=[],
        keywords=[],
        oracle_text="",
        mana_cost="",
    )
    card = normalize_printing(source, vocab)[0].to_card()

    assert card.colors == frozenset()
    assert card.faces[0].colors == frozenset()
    assert card.faces[0].oracle_text == ""
    assert card.faces[0].mana_cost == ""
    assert card.keywords == frozenset()
    assert card.color_identity == frozenset()

    del source["colors"]
    missing = normalize_printing(source, vocab)[0].to_card()
    assert missing.colors is None
    assert missing.faces[0].colors is None


def test_split_card_one_oracle_identity_and_two_faces(vocab):
    source = printing(
        name="Armed // Dangerous",
        layout="split",
        type_line="Sorcery // Sorcery",
        cmc=6,
        colors=None,
        color_identity=["R", "G"],
        card_faces=[
            dict(
                name="Armed",
                type_line="Sorcery",
                mana_cost="{1}{R}",
                colors=["R"],
                oracle_text="Target creature gets...",
            ),
            dict(
                name="Dangerous",
                type_line="Sorcery",
                mana_cost="{3}{G}",
                colors=["G"],
                oracle_text="Target creature gets...",
            ),
        ],
    )

    candidate, = normalize_printing(source, vocab)

    assert candidate.missing == frozenset()
    assert tuple(
        face.name for face in candidate.faces
    ) == ("Armed", "Dangerous")
    assert candidate.faces[0].colors == frozenset("R")
    assert candidate.faces[1].colors == frozenset("G")
    assert candidate.to_card().types == frozenset({"Sorcery"})
    assert candidate.to_card().colors is None


def test_modal_or_transform_dfc_face_types_can_differ(vocab):
    source = printing(
        name="Hero // Hidden Hall",
        layout="modal_dfc",
        type_line="Creature // Land",
        colors=None,
        card_faces=[
            dict(
                name="Hero",
                type_line="Creature — Human",
                colors=["U"],
            ),
            dict(
                name="Hidden Hall",
                type_line="Land",
                colors=[],
            ),
        ],
    )

    card = normalize_printing(source, vocab)[0].to_card()
    assert card.types == frozenset({"Creature", "Land"})
    assert card.faces[0].subtypes == frozenset({"Human"})


def test_reversible_faces_are_two_independent_incomplete_identities(vocab):
    source = printing(
        layout="reversible_card",
        name="A // B",
        card_faces=[
            dict(
                oracle_id="a",
                layout="normal",
                name="A",
                type_line="Land",
                cmc=0,
                colors=[],
                oracle_text="",
                mana_cost="",
            ),
            dict(
                oracle_id="b",
                layout="normal",
                name="B",
                type_line="Land",
                cmc=0,
                colors=[],
                oracle_text="",
                mana_cost="",
            ),
        ],
    )
    del source["oracle_id"]

    a, b = normalize_printing(source, vocab)

    assert a.oracle_id == "a"
    assert b.oracle_id == "b"
    assert a.name == "A"
    assert b.name == "B"
    assert a.layout == "normal"
    assert a.faces[0].face_index == 0
    assert a.missing == frozenset({
        "color_identity", "keywords", "reserved"
    })
    assert a.legalities is None

    with pytest.raises(
        NormalizationError, match="color_identity"
    ):
        a.to_card()

    assert b.color_identity is None


def test_reversible_fields_can_be_complete_if_on_the_face(vocab):
    source = printing(
        layout="reversible_card",
        card_faces=[
            dict(
                oracle_id="a",
                layout="normal",
                name="A",
                type_line="Land",
                cmc=0,
                colors=[],
                color_identity=[],
                keywords=[],
                reserved=False,
                legalities={"vintage": "legal"},
            ),
            dict(
                oracle_id="b",
                layout="normal",
                name="B",
                type_line="Land",
                cmc=0,
                colors=[],
                color_identity=[],
                keywords=[],
                reserved=False,
                legalities={"vintage": "legal"},
            ),
        ],
    )
    del source["oracle_id"]

    a, b = normalize_printing(source, vocab)
    assert not a.missing
    assert not b.missing
    assert a.to_card().name == "A"


def test_partial_ordinary_candidate_tracks_missing_required_facts(vocab):
    source = printing()
    del source["keywords"]
    del source["reserved"]
    del source["color_identity"]

    candidate, = normalize_printing(source, vocab)

    assert candidate.missing == frozenset({
        "keywords", "reserved", "color_identity"
    })

    with pytest.raises(
        NormalizationError, match="oracle-1"
    ):
        candidate.to_card()


def test_invalid_card_types_and_subtypes_are_not_guessed(vocab):
    with pytest.raises(
        NormalizationError, match="unrecognized subtype"
    ):
        normalize_printing(
            printing(type_line="Creature — NotKnown"),
            vocab,
        )

    with pytest.raises(
        NormalizationError, match="unrecognized type"
    ):
        normalize_printing(
            printing(type_line="Thing — Human"),
            vocab,
        )


@pytest.mark.parametrize(
    "modification",
    [
        {"cmc": float("nan")},
        {"cmc": -1},
        {"colors": ["C"]},
        {"keywords": "Flying"},
        {"reserved": 0},
        {"legalities": {"modern": "not-a-status"}},
        {"released_at": "sometime"},
        {"id": ""},
    ],
)
def test_malformed_printings_fail_without_fabricated_values(
    vocab, modification
):
    with pytest.raises(NormalizationError):
        normalize_printing(
            printing(**modification), vocab
        )


def test_reversible_root_oracle_id_rejected(vocab):
    source = printing(
        layout="reversible_card",
        card_faces=[{}, {}],
    )

    with pytest.raises(
        NormalizationError, match="root oracle_id"
    ):
        normalize_printing(source, vocab)


def test_multifaced_invalid_face_rejected(vocab):
    with pytest.raises(
        NormalizationError, match="face0"
    ):
        normalize_printing(
            printing(card_faces=[{"name": "A"}]),
            vocab,
        )


def test_vocab_is_defensive_and_multiword_greedy():
    types = {"Creature"}
    vocab = TypeVocabulary(
        {"Legendary"},
        types,
        {"Time", "Lord", "Time Lord"},
    )

    types.add("Sorcery")

    assert vocab.parse(
        "Legendary Creature — Time Lord"
    )[2] == frozenset({"Time Lord"})

    with pytest.raises(NormalizationError):
        vocab.parse("Sorcery")

def test_reversible_same_oracle_id_is_deduplicated(vocab):
    face = {
        "oracle_id": "same-oracle",
        "layout": "normal",
        "name": "Same Card",
        "type_line": "Creature — Human",
        "cmc": 2,
        "colors": ["U"],
        "mana_cost": "{1}{U}",
        "oracle_text": "Draw a card.",
    }
    source = printing(
        layout="reversible_card",
        card_faces=[dict(face), dict(face)],
    )
    del source["oracle_id"]

    candidates = normalize_printing(source, vocab)

    assert len(candidates) == 1
    assert candidates[0].oracle_id == "same-oracle"


def test_reversible_same_id_conflicting_faces_fail(vocab):
    front = {
        "oracle_id": "same-oracle",
        "layout": "normal",
        "name": "Same Card",
        "type_line": "Creature — Human",
        "cmc": 2,
        "colors": ["U"],
    }
    back = dict(front, oracle_text="Different mechanical text.")

    source = printing(
        layout="reversible_card",
        card_faces=[front, back],
    )
    del source["oracle_id"]

    with pytest.raises(
        NormalizationError, match="disagree mechanically"
    ):
        normalize_printing(source, vocab)


def test_minigame_continuation_preserves_missing_type(vocab):
    from mtg_carddb.catalogs import make_type_vocabulary
    from test_catalogs import sample_catalogs

    vocabulary = make_type_vocabulary(sample_catalogs())

    source = printing(
        layout="double_faced_token",
        name="Example Game // Example Game (cont'd)",
        type_line="Card",
        cmc=0,
        colors=None,
        color_identity=[],
        keywords=[],
        card_faces=[
            {
                "name": "Example Game",
                "type_line": "Card",
                "colors": [],
                "oracle_text": "Game instructions.",
            },
            {
                "name": "Example Game (cont'd)",
                "colors": [],
                "oracle_text": "Continued instructions.",
            },
        ],
    )

    card = normalize_printing(source, vocabulary)[0].to_card()

    assert len(card.faces) == 2
    assert card.faces[1].type_line == ""
    assert card.faces[1].types == frozenset()
    assert card.faces[1].oracle_text == "Continued instructions."
    assert card.types == frozenset({"Card"})


def test_missing_type_on_unrelated_back_face_still_fails(vocab):
    source = printing(
        layout="transform",
        card_faces=[
            {"name": "Front", "type_line": "Creature"},
            {"name": "Back"},
        ],
    )

    with pytest.raises(
        NormalizationError, match="type_line"
    ):
        normalize_printing(source, vocab)


def test_nonmana_t_symbol_is_not_colorless_mana(vocab):
    card = normalize_printing(
        printing(produced_mana=["T"]), vocab
    )[0].to_card()

    assert card.produced_mana is None

    normal = normalize_printing(
        printing(produced_mana=[]), vocab
    )[0].to_card()

    assert normal.produced_mana == frozenset()


def test_unexpected_mixed_mana_symbols_still_fail(vocab):
    with pytest.raises(
        NormalizationError, match="produced_mana"
    ):
        normalize_printing(
            printing(produced_mana=["W", "T"]), vocab
        )


def example_reversible_adventure():
    source = printing(
        id="reversible-adventure",
        name="Guardian // Ritual // Guardian",
        layout="reversible_card",
        type_line=None,
        cmc=3,
        colors=None,
        color_identity=["U"],
        keywords=["Flying"],
        card_faces=[
            {
                "oracle_id": "oracle-1",
                "layout": "adventure",
                "name": "Guardian",
                "type_line": "Creature — Human",
                "cmc": 3,
                "colors": ["U"],
                "mana_cost": "{2}{U}",
                "oracle_text": "Creature ability.",
            },
            {
                "oracle_id": "oracle-1",
                "layout": "adventure",
                "name": "Ritual",
                "type_line": "Sorcery — Arcane",
                "cmc": 3,
                "colors": ["U"],
                "mana_cost": "{U}",
                "oracle_text": "Creature ability.",
            },
        ],
    )
    del source["oracle_id"]
    return source


def test_reversible_adventure_preserves_shared_identity():
    vocabulary = TypeVocabulary(
        supertypes={"Legendary"},
        card_types={"Creature", "Sorcery"},
        subtypes={"Human", "Arcane"},
    )

    candidate, = normalize_printing(
        example_reversible_adventure(), vocabulary
    )

    assert candidate.oracle_id == "oracle-1"
    assert candidate.reversible is True
    assert candidate.layout == "adventure"
    assert candidate.name == "Guardian // Ritual"
    assert candidate.type_line == (
        "Creature — Human // Sorcery — Arcane"
    )
    assert candidate.missing == frozenset()
    assert len(candidate.faces) == 2
    assert candidate.faces[1].oracle_text is None

    card = candidate.to_card()
    assert card.types == frozenset({"Creature", "Sorcery"})
    assert card.faces[1].name == "Ritual"


def test_reversible_adventure_conflicting_mana_values_fail():
    vocabulary = TypeVocabulary(
        supertypes=set(),
        card_types={"Creature", "Sorcery"},
        subtypes={"Human", "Arcane"},
    )

    source = example_reversible_adventure()
    source["card_faces"][1]["cmc"] = 4

    with pytest.raises(
        NormalizationError, match="different mana values"
    ):
        normalize_printing(source, vocabulary)


def test_subtype_parser_backtracks_when_longest_prefix_dead_ends():
    vocabulary = TypeVocabulary(
        supertypes=set(),
        card_types={"Creature"},
        subtypes={"A", "A B", "B C"},
    )

    assert vocabulary.parse(
        "Creature — A B C"
    )[2] == frozenset({"A", "B C"})


def test_subtype_parser_keeps_longest_valid_match_preference():
    vocabulary = TypeVocabulary(
        supertypes=set(),
        card_types={"Creature"},
        subtypes={"A", "A B", "B C", "C"},
    )

    assert vocabulary.parse(
        "Creature — A B C"
    )[2] == frozenset({"A B", "C"})


def test_subtype_parser_rejects_unsatisfiable_sequence():
    vocabulary = TypeVocabulary(
        supertypes=set(),
        card_types={"Creature"},
        subtypes={"A B", "B C"},
    )

    with pytest.raises(
        NormalizationError,
        match="unrecognized subtype",
    ):
        vocabulary.parse("Creature — A B C")
