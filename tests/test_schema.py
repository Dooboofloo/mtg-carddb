"""Verify schema construction against the frozen DDL and reader contract."""

import sqlite3
from pathlib import Path

import pytest

from mtg_carddb.schema import (
    SCHEMA_SQL, SCHEMA_VERSION, SchemaBuildError,
    create_schema, staging_database,
)
from mtg_carddb.storage import SQLiteStore, DatabaseCompatibilityError

DOCS = Path(__file__).resolve().parents[1] / "docs" / "schema.md"


def test_schema_sql_matches_frozen_document():
    document = DOCS.read_text(encoding="utf-8")
    documented = document.split("```sql", 1)[1].split("```", 1)[0].strip()
    assert SCHEMA_SQL.strip() == documented


def test_staging_database_has_exact_schema_and_foreign_keys(tmp_path):
    target = tmp_path / "build.sqlite"
    with staging_database(target) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        assert names == {
            "cards", "faces", "face_types", "card_keywords",
            "card_legalities", "tags", "tag_aliases", "tag_relationships",
            "card_tags", "sets", "card_set_rarities", "database_metadata",
            "source_metadata",
        }
        columns = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
        assert "game_changer" in columns
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO faces (oracle_id, face_index, name, type_line) "
                         "VALUES ('missing', 0, 'X', 'Creature')")
    assert target.is_file()
    # Empty build is not publishable: database_metadata is still absent.
    with pytest.raises(DatabaseCompatibilityError, match="database_metadata"):
        SQLiteStore.open(target)


def test_schema_compatible_with_existing_readonly_storage(tmp_path):
    target = tmp_path / "stage.sqlite"
    with staging_database(target) as conn:
        conn.execute(
            "INSERT INTO database_metadata VALUES (1, '2026-11-06T00:00:00Z', '0.1.0')"
        )
    with SQLiteStore.open(target) as store:
        assert store.all_ids() == frozenset()
        assert store.info().schema_version == SCHEMA_VERSION


def test_staging_never_overwrites_an_existing_file(tmp_path):
    target = tmp_path / "stage.sqlite"
    target.write_bytes(b"DO NOT TOUCH")
    with pytest.raises(FileExistsError):
        with staging_database(target):
            pass
    assert target.read_bytes() == b"DO NOT TOUCH"


def test_failed_staging_build_is_removed(tmp_path):
    target = tmp_path / "stage.sqlite"
    with pytest.raises(RuntimeError, match="abort"):
        with staging_database(target) as conn:
            conn.execute(
                "INSERT INTO database_metadata VALUES (1, 'today', '0.1.0')"
            )
            raise RuntimeError("abort")
    assert not target.exists()


def test_creation_rejects_existing_schema_and_nonzero_version():
    with sqlite3.connect(":memory:") as conn:
        create_schema(conn)
        with pytest.raises(SchemaBuildError, match="empty"):
            create_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA user_version = 4")
        with pytest.raises(SchemaBuildError, match="empty"):
            create_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_active_transaction_rejected_without_committing_user_data():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('not committed')")
        with pytest.raises(SchemaBuildError, match="active transaction"):
            create_schema(conn)
        conn.rollback()
        assert conn.execute("SELECT * FROM sentinel").fetchall() == []


def test_sqlite_constraints_include_nullable_game_changer(tmp_path):
    with staging_database(tmp_path / "stage.sqlite") as conn:
        base = ("id", "A", "normal", "Artifact", 1, 0, 0, None, 0)
        for value in (None, 0, 1):
            conn.execute("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (str(value), *base[1:], value))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("bad", *base[1:], 2))
