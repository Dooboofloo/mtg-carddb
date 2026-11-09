"""Offline tests for the full-corpus audit helpers."""

import gzip
import json
from collections import Counter

import pytest

from scripts.audit_staging import (
    audit_representatives,
    audit_tag_references,
    filtered_tags,
    printing_oracle_ids,
)
from test_normalization import printing, vocab


def write_jsonl(path, objects):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for item in objects:
            handle.write(json.dumps(item) + "\n")


def test_printing_id_extraction_handles_reversible():
    assert printing_oracle_ids({"oracle_id": "a"}) == {"a"}

    assert printing_oracle_ids({
        "layout": "reversible_card",
        "card_faces": [
            {"oracle_id": "a"},
            {"oracle_id": "a"},
        ],
    }) == {"a"}

    assert printing_oracle_ids({
        "layout": "reversible_card",
        "card_faces": [
            {"oracle_id": "a"},
            {"oracle_id": "b"},
        ],
    }) == {"a", "b"}


def test_audit_selection_skips_art_without_losing_non_art(
    tmp_path, vocab
):
    cards = tmp_path / "cards.jsonl.gz"

    write_jsonl(cards, [
        printing(id="normal", oracle_id="a"),
        printing(
            id="art",
            oracle_id="art-id",
            layout="art_series",
            type_line="Card",
        ),
    ])

    present, art_only, aggregated = audit_representatives(
        cards,
        vocab,
        tmp_path / "spool.sqlite",
    )

    assert present == frozenset({"a"})
    assert art_only == frozenset({"art-id"})
    assert aggregated == 0


def test_audit_representative_conflicts_are_reported(
    tmp_path, vocab
):
    cards = tmp_path / "cards.jsonl.gz"

    write_jsonl(cards, [
        printing(
            id="one",
            oracle_id="a",
            color_identity=["U"],
        ),
        printing(
            id="two",
            oracle_id="a",
            color_identity=["G"],
        ),
    ])

    spool = tmp_path / "spool.sqlite"

    with pytest.raises(
        RuntimeError,
        match="representative audit failed",
    ):
        audit_representatives(cards, vocab, spool)

    # On Windows, this fails if the SQLite connection is still open.
    spool.unlink()
    assert not spool.exists()



def test_art_filter_preserves_unknowns_and_original_data():
    objects = [{
        "object": "tag",
        "slug": "sample",
        "taggings": [
            {"oracle_id": "included", "weight": "median"},
            {"oracle_id": "art-only", "weight": "strong"},
            {"oracle_id": "unknown", "weight": "median"},
        ],
    }]

    counts = Counter()

    result = list(filtered_tags(
        objects,
        frozenset({"art-only"}),
        counts,
    ))

    assert [
        record["oracle_id"]
        for record in result[0]["taggings"]
    ] == ["included", "unknown"]

    assert counts == Counter({"sample": 1})
    assert len(objects[0]["taggings"]) == 3


def test_tag_reference_audit_allows_only_confirmed_art(tmp_path):
    path = tmp_path / "tags.jsonl.gz"

    write_jsonl(path, [{
        "object": "tag",
        "slug": "sample",
        "taggings": [
            {"oracle_id": "included"},
            {"oracle_id": "art-only"},
        ],
    }])

    assert audit_tag_references(
        path,
        frozenset({"included"}),
        frozenset({"art-only"}),
    ) == 1

    write_jsonl(path, [{
        "object": "tag",
        "slug": "sample",
        "taggings": [
            {"oracle_id": "not-in-export"},
        ],
    }])

    with pytest.raises(
        RuntimeError,
        match="unexpected unresolved",
    ):
        audit_tag_references(
            path,
            frozenset({"included"}),
            frozenset({"art-only"}),
        )
