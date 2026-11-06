"""Deterministic, strict selection of Oracle mechanics and legality projections."""

import pytest

from mtg_carddb.normalization import (
    NormalizationError,
    normalize_printing,
)
from mtg_carddb.representatives import (
    RepresentativeConflictError,
    select_representative,
)
from test_normalization import printing, vocab


def candidate(vocab, **changes):
    return normalize_printing(
        printing(**changes), vocab
    )[0]


def test_select_newest_english_and_lexical_id_independent_of_order(vocab):
    source = [
        candidate(vocab, id="b", released_at="2022-01-01"),
        candidate(vocab, id="z", released_at="2025-01-01"),
        candidate(vocab, id="a", released_at="2025-01-01"),
        candidate(
            vocab,
            id="newer-fr",
            lang="fr",
            released_at="2026-01-01",
        ),
    ]

    permutations = (
        source,
        list(reversed(source)),
        [source[2], source[0], source[3], source[1]],
    )

    for group in permutations:
        selected = select_representative(group)
        assert selected.mechanics_printing_id == "a"
        assert selected.card.oracle_id == "oracle-1"
        assert selected.legality_printing_id == "a"


def test_incomplete_candidate_cannot_displace_complete(vocab):
    incomplete = printing(
        id="newer",
        released_at="2026-01-01",
    )
    del incomplete["color_identity"]

    selected = select_representative([
        normalize_printing(incomplete, vocab)[0],
        candidate(
            vocab,
            id="complete",
            released_at="2020-01-01",
        ),
    ])

    assert selected.mechanics_printing_id == "complete"
    assert selected.card.color_identity == frozenset("UR")


def test_reversible_identity_uses_nonreversible_when_available(vocab):
    source = printing(
        layout="reversible_card",
        name="Test Doctor // Other",
        id="reversible",
        released_at="2026-01-01",
        card_faces=[
            dict(
                oracle_id="oracle-1",
                layout="normal",
                name="Test Doctor",
                type_line="Legendary Creature — Time Lord Doctor",
                cmc=2.5,
                colors=["U"],
                oracle_text="Draw a card.",
                mana_cost="{1}{U}",
                power="*",
                toughness="2",
            ),
            dict(
                oracle_id="unrelated",
                layout="normal",
                name="Other",
                type_line="Land",
                cmc=0,
                colors=[],
            ),
        ],
    )
    del source["oracle_id"]

    reverse, _ = normalize_printing(source, vocab)
    ordinary = candidate(
        vocab,
        id="ordinary",
        released_at="2020-01-01",
    )

    selected = select_representative([reverse, ordinary])
    assert selected.mechanics_printing_id == "ordinary"
    assert selected.card.name == "Test Doctor"
    assert selected.card.color_identity == frozenset("UR")


def test_incomplete_reversible_only_is_diagnostic_failure(vocab):
    source = printing(
        layout="reversible_card",
        card_faces=[
            dict(
                oracle_id="a",
                layout="normal",
                name="A",
                type_line="Land",
                cmc=0,
            ),
            dict(
                oracle_id="b",
                layout="normal",
                name="B",
                type_line="Land",
                cmc=0,
            ),
        ],
    )
    del source["oracle_id"]

    candidates = normalize_printing(source, vocab)

    with pytest.raises(
        NormalizationError,
        match="no complete mechanics candidate",
    ):
        select_representative([candidates[0]])


def test_resolved_reversible_only_with_source_backed_legality(vocab):
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
                legalities={"modern": "legal"},
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
                legalities={"modern": "legal"},
            ),
        ],
    )
    del source["oracle_id"]

    a, _ = normalize_printing(source, vocab)
    assert select_representative([a]).card.oracle_id == "a"


def test_true_mechanical_disagreement_is_an_error(vocab):
    first = candidate(vocab, id="first")
    second = candidate(
        vocab, id="second", color_identity=["U"]
    )

    with pytest.raises(
        RepresentativeConflictError, match="color_identity"
    ):
        select_representative([first, second])


def test_face_text_disagreement_is_an_error(vocab):
    first = candidate(vocab, id="first")
    second = candidate(
        vocab,
        id="second",
        oracle_text="Actually a different effect.",
    )

    with pytest.raises(
        RepresentativeConflictError, match="oracle_text"
    ):
        select_representative([first, second])


def test_missing_optional_field_does_not_conflict(vocab):
    selected = select_representative([
        candidate(vocab, id="known", colors=["U"]),
        candidate(vocab, id="unknown", colors=None),
    ])
    assert selected.card.oracle_id == "oracle-1"


def test_gold_legality_does_not_override_ordinary(vocab):
    gold = candidate(
        vocab,
        id="gold",
        border_color="gold",
        legalities={"modern": "not_legal"},
        released_at="2026-01-01",
    )
    ordinary = candidate(
        vocab,
        id="regular",
        border_color="black",
        legalities={"modern": "legal"},
        released_at="2020-01-01",
    )

    selected = select_representative([gold, ordinary])

    assert selected.mechanics_printing_id == "gold"
    assert selected.legality_printing_id == "regular"
    assert selected.legalities[0].status == "legal"


def test_memorabilia_legalities_lower_priority(vocab):
    bad = candidate(
        vocab,
        id="memorabilia",
        set_type="memorabilia",
        legalities={"modern": "not_legal"},
    )
    good = candidate(
        vocab,
        id="good",
        set_type="expansion",
        legalities={"modern": "legal"},
    )

    assert (
        select_representative([bad, good]).legalities[0].status
        == "legal"
    )


def test_equally_eligible_legality_conflict_fails(vocab):
    left = candidate(
        vocab,
        id="one",
        legalities={"modern": "legal"},
    )
    right = candidate(
        vocab,
        id="two",
        legalities={"modern": "banned"},
    )

    with pytest.raises(
        RepresentativeConflictError,
        match="legality disagreement",
    ):
        select_representative([left, right])


def test_only_gold_preserves_supplied_status(vocab):
    selected = select_representative([
        candidate(
            vocab,
            id="gold",
            border_color="gold",
            legalities={"modern": "not_legal"},
        )
    ])
    assert selected.legalities[0].status == "not_legal"


def test_missing_legality_projection_is_not_guessed(vocab):
    no_legalities = printing()
    del no_legalities["legalities"]

    with pytest.raises(
        NormalizationError,
        match="no source-backed legalities",
    ):
        select_representative([
            normalize_printing(no_legalities, vocab)[0]
        ])


def test_cross_oracle_group_rejected(vocab):
    with pytest.raises(
        NormalizationError,
        match="multiple Oracle IDs",
    ):
        select_representative([
            candidate(vocab),
            candidate(vocab, oracle_id="other"),
        ])


def test_empty_group_rejected():
    with pytest.raises(
        NormalizationError,
        match="empty group",
    ):
        select_representative([])


def test_duplicate_printing_id_with_different_data_rejected(vocab):
    with pytest.raises(
        RepresentativeConflictError,
        match="duplicate printing ID",
    ):
        select_representative([
            candidate(vocab, id="same", colors=["U"]),
            candidate(vocab, id="same", colors=None),
        ])


def test_many_consistent_printings_select_deterministically(vocab):
    group = [
        candidate(vocab, id=f"print-{i:04d}")
        for i in range(400)
    ]

    selected = select_representative(reversed(group))
    assert selected.mechanics_printing_id == "print-0000"
