"""Tests for verified Scryfall bulk-source provenance."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import sqlite3

import pytest

from mtg_carddb.provenance import (
    BulkSource,
    SourceProvenanceError,
    sha256_file,
    verify_bulk_sources,
    write_bulk_sources,
)


def make_sources(tmp_path):
    cards = tmp_path / "cards.jsonl.gz"
    tags = tmp_path / "tags.jsonl.gz"

    cards.write_bytes(b"compressed-card-fixture")
    tags.write_bytes(b"compressed-tag-fixture")

    records = (
        BulkSource(
            source_type="default_cards",
            source_url="https://example.com/cards.jsonl.gz",
            downloaded_at="2026-11-06T10:00:00Z",
            sha256=sha256_file(cards),
            record_count=2,
            source_updated_at="2026-11-06T09:00:00Z",
        ),
        BulkSource(
            source_type="oracle_tags",
            source_url="https://example.com/tags.jsonl.gz",
            downloaded_at="2026-11-06T10:01:00Z",
            sha256=sha256_file(tags),
            record_count=3,
        ),
    )

    inputs = {
        "default_cards": (cards, 2),
        "oracle_tags": (tags, 3),
    }

    return records, inputs


def test_sha256_hashes_original_file_bytes(tmp_path):
    path = tmp_path / "source.gz"
    content = b"\x1f\x8b\x08test-input-bytes"
    path.write_bytes(content)

    assert sha256_file(path) == hashlib.sha256(
        content
    ).hexdigest()


def test_valid_bulk_manifest_verifies(tmp_path):
    records, inputs = make_sources(tmp_path)

    verified = verify_bulk_sources(records, inputs)

    assert len(verified) == 2
    assert {r.source_type for r in verified} == {
        "default_cards",
        "oracle_tags",
    }


def test_bulk_manifest_rejects_hash_mismatch(tmp_path):
    records, inputs = make_sources(tmp_path)

    changed = (
        replace(records[0], sha256="0" * 64),
        records[1],
    )

    with pytest.raises(
        SourceProvenanceError,
        match="SHA-256 mismatch",
    ):
        verify_bulk_sources(changed, inputs)


def test_bulk_manifest_rejects_record_count_mismatch(tmp_path):
    records, inputs = make_sources(tmp_path)

    changed = (
        replace(records[0], record_count=999),
        records[1],
    )

    with pytest.raises(
        SourceProvenanceError,
        match="claimed 999 records",
    ):
        verify_bulk_sources(changed, inputs)


@pytest.mark.parametrize(
    "selection",
    [
        (0,),
        (0, 0),
    ],
)
def test_manifest_requires_both_unique_sources(
    tmp_path, selection
):
    records, inputs = make_sources(tmp_path)

    chosen = [records[index] for index in selection]

    with pytest.raises(
        SourceProvenanceError,
        match="exactly one",
    ):
        verify_bulk_sources(chosen, inputs)


def test_manifest_rejects_timestamp_without_timezone():
    with pytest.raises(
        SourceProvenanceError,
        match="UTC offset",
    ):
        BulkSource(
            source_type="default_cards",
            source_url="https://example.com/cards.gz",
            downloaded_at="2026-11-06T10:00:00",
            sha256="a" * 64,
            record_count=1,
        )


def test_verified_sources_are_persisted(tmp_path):
    records, inputs = make_sources(tmp_path)
    verified = verify_bulk_sources(records, inputs)

    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE source_metadata ("
            "source_type TEXT PRIMARY KEY, "
            "source_updated_at TEXT, "
            "downloaded_at TEXT NOT NULL, "
            "source_url TEXT NOT NULL, "
            "sha256 TEXT NOT NULL, "
            "record_count INTEGER NOT NULL)"
        )

        assert write_bulk_sources(
            connection, verified
        ) == 2

        rows = connection.execute(
            "SELECT source_type, record_count "
            "FROM source_metadata ORDER BY source_type"
        ).fetchall()

    assert rows == [
        ("default_cards", 2),
        ("oracle_tags", 3),
    ]
