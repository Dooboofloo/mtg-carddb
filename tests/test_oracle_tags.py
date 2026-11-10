"""Offline Oracle Tag import and bulk-reader tests."""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from mtg_carddb.oracle_tags import (
    OracleTagFormatError,
    OracleTagReadError,
    ingest_oracle_tags,
    iter_oracle_tags,
)
from mtg_carddb.schema import staging_database


def tag(tag_id: str, slug: str, **changes) -> dict:
    raw = {
        "object": "tag", "type": "oracle", "id": tag_id,
        "slug": slug, "label": slug.title(), "description": None,
        "aliases": [], "parent_ids": [], "child_ids": [], "taggings": [],
    }
    raw.update(changes)
    return raw


def tagging(oracle_id: str, weight="strong", annotation=None):
    return {"oracle_id": oracle_id, "weight": weight, "annotation": annotation}


def hierarchy():
    return [
        tag("r", "ramp", child_ids=["l"], taggings=[tagging("a")]),
        tag("l", "land-ramp", parent_ids=["r", "s"],
            aliases=["mana development"], taggings=[tagging("b", "very_strong", "lands")]),
        tag("s", "support", child_ids=["l"], taggings=[tagging("c", "weak")]),
    ]


@pytest.fixture
def db(tmp_path):
    with staging_database(tmp_path / "stage.sqlite") as connection:
        connection.executemany(
            "INSERT INTO cards (oracle_id, name, layout, type_line, "
            "mana_value, color_identity, reserved) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(x, x, "normal", "Creature", 1, 0, 0) for x in "abc"],
        )
        connection.executemany(
            "INSERT INTO faces (oracle_id, face_index, name, type_line) "
            "VALUES (?, 0, ?, 'Creature')",
            [(x, x) for x in "abc"],
        )
        yield connection


def counts(conn):
    return [
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tags", "tag_aliases", "tag_relationships", "card_tags")
    ]


def test_tag_import_persists_direct_assignments_and_dag(db):
    stats = ingest_oracle_tags(db, iter(hierarchy()))
    assert (stats.tags, stats.aliases, stats.relationships, stats.taggings) == (3, 1, 2, 3)
    assert counts(db) == [3, 1, 2, 3]
    assert db.execute("SELECT tag_id, oracle_id, weight, annotation FROM card_tags "
                      "WHERE oracle_id='b'").fetchone() == ("l", "b", "very_strong", "lands")
    assert db.execute("SELECT parent_id, child_id FROM tag_relationships ORDER BY parent_id").fetchall() == [("r", "l"), ("s", "l")]
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'carddb_tag_%'").fetchall() == []
    # No inheritance written into SQL: only one tag for card b.
    assert db.execute("SELECT tag_id FROM card_tags WHERE oracle_id='b'").fetchall() == [("l",)]


def test_unordered_tag_records_and_one_sided_omitted_lists(db):
    objects = [tag("leaf", "leaf", parent_ids=["root"]), tag("root", "root")]
    del objects[1]["child_ids"]
    stats = ingest_oracle_tags(db, objects)
    assert stats.relationships == 1


def test_explicit_omitted_mirror_edge_fails(db):
    with pytest.raises(OracleTagFormatError, match="inconsistent child_ids"):
        ingest_oracle_tags(db, [tag("leaf", "leaf", parent_ids=["root"]), tag("root", "root")])
    assert counts(db) == [0, 0, 0, 0]


def test_cycle_is_rejected_and_rolled_back(db):
    objects = [tag("a", "aa", child_ids=["b"], parent_ids=["b"]),
               tag("b", "bb", child_ids=["a"], parent_ids=["a"])]
    with pytest.raises(OracleTagFormatError, match="cycle"):
        ingest_oracle_tags(db, objects)
    assert counts(db) == [0, 0, 0, 0]


@pytest.mark.parametrize("objects,expected", [
    ([tag("r", "ramp", child_ids=["missing"])], "unknown tag"),
    ([tag("r", "ramp", child_ids=["r"], parent_ids=["r"])], "self-referential"),
    ([tag("r", "ramp"), tag("r", "other")], "duplicate tag ID"),
    ([tag("r", "ramp"), tag("s", "RAMP")], "tag slug"),
    ([tag("r", "ramp", aliases=["boost"]), tag("s", "support", aliases=["BOOST"])], "alias"),
    ([tag("r", "ramp", aliases=["support"]), tag("s", "support")], "tag slug"),
    ([tag("r", "ramp", taggings=[tagging("ghost")])], "unknown or duplicate Oracle"),
    ([tag("r", "ramp", taggings=[tagging("a"), tagging("a")])], "unknown or duplicate Oracle"),
    ([tag("r", "ramp", type="illustration")], "non-Oracle"),
    ([tag("r", "ramp", taggings=[tagging("a", weight=9)])], "weight"),
    ([tag("r", "ramp", taggings=[{"illustration_id": "x", **tagging("a")}])], "illustration"),
    ([tag("r", "ramp", taggings=None)], "taggings"),
    ([tag("r", "ramp", parent_ids="bad")], "parent_ids"),
    ([tag("r", "ramp", aliases=["boost", "boost"])], "aliases"),
])
def test_invalid_export_rolls_back(db, objects, expected):
    with pytest.raises(OracleTagFormatError, match=expected):
        ingest_oracle_tags(db, objects)
    assert counts(db) == [0, 0, 0, 0]
    assert db.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'carddb_tag_%'").fetchall() == []


def test_not_empty_import_and_no_second_import(db):
    with pytest.raises(OracleTagFormatError, match="empty"):
        ingest_oracle_tags(db, [])
    ingest_oracle_tags(db, hierarchy())
    with pytest.raises(OracleTagFormatError, match="already populated"):
        ingest_oracle_tags(db, hierarchy())


def test_preserves_caller_transaction(db):
    db.execute("INSERT INTO database_metadata VALUES (1, 'now', '1.0')")
    assert db.in_transaction
    ingest_oracle_tags(db, hierarchy())
    db.rollback()
    assert counts(db) == [0, 0, 0, 0]
    assert db.execute("SELECT * FROM database_metadata").fetchall() == []


def test_error_preserves_caller_transaction(db):
    db.execute("INSERT INTO database_metadata VALUES (1, 'now', '1.0')")
    with pytest.raises(OracleTagFormatError):
        ingest_oracle_tags(db, [tag("r", "ramp", child_ids=["missing"])])
    assert db.execute("SELECT * FROM database_metadata").fetchall() == [(1, "now", "1.0")]


def test_rejects_foreign_keys_disabled(db):
    db.commit()
    db.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(OracleTagFormatError, match="foreign key"):
        ingest_oracle_tags(db, hierarchy())
    db.execute("PRAGMA foreign_keys=ON")


def write_tags(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_reader_round_trip_and_stream_ingestion(tmp_path, db):
    path = tmp_path / "oracle-tags.jsonl.gz"
    items = hierarchy()
    items[0]["label"] = "Rampé"
    write_tags(path, items)
    assert list(iter_oracle_tags(path)) == items
    stats = ingest_oracle_tags(db, iter_oracle_tags(path))
    assert stats.tags == 3
    assert db.execute("SELECT label FROM tags WHERE tag_id='r'").fetchone()[0] == "Rampé"


def test_reader_yields_first_record_before_bad_later_line(tmp_path):
    path = tmp_path / "broken.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(tag("one", "one")) + "\n")
        stream.write("oops not json\n")
    stream = iter_oracle_tags(path)
    assert next(stream)["slug"] == "one"
    with pytest.raises(OracleTagFormatError, match="line 2"):
        next(stream)


@pytest.mark.parametrize("data", [b'{}\n', b'[]\n'])
def test_reader_rejects_plaintext(tmp_path, data):
    path = tmp_path / "fake.gz"
    path.write_bytes(data)
    with pytest.raises(OracleTagFormatError, match="gzip-compressed"):
        list(iter_oracle_tags(path))


def test_reader_rejects_non_tag_and_empty(tmp_path):
    path = tmp_path / "one.gz"
    write_tags(path, [{"object": "card"}])
    with pytest.raises(OracleTagFormatError, match="Tag object"):
        list(iter_oracle_tags(path))
    write_tags(path, [])
    with pytest.raises(OracleTagFormatError, match="no tag records"):
        list(iter_oracle_tags(path))


def test_reader_corrupt_gzip_trailer(tmp_path):
    path = tmp_path / "truncated.gz"
    write_tags(path, [tag("x", "x")])
    path.write_bytes(path.read_bytes()[:-4])
    with pytest.raises(OracleTagReadError):
        list(iter_oracle_tags(path))


def test_missing_bulk_file_error(tmp_path):
    with pytest.raises(OracleTagReadError):
        list(iter_oracle_tags(tmp_path / "missing.gz"))


def test_tag_reader_maps_oversized_record_to_format_error(
    tmp_path,
    monkeypatch,
):
    from mtg_carddb import oracle_tags
    from mtg_carddb.jsonl_limits import (
        iter_bounded_gzip_lines,
    )

    path = tmp_path / "oversized-tag.gz"
    write_tags(
        path,
        [tag("a", "a")],
    )

    monkeypatch.setattr(
        oracle_tags,
        "iter_bounded_gzip_lines",
        lambda src: iter_bounded_gzip_lines(
            src,
            max_record_bytes=10,
        ),
    )

    with pytest.raises(
        OracleTagFormatError,
        match="line 1 exceeds 10 bytes",
    ):
        list(iter_oracle_tags(path))


def test_tag_reader_maps_total_limit_to_format_error(
    tmp_path,
    monkeypatch,
):
    from mtg_carddb import oracle_tags
    from mtg_carddb.jsonl_limits import (
        iter_bounded_gzip_lines,
    )

    path = tmp_path / "oversized-tags-total.gz"

    write_tags(
        path,
        [
            tag("a", "a"),
            tag("b", "b"),
        ],
    )

    monkeypatch.setattr(
        oracle_tags,
        "iter_bounded_gzip_lines",
        lambda src: iter_bounded_gzip_lines(
            src,
            max_total_bytes=180,
        ),
    )

    with pytest.raises(
        OracleTagFormatError,
        match="export exceeds 180 bytes",
    ):
        list(iter_oracle_tags(path))
