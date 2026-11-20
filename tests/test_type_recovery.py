"""Resilient type parsing retains source facts and reports recoveries."""

from __future__ import annotations

import pytest

from mtg_carddb.normalization import (
    NormalizationError, TypeVocabulary, normalize_printing,
)
from mtg_carddb.representatives import select_representative
from mtg_carddb.ingestion import ingest_printings
from test_normalization import printing
from test_ingestion import source
from mtg_carddb.schema import staging_database


@pytest.fixture
def types():
    return TypeVocabulary(
        supertypes={"Legendary"},
        card_types={"Creature", "Artifact", "Instant", "Land"},
        subtypes={"Human", "Advisor", "Noble", "Wizard", "Vampire", "Aetherborn"},
    )


def test_missing_separator_inferred_not_hardcoded(types):
    for suffix in ("Human Advisor", "Human Noble", "Human Wizard"):
        raw = f"Legendary Creature {suffix}"
        result = types.interpret(raw)
        assert result.supertypes == frozenset({"Legendary"})
        assert result.types == frozenset({"Creature"})
        assert result.subtypes == frozenset(suffix.split())
        assert result.issues == ("inferred_separator",)
        with pytest.raises(NormalizationError):
            types.interpret(raw, strict=True)
    record, = normalize_printing(
        printing(type_line="Legendary Creature Human Noble"), types
    )
    assert record.faces[0].type_line == "Legendary Creature Human Noble"
    assert record.type_issues == ("inferred_separator",)


def test_unknown_subtype_not_misspelled_or_rejected(types):
    record, = normalize_printing(
        printing(type_line="Legendary Creature — Human FutureSpecies"), types
    )
    assert record.faces[0].subtypes == frozenset({"Human", "FutureSpecies"})
    assert record.type_issues == ("unknown_subtype",)
    with pytest.raises(NormalizationError):
        types.interpret("Legendary Creature — Human FutureSpecies", strict=True)


def test_unknown_multiword_subtype_kept_together(types):
    result = types.interpret("Creature — Human Mystery Species Wizard")
    assert result.subtypes == frozenset({"Human", "Mystery Species", "Wizard"})
    assert result.issues == ("unknown_subtype",)


def test_unknown_card_types_or_ambiguous_undelimited_lines_still_fail(types):
    for raw in (
        "Legendary Wrongtype — Human",
        "Creature BogusSubtype",
        "Creature Human NewSubtype",
        "Creature — ",
        "Creature — Human — Wizard",
    ):
        with pytest.raises(NormalizationError):
            types.parse(raw)


def test_alternate_separator_is_diagnostic(types):
    result = types.interpret("Creature -- Human")
    assert result.subtypes == frozenset({"Human"})
    assert result.issues == ("alternate_separator",)


def test_type_recovery_does_not_mask_real_mechanical_conflicts(types):
    repaired, = normalize_printing(
        printing(id="repaired", type_line="Creature Human", oracle_text="Draw a card."),
        types,
    )
    canonical, = normalize_printing(
        printing(id="canonical", type_line="Creature — Human", oracle_text="Draw a card."),
        types,
    )
    selected = select_representative((repaired, canonical))
    assert selected.mechanics_printing_id == "canonical"
    assert selected.card.faces[0].subtypes == frozenset({"Human"})

    changed, = normalize_printing(
        printing(id="changed", type_line="Creature Human", oracle_text="Different effect."),
        types,
    )
    with pytest.raises(NormalizationError, match="oracle_text"):
        select_representative((canonical, changed))


def test_ingestion_collects_bounded_structured_recoveries(tmp_path, types):
    normalized = [
        source(id="good", type_line="Creature — Human", set="dgm", set_id="sid1"),
        source(id="odd", type_line="Creature -- Human", set="dgm", set_id="sid1"),
        source(id="unknown", type_line="Creature — Oddball", set="dgm", set_id="sid1"),
    ]
    # Test fixture uses a shared Oracle ID; each printing retains identical
    # non-type Oracle facts, and repairs do not cause conflicts.
    with staging_database(tmp_path / "test.sqlite") as conn:
        conn.execute(
            "INSERT INTO sets VALUES (?, ?, ?, ?, ?, ?)",
            ("sid1", "dgm", "Dragon's Maze", "expansion", "2013-05-03", 0),
        )
        stats = ingest_printings(conn, normalized, types)
    assert dict(stats.type_line_recoveries) == {
        "alternate_separator": 1, "unknown_subtype": 1
    }
    assert ("unknown_subtype", "unknown", "Creature — Oddball") in stats.type_line_examples
