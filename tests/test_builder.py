"""Offline tests for the non-activating snapshot builder."""

from contextlib import contextmanager
from types import SimpleNamespace
import sqlite3

import pytest

from mtg_carddb import builder
from mtg_carddb.builder import (
    SnapshotBuildError,
    _validate_counts,
    _validate_integrity,
    build_snapshot,
)
from mtg_carddb.provenance import BulkSource, sha256_file


def test_builder_never_overwrites_existing_output(tmp_path):
    cards = tmp_path / "cards.jsonl.gz"
    tags = tmp_path / "tags.jsonl.gz"
    target = tmp_path / "existing.sqlite"

    cards.touch()
    tags.touch()
    target.write_bytes(b"important existing database")

    with pytest.raises(FileExistsError):
        build_snapshot(cards, tags, target)

    assert target.read_bytes() == b"important existing database"


def test_builder_rejects_missing_source_without_creating_output(
    tmp_path,
):
    target = tmp_path / "missing.sqlite"

    with pytest.raises(FileNotFoundError):
        build_snapshot(
            tmp_path / "missing-cards.jsonl.gz",
            tmp_path / "missing-tags.jsonl.gz",
            target,
        )

    assert not target.exists()


def test_builder_deletes_failed_staging_file(
    tmp_path, monkeypatch,
):
    cards = tmp_path / "cards.jsonl.gz"
    tags = tmp_path / "tags.jsonl.gz"
    target = tmp_path / "staging.sqlite"

    cards.touch()
    tags.touch()

    monkeypatch.setattr(
        builder,
        "iter_default_cards",
        lambda _: iter([
            {"layout": "normal", "oracle_id": "real"}
        ]),
    )
    monkeypatch.setattr(
        builder, "write_sets", lambda *_: 0
    )

    def fail_import(*_):
        raise ValueError("synthetic import failure")

    monkeypatch.setattr(
        builder, "ingest_printings", fail_import
    )

    with pytest.raises(
        ValueError, match="synthetic import failure"
    ):
        build_snapshot(
            cards,
            tags,
            target,
            references=SimpleNamespace(
                sets=(),
                vocabulary=object(),
            ),
        )

    assert not target.exists()


def test_builder_filters_only_art_ids_and_keeps_snapshot(
    tmp_path, monkeypatch,
):
    cards = tmp_path / "cards.jsonl.gz"
    tags = tmp_path / "tags.jsonl.gz"
    target = tmp_path / "new.sqlite"

    cards.touch()
    tags.touch()

    manifest = (
        BulkSource(
            source_type="default_cards",
            source_url="https://example.com/cards.gz",
            downloaded_at="2026-11-06T10:00:00Z",
            sha256=sha256_file(cards),
            record_count=2,
        ),
        BulkSource(
            source_type="oracle_tags",
            source_url="https://example.com/tags.gz",
            downloaded_at="2026-11-06T10:01:00Z",
            sha256=sha256_file(tags),
            record_count=1,
        ),
    )

    printings = [
        {"layout": "art_series", "oracle_id": "art-only"},
        {"layout": "normal", "oracle_id": "real"},
    ]

    monkeypatch.setattr(
        builder,
        "iter_default_cards",
        lambda _: iter(printings),
    )
    monkeypatch.setattr(
        builder, "write_sets", lambda *_: 1
    )

    def fake_cards(_conn, source, _vocabulary):
        assert list(source) == printings

        return SimpleNamespace(
            printings=2,
            excluded_printings=1,
            oracle_cards=1,
            historical_observations=0,
        )

    monkeypatch.setattr(
        builder, "ingest_printings", fake_cards
    )

    monkeypatch.setattr(
        builder,
        "iter_oracle_tags",
        lambda _: iter([{
            "slug": "test",
            "taggings": [
                {"oracle_id": "real"},
                {"oracle_id": "art-only"},
            ],
        }]),
    )

    def fake_tags(_conn, source):
        tags_received = list(source)

        assert tags_received[0]["taggings"] == [
            {"oracle_id": "real"}
        ]

        return SimpleNamespace(tags=1, taggings=1)

    monkeypatch.setattr(
        builder, "ingest_oracle_tags", fake_tags
    )

    # Mocked importers don't write actual rows. The real row-count
    # checks are exercised by the full-data integration run.
    monkeypatch.setattr(
        builder,
        "_validate_counts",
        lambda *_args, **_kwargs: None,
    )

    class FakeDatabase:
        def __len__(self):
            return 1

        def all_cards(self):
            return frozenset({"real"})

        def by_type(self, _type):
            return frozenset({"real"})

    @contextmanager
    def fake_open(path):
        assert path == target
        yield FakeDatabase()

    monkeypatch.setattr(
        builder.CardDatabase, "open", fake_open
    )

    result = build_snapshot(
        cards,
        tags,
        target,
        references=SimpleNamespace(
            sets=(),
            vocabulary=object(),
        ),
        bulk_sources=manifest,
    )

    assert result.path == target
    assert result.cards == 1
    assert result.art_only_oracle_ids == 1
    assert result.excluded_art_taggings == 1
    assert target.is_file()

    with sqlite3.connect(target) as db:
        assert db.execute(
            "SELECT builder_version "
            "FROM database_metadata WHERE id=1"
        ).fetchone() == ("snapshot-builder-v1",)

    assert result.provenance_sources == 2

    with sqlite3.connect(target) as db:
        recorded = db.execute(
            "SELECT source_type, record_count "
            "FROM source_metadata ORDER BY source_type"
        ).fetchall()

    assert recorded == [
        ("default_cards", 2),
        ("oracle_tags", 1),
    ]


def test_row_count_validation_rejects_mismatch():
    with sqlite3.connect(":memory:") as db:
        for table in (
            "sets",
            "cards",
            "card_set_rarities",
            "tags",
            "card_tags",
        ):
            db.execute(
                f"CREATE TABLE {table} (id TEXT)"
            )

        with pytest.raises(
            SnapshotBuildError,
            match="cards: expected 1",
        ):
            _validate_counts(
                db,
                sets=0,
                cards=1,
                historical_observations=0,
                tags=0,
                taggings=0,
            )


def test_integrity_validation_rejects_foreign_key_violation():
    with sqlite3.connect(":memory:") as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY)"
        )
        db.execute(
            "CREATE TABLE child "
            "(parent_id INTEGER REFERENCES parent(id))"
        )
        db.execute(
            "INSERT INTO child VALUES (999)"
        )

        with pytest.raises(
            SnapshotBuildError, match="foreign-key"
        ):
            _validate_integrity(db)


def test_reference_archive_provenance_without_bulk_manifest(
    tmp_path, monkeypatch,
):
    from mtg_carddb.reference_sources import (
        ReferenceArchive,
        ReferenceSource,
    )

    cards = tmp_path / "cards.gz"
    tags = tmp_path / "tags.gz"
    target = tmp_path / "archived.sqlite"

    cards.touch()
    tags.touch()

    reference = ReferenceSource(
        source_type="sets:page:1",
        source_url="https://api.scryfall.com/sets",
        filename="sets-0001.json",
        downloaded_at="2026-11-10T12:00:00Z",
        sha256="a" * 64,
        record_count=2,
    )

    archive = ReferenceArchive(
        tmp_path,
        (reference,),
    )

    references = SimpleNamespace(
        sets=(),
        vocabulary=object(),
    )

    monkeypatch.setattr(
        builder,
        "replay_reference_archive",
        lambda _: (references, archive),
    )

    monkeypatch.setattr(
        builder,
        "iter_default_cards",
        lambda _: iter(()),
    )

    monkeypatch.setattr(
        builder,
        "write_sets",
        lambda *_: 0,
    )

    monkeypatch.setattr(
        builder,
        "ingest_printings",
        lambda *_: SimpleNamespace(
            printings=0,
            excluded_printings=0,
            oracle_cards=0,
            historical_observations=0,
        ),
    )

    monkeypatch.setattr(
        builder,
        "ingest_oracle_tags",
        lambda *_: SimpleNamespace(
            tags=0,
            taggings=0,
        ),
    )

    result = build_snapshot(
        cards,
        tags,
        target,
        reference_archive=archive,
    )

    assert result.provenance_sources == 1

    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT source_type, record_count "
            "FROM source_metadata"
        ).fetchall() == [
            ("sets:page:1", 2)
        ]
