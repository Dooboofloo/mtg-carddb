"""Tests for source-backed art-series exclusion policy."""

from collections import Counter

from mtg_carddb.import_policy import (
    exclude_art_only_taggings,
    find_art_only_oracle_ids,
)


def test_art_only_identity_is_excluded():
    printings = [
        {"layout": "art_series", "oracle_id": "art-only"},
        {"layout": "normal", "oracle_id": "ordinary"},
    ]

    assert find_art_only_oracle_ids(printings) == frozenset({
        "art-only",
    })


def test_shared_identity_with_real_printing_is_not_excluded():
    printings = [
        {"layout": "art_series", "oracle_id": "shared"},
        {"layout": "normal", "oracle_id": "shared"},
        {"layout": "art_series", "oracle_id": "exclusive"},
    ]

    assert find_art_only_oracle_ids(printings) == frozenset({
        "exclusive",
    })


def test_reversible_face_id_prevents_false_art_exclusion():
    printings = [
        {"layout": "art_series", "oracle_id": "face-id"},
        {
            "layout": "reversible_card",
            "card_faces": [
                {"oracle_id": "face-id"},
                {"oracle_id": "another-id"},
            ],
        },
    ]

    assert find_art_only_oracle_ids(printings) == frozenset()


def test_tag_filter_preserves_other_assignments_and_metadata():
    tags = [{
        "slug": "example",
        "aliases": ["alias"],
        "parent_ids": ["parent"],
        "taggings": [
            {"oracle_id": "ordinary", "weight": "median"},
            {"oracle_id": "art-only", "weight": "strong"},
            {"oracle_id": "unknown", "weight": "median"},
            {"oracle_id": None},
        ],
    }]

    counts = Counter()
    result = list(exclude_art_only_taggings(
        tags,
        frozenset({"art-only"}),
        counts,
    ))

    assert [
        item["oracle_id"]
        for item in result[0]["taggings"]
    ] == ["ordinary", "unknown", None]

    assert result[0]["aliases"] == ["alias"]
    assert result[0]["parent_ids"] == ["parent"]
    assert counts == Counter({"example": 1})

    # Filtering cannot modify the original export objects.
    assert len(tags[0]["taggings"]) == 4
