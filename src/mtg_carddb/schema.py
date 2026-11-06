"""Create the frozen SQLite schema for an *unpublished* CardDB build.

This module creates empty staging databases. It never replaces carddb.sqlite,
imports Scryfall records, or declares an empty database ready for readers.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

SCHEMA_VERSION = 1

# Matches the SQL block in docs/schema.md. Tests enforce that correspondence.
SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

-- One row per Oracle identity.
CREATE TABLE cards (
    oracle_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    layout          TEXT NOT NULL,
    type_line       TEXT NOT NULL,
    mana_value      REAL NOT NULL,
    color_identity  INTEGER NOT NULL CHECK (color_identity BETWEEN 0 AND 31),
    colors          INTEGER CHECK (colors BETWEEN 0 AND 31),
    produced_mana   INTEGER CHECK (produced_mana BETWEEN 0 AND 63),
    reserved        INTEGER NOT NULL CHECK (reserved IN (0, 1)),
    game_changer    INTEGER CHECK (game_changer IN (0, 1))
);

-- Normalized face rows, including one face for ordinarily single-faced cards.
CREATE TABLE faces (
    oracle_id       TEXT NOT NULL,
    face_index      INTEGER NOT NULL CHECK (face_index >= 0),
    name            TEXT NOT NULL,
    type_line       TEXT NOT NULL,
    mana_cost       TEXT,
    oracle_text     TEXT,
    colors          INTEGER CHECK (colors BETWEEN 0 AND 31),
    color_indicator INTEGER CHECK (color_indicator BETWEEN 0 AND 31),
    power           TEXT,
    toughness       TEXT,
    loyalty         TEXT,
    defense         TEXT,
    hand_modifier   TEXT,
    life_modifier   TEXT,
    PRIMARY KEY (oracle_id, face_index),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- Parsed from face type lines; the original text is preserved above.
CREATE TABLE face_types (
    oracle_id   TEXT NOT NULL,
    face_index  INTEGER NOT NULL,
    category    TEXT NOT NULL
                CHECK (category IN ('supertype', 'type', 'subtype')),
    value       TEXT NOT NULL,
    PRIMARY KEY (oracle_id, face_index, category, value),
    FOREIGN KEY (oracle_id, face_index)
        REFERENCES faces(oracle_id, face_index) ON DELETE CASCADE
);

-- Scryfall's Oracle-level keyword classifications.
CREATE TABLE card_keywords (
    oracle_id  TEXT NOT NULL,
    keyword    TEXT NOT NULL,
    PRIMARY KEY (oracle_id, keyword),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- One current Scryfall status per card and format.
CREATE TABLE card_legalities (
    oracle_id  TEXT NOT NULL,
    format     TEXT NOT NULL,
    status     TEXT NOT NULL
               CHECK (status IN ('legal', 'restricted', 'banned', 'not_legal')),
    PRIMARY KEY (oracle_id, format),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- Oracle tag identities, independent of tag/card assignments.
CREATE TABLE tags (
    tag_id       TEXT PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL,
    description  TEXT
);

CREATE TABLE tag_aliases (
    tag_id  TEXT NOT NULL,
    alias   TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (tag_id, alias),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
);

-- Directed parent -> child edges. Multiple parents are allowed.
CREATE TABLE tag_relationships (
    parent_id  TEXT NOT NULL,
    child_id   TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id),
    CHECK (parent_id <> child_id),
    FOREIGN KEY (parent_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (child_id) REFERENCES tags(tag_id) ON DELETE CASCADE
);

-- Direct Scryfall taggings only; no duplicated ancestor assignments.
CREATE TABLE card_tags (
    tag_id      TEXT NOT NULL,
    oracle_id   TEXT NOT NULL,
    weight      TEXT NOT NULL,
    annotation  TEXT,
    PRIMARY KEY (tag_id, oracle_id),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- Set identity and limited descriptive metadata (no printings).
CREATE TABLE sets (
    set_id       TEXT PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    set_type     TEXT NOT NULL,
    released_at  TEXT,
    digital      INTEGER NOT NULL CHECK (digital IN (0, 1))
);

-- Distinct observed historical relationships, not individual editions.
CREATE TABLE card_set_rarities (
    oracle_id  TEXT NOT NULL,
    set_id     TEXT NOT NULL,
    rarity     TEXT NOT NULL,
    game       TEXT NOT NULL,
    PRIMARY KEY (oracle_id, set_id, rarity, game),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE,
    FOREIGN KEY (set_id) REFERENCES sets(set_id)
);

-- One database-build record, not a history of old database files.
CREATE TABLE database_metadata (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    created_at       TEXT NOT NULL,
    builder_version  TEXT NOT NULL
);

-- Provenance for each source actually used in this build.
CREATE TABLE source_metadata (
    source_type       TEXT PRIMARY KEY,
    source_updated_at TEXT,
    downloaded_at     TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    sha256            TEXT NOT NULL CHECK (length(sha256) = 64),
    record_count      INTEGER NOT NULL CHECK (record_count >= 0)
);
"""


class SchemaBuildError(RuntimeError):
    """The requested SQLite schema could not be created safely."""


def create_schema(connection: sqlite3.Connection) -> None:
    """Create schema v1 transactionally in an otherwise empty SQLite file.

    The caller owns the connection. A pre-existing schema or nonzero
    user_version is rejected; the function never migrates or drops tables.
    """
    if connection.in_transaction:
        raise SchemaBuildError("create_schema requires no active transaction")

    try:
        existing = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view', 'trigger', 'index') "
            "AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if existing or version != 0:
            raise SchemaBuildError(
                "schema creation requires an empty, unversioned database"
            )

        # SQLite ignores attempts to change foreign_keys inside a transaction.
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise SchemaBuildError("could not enable SQLite foreign keys")

        # executescript() commits any pending transaction first; we rejected
        # those above and place the entire schema inside one explicit txn.
        connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL + "\nCOMMIT;")
    except sqlite3.Error as exc:
        connection.rollback()
        raise SchemaBuildError("could not create CardDB schema v1") from exc


@contextmanager
def staging_database(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Create a *new*, writable staging file and yield its SQLite connection.

    The target must not exist. Normal exit commits and leaves the staging
    file in place; an exception rolls back and removes it. Neither outcome
    activates/replaces a published CardDB database.
    """
    if not isinstance(path, (str, Path)):
        raise TypeError("path must be a string or Path")
    target = Path(path).expanduser().resolve()

    # Exclusive creation prevents silently overwriting a previous build.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)

    connection: sqlite3.Connection | None = None
    completed = False
    try:
        connection = sqlite3.connect(target)
        create_schema(connection)
        yield connection
        connection.commit()
        completed = True
    except BaseException:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
        if not completed:
            target.unlink(missing_ok=True)
