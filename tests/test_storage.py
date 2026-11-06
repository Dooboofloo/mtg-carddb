"""SQLite adapter contract tests against a synthetic schema-v1 database."""

import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from mtg_carddb.storage import (
    AmbiguousTagError, DatabaseClosedError, DatabaseCompatibilityError,
    DatabaseOpenError, SQLiteStore,
)

SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "schema.md"


def make_database(path: Path) -> None:
    # Until an importer/schema-builder exists, use the actual frozen DDL.
    ddl = SCHEMA.read_text(encoding="utf-8").split("```sql", 1)[1].split("```", 1)[0]
    with sqlite3.connect(path) as conn:
        conn.executescript(ddl)
        conn.execute("INSERT INTO database_metadata VALUES (1, ?, ?)",
                     ("2026-11-06T16:00:00Z", "0.1.0"))
        conn.execute("INSERT INTO source_metadata VALUES (?, ?, ?, ?, ?, ?)",
                     ("default_cards", None, "2026-11-06T16:00:00Z",
                      "https://example.test/default.json", "a" * 64, 3))

        cards = [
            ("a", "Armed // Dangerous", "split", "Sorcery // Sorcery", 6.0,
             24, None, None, 0, None),
            ("b", "Colorless Grove", "normal", "Land", 0.0,
             0, 0, 32, 0, 0),
            ("c", "Arcane Relic", "normal", "Artifact", 2.5,
             0, None, None, 1, 1),
        ]
        conn.executemany(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", cards
        )
        faces = [
            ("a", 0, "Armed", "Sorcery", "{1}{R}", "Target creature...", 8,
             None, None, None, None, None, None, None),
            ("a", 1, "Dangerous", "Sorcery", "{3}{G}", "Target creature...", 16,
             None, None, None, None, None, None, None),
            ("b", 0, "Colorless Grove", "Land", "", None, 0,
             None, None, None, None, None, None, None),
            ("c", 0, "Arcane Relic", "Artifact", "{2}", None, None,
             None, None, None, None, None, None, None),
        ]
        conn.executemany(
            "INSERT INTO faces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            faces,
        )
        conn.executemany("INSERT INTO face_types VALUES (?, ?, ?, ?)", [
            ("a", 0, "type", "Sorcery"),
            ("a", 1, "type", "Sorcery"),
            ("b", 0, "type", "Land"),
            ("c", 0, "type", "Artifact"),
            ("c", 0, "supertype", "Legendary"),
            ("c", 0, "subtype", "Time Lord"),
        ])
        conn.execute("INSERT INTO card_keywords VALUES ('a', 'Fuse')")
        conn.executemany("INSERT INTO card_legalities VALUES (?, ?, ?)", [
            ("a", "modern", "legal"), ("a", "vintage", "legal"),
            ("b", "modern", "not_legal"),
        ])
        conn.executemany("INSERT INTO tags VALUES (?, ?, ?, ?)", [
            ("tag1", "ramp", "Ramp", None),
            ("tag2", "land-ramp", "Land Ramp", "Find lands"),
            ("tag3", "support", "Support", None),
        ])
        conn.executemany("INSERT INTO tag_aliases VALUES (?, ?)", [
            ("tag1", "accelerate"),
            ("tag2", "mana development"),
            ("tag3", "mana development"),
            ("tag3", "ramp"),
        ])
        conn.execute("INSERT INTO tag_relationships VALUES ('tag1', 'tag2')")
        conn.executemany("INSERT INTO card_tags VALUES (?, ?, ?, ?)", [
            ("tag2", "b", "strong", None),
            ("tag3", "c", "weak", "context"),
        ])
        conn.execute("INSERT INTO sets VALUES (?, ?, ?, ?, ?, ?)",
                     ("set1", "dgm", "Dragon's Maze", "expansion", "2013-05-03", 0))
        conn.executemany("INSERT INTO card_set_rarities VALUES (?, ?, ?, ?)", [
            ("a", "set1", "uncommon", "paper"),
            ("a", "set1", "rare", "mtgo"),
            ("b", "set1", "common", "paper"),
        ])


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "carddb.sqlite"
    make_database(path)
    return path


def test_open_missing_wrong_version_and_missing_column(db_path, tmp_path):
    with pytest.raises(DatabaseOpenError, match="not found"):
        SQLiteStore.open(tmp_path / "missing.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 2")
    with pytest.raises(DatabaseCompatibilityError, match="version"):
        SQLiteStore.open(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 1")
        conn.execute("DROP TABLE card_keywords")
    with pytest.raises(DatabaseCompatibilityError, match="card_keywords"):
        SQLiteStore.open(db_path)


def test_readonly_open_and_lifecycle(db_path):
    store = SQLiteStore.open(db_path)
    with pytest.raises(sqlite3.OperationalError):
        store._conn().execute("DELETE FROM cards")
    assert store.all_ids() == frozenset({"a", "b", "c"})
    store.close()
    store.close()
    with pytest.raises(DatabaseClosedError):
        store.all_ids()
    with pytest.raises(DatabaseClosedError):
        store.info()


def test_context_manager_closes_on_error(db_path):
    with pytest.raises(RuntimeError, match="boom"):
        with SQLiteStore.open(db_path) as store:
            raise RuntimeError("boom")
    with pytest.raises(DatabaseClosedError):
        store.load_card("a")


def test_card_resolution_preserves_unknown_and_face_order(db_path):
    with SQLiteStore.open(db_path) as store:
        card = store.load_card("a")
        assert card.oracle_id == "a"
        assert card.colors is None
        assert card.color_identity == frozenset({"R", "G"})
        assert card.game_changer is None
        assert tuple(face.name for face in card.faces) == ("Armed", "Dangerous")
        assert card.faces[0].colors == frozenset({"R"})
        assert card.faces[1].colors == frozenset({"G"})
        assert card.types == frozenset({"Sorcery"})
        assert card.keywords == frozenset({"Fuse"})
        assert store.load_card("missing") is None
        with pytest.raises(FrozenInstanceError):
            card.name = "changed"
    assert card.faces[1].name == "Dangerous"  # detached from the DB


def test_known_colorless_mana_and_three_way_flag(db_path):
    with SQLiteStore.open(db_path) as store:
        land = store.load_card("b")
        artifact = store.load_card("c")
        assert land.colors == frozenset()
        assert land.color_identity == frozenset()
        assert land.produced_mana == frozenset({"C"})
        assert land.game_changer is False
        assert artifact.colors is None
        assert artifact.game_changer is True
        assert artifact.mana_value == 2.5
        assert artifact.subtypes == frozenset({"Time Lord"})


def test_source_info_and_relationships(db_path):
    with SQLiteStore.open(db_path) as store:
        info = store.info()
        assert info.schema_version == 1
        assert info.sources[0].source_type == "default_cards"
        assert store.legalities_for("a")[0].format == "modern"
        assert store.taggings_for("b")[0].tag_slug == "land-ramp"
        assert [(r.set_code, r.rarity, r.game) for r in store.set_rarities_for("a")] == [
            ("dgm", "rare", "mtgo"), ("dgm", "uncommon", "paper"),
        ]
        assert store.set_rarities_for("c") == ()
        assert store.get_set("dgm").digital is False
        assert store.get_set("absent") is None
        with pytest.raises(KeyError):
            store.taggings_for("absent")


def test_canonical_tag_precedes_alias_and_ambiguous_alias_fails(db_path):
    with SQLiteStore.open(db_path) as store:
        ramp = store.get_tag("ramp")
        assert ramp.slug == "ramp"
        assert ramp.children == frozenset({"land-ramp"})
        assert store.get_tag("accelerate") == ramp
        assert store.get_tag("land-ramp").parents == frozenset({"ramp"})
        with pytest.raises(AmbiguousTagError):
            store.get_tag("MANA DEVELOPMENT")
        assert store.get_tag("absent") is None


def test_lazy_index_memberships_and_compound_keys(db_path):
    with SQLiteStore.open(db_path) as store:
        index = store.index_store()
        assert index.get("name", "armed // dangerous") == frozenset({"a"})
        assert index.get("type", "Sorcery") == frozenset({"a"})
        assert index.get("subtype", "Time Lord") == frozenset({"c"})
        assert index.get("identity", 24) == frozenset({"a"})
        assert index.get("card_color", 0) == frozenset({"b"})
        assert index.get("card_color", 24) == frozenset()
        assert index.get("face_color", 8) == frozenset({"a"})
        assert index.get("face_color", 16) == frozenset({"a"})
        assert index.get("game_changer", 1) == frozenset({"c"})
        assert index.get("game_changer", 0) == frozenset({"b"})
        assert index.get("tag", "land-ramp") == frozenset({"b"})
        assert index.get("legality", ("modern", "legal")) == frozenset({"a"})
        assert index.get("set_rarity", ("dgm", "uncommon", "paper")) == frozenset({"a"})
        assert index.get("set_rarity", ("dgm", "rare", "mtgo")) == frozenset({"a"})
        assert index.get("set_rarity", ("dgm", "rare", "paper")) == frozenset()


def test_unsupported_version_and_malformed_file(tmp_path):
    bogus = tmp_path / "bogus.sqlite"
    bogus.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(DatabaseCompatibilityError, match="malformed"):
        SQLiteStore.open(bogus)


def test_bad_face_order_rejected_on_load(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM face_types WHERE oracle_id = 'a'")
        conn.execute("DELETE FROM faces WHERE oracle_id = 'a' AND face_index = 0")
    with SQLiteStore.open(db_path) as store:
        with pytest.raises(ValueError, match="contiguous"):
            store.load_card("a")
