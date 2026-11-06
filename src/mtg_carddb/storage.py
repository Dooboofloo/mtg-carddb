"""Read-only SQLite access to normalized CardDB v1 data.

This is an internal adapter, not the public CardDatabase query interface.
Schema creation, import, refresh, and query semantics belong elsewhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .indexes import IndexStore
from .models import (
    Card, CardFace, DatabaseInfo, Legality, SetInfo, SetRarity,
    SourceInfo, Tag, Tagging,
)

SCHEMA_VERSION = 1
_BITS = (("W", 1), ("U", 2), ("B", 4), ("R", 8), ("G", 16), ("C", 32))

# Validate columns actually consumed by this adapter, not merely table names.
# Additive columns do not invalidate a same-version file.
_REQUIRED = {
    "cards": ("oracle_id", "name", "layout", "type_line", "mana_value",
              "color_identity", "colors", "produced_mana", "reserved", "game_changer"),
    "faces": ("oracle_id", "face_index", "name", "type_line", "mana_cost",
              "oracle_text", "colors", "color_indicator", "power", "toughness",
              "loyalty", "defense", "hand_modifier", "life_modifier"),
    "face_types": ("oracle_id", "face_index", "category", "value"),
    "card_keywords": ("oracle_id", "keyword"),
    "card_legalities": ("oracle_id", "format", "status"),
    "tags": ("tag_id", "slug", "label", "description"),
    "tag_aliases": ("tag_id", "alias"),
    "tag_relationships": ("parent_id", "child_id"),
    "card_tags": ("tag_id", "oracle_id", "weight", "annotation"),
    "sets": ("set_id", "code", "name", "set_type", "released_at", "digital"),
    "card_set_rarities": ("oracle_id", "set_id", "rarity", "game"),
    "database_metadata": ("id", "created_at", "builder_version"),
    "source_metadata": ("source_type", "source_updated_at", "downloaded_at",
                        "source_url", "sha256", "record_count"),
}


class DatabaseOpenError(RuntimeError):
    """A database cannot be opened as a readable SQLite file."""


class DatabaseCompatibilityError(DatabaseOpenError):
    """A SQLite file does not conform to CardDB schema v1."""


class DatabaseClosedError(RuntimeError):
    """An operation requires an open database handle."""


class AmbiguousTagError(ValueError):
    """A tag alias refers to more than one canonical tag."""


def _symbols(mask: int | None, *, mana: bool = False) -> frozenset[str] | None:
    if mask is None:
        return None
    allowed = 63 if mana else 31
    if not isinstance(mask, int) or mask < 0 or mask > allowed:
        raise DatabaseCompatibilityError(f"invalid color mask: {mask!r}")
    return frozenset(
        symbol for symbol, bit in _BITS
        if mask & bit and (mana or symbol != "C")
    )


class SQLiteStore:
    """A single, read-only connection to a normalized CardDB database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection: sqlite3.Connection | None = connection

    @classmethod
    def open(cls, path: str | Path) -> SQLiteStore:
        if not isinstance(path, (str, Path)):
            raise TypeError("path must be a string or Path")
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise DatabaseOpenError(f"database file not found: {target}")
        try:
            # URI encoding also handles filenames containing ?, #, and spaces.
            connection = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            raise DatabaseOpenError(f"cannot open CardDB database: {target}") from exc

        store = cls(connection)
        try:
            store._validate_schema()
        except (sqlite3.DatabaseError, DatabaseOpenError):
            store.close()
            raise
        return store

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DatabaseClosedError("CardDB database handle is closed")
        return self._connection

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    def _validate_schema(self) -> None:
        conn = self._conn()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise DatabaseCompatibilityError(
                    f"unsupported schema version {version}; expected {SCHEMA_VERSION}"
                )
            for table, expected in _REQUIRED.items():
                # All identifiers come from _REQUIRED, not caller input.
                actual = {
                    row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                missing = set(expected) - actual
                if missing:
                    raise DatabaseCompatibilityError(
                        f"table {table!r} missing required columns: {sorted(missing)}"
                    )
            rows = self._rows("SELECT id FROM database_metadata")
            if len(rows) != 1 or rows[0]["id"] != 1:
                raise DatabaseCompatibilityError(
                    "database_metadata must contain exactly the build row id=1"
                )
        except sqlite3.DatabaseError as exc:
            raise DatabaseCompatibilityError("malformed CardDB SQLite schema") from exc

    def close(self) -> None:
        if self._connection is not None:
            conn, self._connection = self._connection, None
            conn.close()

    def __enter__(self) -> SQLiteStore:
        self._conn()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def all_ids(self) -> frozenset[str]:
        return frozenset(row["oracle_id"] for row in self._rows("SELECT oracle_id FROM cards"))

    def _require_card(self, oracle_id: str) -> None:
        if not self._rows("SELECT 1 FROM cards WHERE oracle_id = ?", (oracle_id,)):
            raise KeyError(oracle_id)

    def load_card(self, oracle_id: str) -> Card | None:
        rows = self._rows("SELECT * FROM cards WHERE oracle_id = ?", (oracle_id,))
        if not rows:
            return None
        row = rows[0]
        types: dict[int, dict[str, set[str]]] = {}
        for item in self._rows(
            "SELECT face_index, category, value FROM face_types "
            "WHERE oracle_id = ?", (oracle_id,)
        ):
            buckets = types.setdefault(item["face_index"], {
                "supertype": set(), "type": set(), "subtype": set(),
            })
            buckets[item["category"]].add(item["value"])

        faces = []
        for face in self._rows(
            "SELECT * FROM faces WHERE oracle_id = ? ORDER BY face_index", (oracle_id,)
        ):
            parsed = types.get(face["face_index"], {})
            faces.append(CardFace(
                face_index=face["face_index"], name=face["name"],
                type_line=face["type_line"], mana_cost=face["mana_cost"],
                oracle_text=face["oracle_text"], colors=_symbols(face["colors"]),
                color_indicator=_symbols(face["color_indicator"]),
                supertypes=parsed.get("supertype", frozenset()),
                types=parsed.get("type", frozenset()),
                subtypes=parsed.get("subtype", frozenset()),
                power=face["power"], toughness=face["toughness"],
                loyalty=face["loyalty"], defense=face["defense"],
                hand_modifier=face["hand_modifier"],
                life_modifier=face["life_modifier"],
            ))
        keywords = frozenset(r["keyword"] for r in self._rows(
            "SELECT keyword FROM card_keywords WHERE oracle_id = ?", (oracle_id,)
        ))
        return Card(
            oracle_id=row["oracle_id"], name=row["name"],
            layout=row["layout"], type_line=row["type_line"],
            mana_value=row["mana_value"], colors=_symbols(row["colors"]),
            color_identity=_symbols(row["color_identity"]),
            produced_mana=_symbols(row["produced_mana"], mana=True),
            reserved=bool(row["reserved"]),
            game_changer=(None if row["game_changer"] is None
                          else bool(row["game_changer"])),
            keywords=keywords, faces=tuple(faces),
        )

    def info(self) -> DatabaseInfo:
        row = self._rows(
            "SELECT created_at, builder_version FROM database_metadata WHERE id = 1"
        )[0]
        sources = tuple(SourceInfo(**dict(source)) for source in self._rows(
            "SELECT source_type, source_updated_at, downloaded_at, "
            "source_url, sha256, record_count FROM source_metadata"
        ))
        return DatabaseInfo(
            schema_version=SCHEMA_VERSION, created_at=row["created_at"],
            builder_version=row["builder_version"], sources=sources,
        )

    def legalities_for(self, oracle_id: str) -> tuple[Legality, ...]:
        self._require_card(oracle_id)
        return tuple(Legality(row["format"], row["status"]) for row in self._rows(
            "SELECT format, status FROM card_legalities "
            "WHERE oracle_id = ? ORDER BY format, status", (oracle_id,)
        ))

    def taggings_for(self, oracle_id: str) -> tuple[Tagging, ...]:
        self._require_card(oracle_id)
        return tuple(Tagging(row["slug"], row["weight"], row["annotation"])
                     for row in self._rows(
            "SELECT t.slug, ct.weight, ct.annotation FROM card_tags AS ct "
            "JOIN tags AS t ON t.tag_id = ct.tag_id "
            "WHERE ct.oracle_id = ? ORDER BY t.slug", (oracle_id,)
        ))

    def set_rarities_for(self, oracle_id: str) -> tuple[SetRarity, ...]:
        self._require_card(oracle_id)
        return tuple(SetRarity(row["code"], row["rarity"], row["game"])
                     for row in self._rows(
            "SELECT s.code, csr.rarity, csr.game FROM card_set_rarities AS csr "
            "JOIN sets AS s ON s.set_id = csr.set_id "
            "WHERE csr.oracle_id = ? ORDER BY s.code, csr.rarity, csr.game",
            (oracle_id,),
        ))

    def get_set(self, code: str) -> SetInfo | None:
        rows = self._rows(
            "SELECT code, name, set_type, released_at, digital "
            "FROM sets WHERE code = ?", (code,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["digital"] = bool(row["digital"])
        return SetInfo(**row)

    def get_tag(self, slug: str) -> Tag | None:
        # Canonical exact slug wins over any colliding alias.
        rows = self._rows("SELECT * FROM tags WHERE slug = ?", (slug,))
        if not rows:
            rows = self._rows(
                "SELECT t.* FROM tags AS t JOIN tag_aliases AS a "
                "ON a.tag_id = t.tag_id WHERE a.alias = ? COLLATE NOCASE "
                "ORDER BY t.slug", (slug,)
            )
            if len(rows) > 1:
                raise AmbiguousTagError(f"ambiguous tag alias: {slug!r}")
        if not rows:
            return None
        tag = rows[0]
        tag_id = tag["tag_id"]
        aliases = frozenset(row["alias"] for row in self._rows(
            "SELECT alias FROM tag_aliases WHERE tag_id = ?", (tag_id,)
        ))
        parents = frozenset(row["slug"] for row in self._rows(
            "SELECT t.slug FROM tag_relationships AS r "
            "JOIN tags AS t ON t.tag_id = r.parent_id WHERE r.child_id = ?",
            (tag_id,),
        ))
        children = frozenset(row["slug"] for row in self._rows(
            "SELECT t.slug FROM tag_relationships AS r "
            "JOIN tags AS t ON t.tag_id = r.child_id WHERE r.parent_id = ?",
            (tag_id,),
        ))
        return Tag(slug=tag["slug"], label=tag["label"],
                   description=tag["description"], aliases=aliases,
                   parents=parents, children=children)

    def index_store(self) -> IndexStore:
        """Wire SQLite table scans into the existing category-lazy index engine.

        Compound set-rarity-game and format-status keys remain coupled.
        Face and whole-card color facts remain separate; aggregate matching is
        a higher-level derived query, not a fabricated stored SQL field.
        """
        self._conn()

        def single(sql: str, *, casefold: bool = False):
            def load():
                for row in self._rows(sql):
                    key = row["k"]
                    yield (key.casefold() if casefold else key), row["oracle_id"]
            return load

        def compound(sql: str, fields: tuple[str, ...]):
            def load():
                for row in self._rows(sql):
                    yield tuple(row[field] for field in fields), row["oracle_id"]
            return load

        return IndexStore({
            "name": single("SELECT name AS k, oracle_id FROM cards", casefold=True),
            "layout": single("SELECT layout AS k, oracle_id FROM cards"),
            "identity": single("SELECT color_identity AS k, oracle_id FROM cards"),
            "card_color": single("SELECT colors AS k, oracle_id FROM cards "
                                 "WHERE colors IS NOT NULL"),
            "face_color": single("SELECT colors AS k, oracle_id FROM faces "
                                 "WHERE colors IS NOT NULL"),
            "mana_value": single("SELECT mana_value AS k, oracle_id FROM cards"),
            "produced_mana": single("SELECT produced_mana AS k, oracle_id FROM cards "
                                    "WHERE produced_mana IS NOT NULL"),
            "game_changer": single("SELECT game_changer AS k, oracle_id FROM cards "
                                   "WHERE game_changer IS NOT NULL"),
            "type": single("SELECT value AS k, oracle_id FROM face_types "
                           "WHERE category = 'type'"),
            "supertype": single("SELECT value AS k, oracle_id FROM face_types "
                                "WHERE category = 'supertype'"),
            "subtype": single("SELECT value AS k, oracle_id FROM face_types "
                              "WHERE category = 'subtype'"),
            "keyword": single("SELECT keyword AS k, oracle_id FROM card_keywords"),
            "tag": single("SELECT t.slug AS k, ct.oracle_id FROM card_tags AS ct "
                          "JOIN tags AS t ON t.tag_id = ct.tag_id"),
            "legality": compound(
                "SELECT format, status, oracle_id FROM card_legalities",
                ("format", "status"),
            ),
            "set_rarity": compound(
                "SELECT s.code, csr.rarity, csr.game, csr.oracle_id "
                "FROM card_set_rarities AS csr "
                "JOIN sets AS s ON s.set_id = csr.set_id",
                ("code", "rarity", "game"),
            ),
        }, valid_ids=self.all_ids())
