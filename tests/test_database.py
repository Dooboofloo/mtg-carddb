"""Public API contract tests against deterministic synthetic SQLite facts."""

import sqlite3
from pathlib import Path

import pytest

from test_storage import make_database
from mtg_carddb.database import (
    CardDatabase, UnknownFormatError, UnknownSetError, UnknownTagError,
)
from mtg_carddb.storage import (
    AmbiguousTagError, DatabaseClosedError, DatabaseOpenError,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "carddb.sqlite"
    make_database(path)

    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("d", "Unknown Card", "normal", "Creature",
                 1, 2, None, None, 0, None),
                ("e", "Partial Faces", "transform", "Creature // Land",
                 2, 24, None, None, 0, None),
                ("f", "Whole Color Wins", "transform", "Creature // Creature",
                 2, 24, 24, None, 0, None),
                ("g", "White Spell", "normal", "Instant",
                 1, 1, 1, 3, 0, None),
            ],
        )
        conn.executemany(
            "INSERT INTO faces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("d", 0, "Unknown Card", "Creature",
                 None, None, None, None, None, None, None, None, None, None),
                ("e", 0, "Partial One", "Creature",
                 None, None, 8, None, None, None, None, None, None, None),
                ("e", 1, "Partial Two", "Land",
                 None, None, None, None, None, None, None, None, None, None),
                ("f", 0, "Color Winner", "Creature",
                 None, None, 8, None, None, None, None, None, None, None),
                ("f", 1, "Other Side", "Creature",
                 None, None, 2, None, None, None, None, None, None, None),
                ("g", 0, "White Spell", "Instant",
                 None, None, 1, None, None, None, None, None, None, None),
            ],
        )
        conn.executemany(
            "INSERT INTO face_types VALUES (?, ?, ?, ?)",
            [
                ("d", 0, "type", "Creature"),
                ("e", 0, "type", "Creature"),
                ("e", 1, "type", "Land"),
                ("f", 0, "type", "Creature"),
                ("f", 1, "type", "Creature"),
                ("g", 0, "type", "Instant"),
            ],
        )
        conn.execute(
            "INSERT INTO tag_relationships VALUES ('tag3', 'tag2')"
        )
        conn.execute(
            "INSERT INTO sets VALUES (?, ?, ?, ?, ?, ?)",
            ("set2", "m21", "Core Set 2021",
             "core", "2020-07-03", 0),
        )
        conn.executemany(
            "INSERT INTO card_set_rarities VALUES (?, ?, ?, ?)",
            [
                ("a", "set2", "rare", "paper"),
                ("b", "set2", "rare", "mtgo"),
                ("g", "set2", "uncommon", "paper"),
            ],
        )
    return path


def test_lifecycle_strict_optional_resolution(db_path):
    with CardDatabase.open(db_path) as db:
        assert len(db) == 7
        assert "a" in db and 123 not in db
        assert db["a"].name == "Armed // Dangerous"
        assert db.get("missing") is None

        with pytest.raises(KeyError):
            db["missing"]
        with pytest.raises(KeyError):
            db.resolve(["a", "missing"])

        resolved = db.resolve(["g", "b", "a", "b"])
        assert [c.oracle_id for c in resolved] == ["a", "b", "g"]
        assert db.resolve([]) == ()
        assert db.info().schema_version == 1

        all_ids = db.all_cards()
        card = db["a"]

    assert card.faces[1].name == "Dangerous"
    assert len(all_ids) == 7

    operations = (
        lambda: len(db),
        db.all_cards,
        lambda: db.by_type("Creature"),
        lambda: db["a"],
        lambda: db.types,
        lambda: db.get_tag("ramp"),
    )
    for operation in operations:
        with pytest.raises(DatabaseClosedError):
            operation()
    db.close()


def test_open_missing_file(tmp_path):
    with pytest.raises(DatabaseOpenError):
        CardDatabase.open(tmp_path / "missing.sqlite")


def test_names_layouts_types_keywords_are_case_insensitive(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.by_name("armed // dangerous") == frozenset({"a"})
        assert db.by_name("aRmEd") == frozenset({"a"})
        assert db.by_name("dangerous") == frozenset({"a"})
        assert db.by_name("PARTIAL TWO") == frozenset({"e"})
        assert db.by_name("none") == frozenset()
        assert db.by_layout("SPLIT") == frozenset({"a"})
        assert db.by_type("land") == frozenset({"b", "e"})
        assert db.by_supertype("LEGENDARY") == frozenset({"c"})
        assert db.by_subtype("time lord") == frozenset({"c"})
        assert db.by_keyword("fUSE") == frozenset({"a"})
        assert db.game_changers() == frozenset({"c"})
        assert db.by_type("missing") == frozenset()


def test_color_identity_with_colorless_aliases(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.exact_identity("GRC") == frozenset({"a", "e", "f"})
        assert db.exact_identity("c") == frozenset({"b", "c"})
        assert db.exact_identity("u") == frozenset({"d"})
        assert db.within_identity("C") == frozenset({"b", "c"})
        assert db.within_identity("rG") == frozenset(
            {"a", "b", "c", "e", "f"}
        )
        assert db.containing_identity("C") == db.all_cards()
        assert db.containing_identity("R") == frozenset({"a", "e", "f"})

        with pytest.raises(ValueError):
            db.within_identity("X")
        with pytest.raises(TypeError):
            db.exact_identity(8)


def test_aggregate_colors_precedence_and_unknowns(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.by_color("RG") == frozenset({"a", "f"})
        assert db.by_color("R", match="exact") == frozenset()
        assert db.by_color("RG", match="exact") == frozenset({"a", "f"})
        assert db.by_color("R", match="within") == frozenset({"b"})
        assert db.by_color("C") == frozenset({"b"})
        assert db.by_color("cC") == frozenset({"b"})
        assert db.by_color("", match="contains") == frozenset(
            {"a", "b", "f", "g"}
        )
        assert db.by_color("C", match="within") == frozenset({"b"})
        assert db.by_color("CRG") == frozenset({"a", "f"})

        # One unknown face blocks fallback.
        assert "e" not in db.by_color("R")

        # A missing root and missing face aren't colorless.
        assert "c" not in db.by_color("C")

        assert db["f"].faces[1].colors == frozenset({"U"})
        assert db["f"].colors == frozenset({"R", "G"})

        with pytest.raises(ValueError):
            db.by_color("RG", match="approx")
        with pytest.raises(TypeError):
            db.by_color("RG", match=1)


def test_mana_values_and_production(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.by_mana_value(2.5) == frozenset({"c"})
        assert db.by_mana_value(0) == frozenset({"b"})
        assert db.mana_value_between(1, 2.5) == frozenset(
            {"c", "d", "e", "f", "g"}
        )
        assert db.produces_mana("c") == frozenset({"b"})
        assert db.produces_mana("W") == frozenset({"g"})
        assert db.produces_mana("u") == frozenset({"g"})
        assert db.produces_mana("G") == frozenset()

        for invalid in (True, float("nan"), float("inf"), -1):
            with pytest.raises((TypeError, ValueError)):
                db.by_mana_value(invalid)

        with pytest.raises(ValueError):
            db.mana_value_between(5, 2)
        with pytest.raises(ValueError):
            db.produces_mana("WU")


def test_tag_aliases_descendants_multiparent_and_direct(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.by_tag("land-ramp") == frozenset({"b"})
        assert db.by_tag("accelerate") == frozenset({"b"})
        assert db.by_tag(
            "ramp", include_descendants=False
        ) == frozenset()
        assert db.by_tag("ramp") == frozenset({"b"})
        assert db.by_tag("SUPPORT") == frozenset({"b", "c"})
        assert db.by_tag(
            "support", include_descendants=False
        ) == frozenset({"c"})
        assert db.get_tag("RAMP").slug == "ramp"

        with pytest.raises(UnknownTagError):
            db.by_tag("missing")
        with pytest.raises(AmbiguousTagError):
            db.by_tag("mana development")
        with pytest.raises(TypeError):
            db.by_tag("support", include_descendants="yes")


def test_legality_validation_and_exact_status(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.by_legality("MODERN", "LEGAL") == frozenset({"a"})
        assert db.by_legality("modern", "restricted") == frozenset()
        assert db.by_legality(
            "modern", "not_legal"
        ) == frozenset({"b"})

        with pytest.raises(UnknownFormatError):
            db.by_legality("no-format", "legal")
        with pytest.raises(ValueError):
            db.by_legality("modern", "no-status")


def test_set_rarity_game_coupling_and_defaults(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.by_set("DGM") == frozenset({"a", "b"})
        assert db.by_set("dgm", game="MTGO") == frozenset({"a"})
        assert db.by_set("dgm", game=None) == frozenset({"a", "b"})
        assert db.by_rarity("COMMON") == frozenset({"b"})
        assert db.by_rarity("common", game="mtgo") == frozenset()
        assert db.by_set_rarity("dgm", "rare") == frozenset()
        assert db.by_set_rarity(
            "dgm", "rare", game="mtgo"
        ) == frozenset({"a"})
        assert db.by_set_rarity(
            "dgm", "rare", game=None
        ) == frozenset({"a"})
        assert db.by_set_rarity("m21", "rare") == frozenset({"a"})
        assert db.by_rarity("rare") == frozenset({"a"})
        assert db.by_rarity(
            "rare", game=None
        ) == frozenset({"a", "b"})

        # A card can be common elsewhere but not common in this set.
        assert (
            db.by_set("m21", game=None)
            & db.by_rarity("common", game=None)
        ) == frozenset({"b"})
        assert db.by_set_rarity(
            "m21", "common", game=None
        ) == frozenset()

        assert db.get_set("DGM").code == "dgm"

        with pytest.raises(UnknownSetError):
            db.by_set("nonexistent")
        with pytest.raises(ValueError):
            db.by_set("dgm", game="not-a-platform")
        with pytest.raises(ValueError):
            db.by_rarity("legendary")


def test_relationships_and_catalogs(db_path):
    with CardDatabase.open(db_path) as db:
        assert db.legalities_for("a")[0].format == "modern"
        assert db.taggings_for("b")[0].tag_slug == "land-ramp"

        assert {
            (r.set_code, r.rarity, r.game)
            for r in db.set_rarities_for("a")
        } == {
            ("dgm", "uncommon", "paper"),
            ("dgm", "rare", "mtgo"),
            ("m21", "rare", "paper"),
        }

        with pytest.raises(KeyError):
            db.set_rarities_for("missing")

        assert db.types == frozenset({
            "Sorcery", "Land", "Artifact", "Creature", "Instant"
        })
        assert db.supertypes == frozenset({"Legendary"})
        assert db.subtypes == frozenset({"Time Lord"})
        assert db.keywords == frozenset({"Fuse"})
        assert db.formats == frozenset({"modern", "vintage"})
        assert db.set_codes == frozenset({"dgm", "m21"})
        assert db.layouts == frozenset({
            "normal", "split", "transform"
        })
        assert db.tag_slugs == frozenset({
            "ramp", "land-ramp", "support"
        })
        assert db.rarities == frozenset({
            "rare", "common", "uncommon"
        })
        assert db.games == frozenset({"paper", "mtgo"})


def test_set_result_set_algebra(db_path):
    with CardDatabase.open(db_path) as db:
        ids = (
            db.by_type("Creature")
            & db.within_identity("RGU")
        )
        assert ids == frozenset({"d", "e", "f"})

        assert (
            db.all_cards() - db.game_changers()
        ) == frozenset({"a", "b", "d", "e", "f", "g"})

        assert type(db.by_tag("ramp")) is frozenset


def test_closed_handle_cannot_reuse_cached_index(db_path):
    db = CardDatabase.open(db_path)
    db.by_type("Creature")
    db.by_tag("ramp")
    db.close()

    with pytest.raises(DatabaseClosedError):
        db.by_type("Creature")
    with pytest.raises(DatabaseClosedError):
        db.by_tag("ramp")
    with pytest.raises(DatabaseClosedError):
        db.games


def test_all_32_identity_masks_match_reference_filter(db_path):
    symbols = ("W", "U", "B", "R", "G")

    with CardDatabase.open(db_path) as db:
        cards = tuple(db.resolve(db.all_cards()))

        for requested in range(32):
            query = "".join(
                symbol
                for bit, symbol in enumerate(symbols)
                if requested & (1 << bit)
            )
            wanted = frozenset(query)

            assert db.exact_identity(query) == frozenset(
                card.oracle_id
                for card in cards
                if card.color_identity == wanted
            )
            assert db.within_identity(query) == frozenset(
                card.oracle_id
                for card in cards
                if card.color_identity <= wanted
            )
            assert db.containing_identity(query) == frozenset(
                card.oracle_id
                for card in cards
                if card.color_identity >= wanted
            )


def test_aggregate_color_matches_reference_predicate(db_path):
    symbols = ("W", "U", "B", "R", "G")

    with CardDatabase.open(db_path) as db:
        aggregates = {}

        for card in db.resolve(db.all_cards()):
            aggregate = card.colors

            if (
                aggregate is None
                and all(
                    face.colors is not None
                    for face in card.faces
                )
            ):
                aggregate = frozenset().union(*(
                    face.colors for face in card.faces
                ))

            if aggregate is not None:
                aggregates[card.oracle_id] = aggregate

        for requested in range(32):
            query = "".join(
                symbol
                for bit, symbol in enumerate(symbols)
                if requested & (1 << bit)
            )
            wanted = frozenset(query)

            for mode in ("contains", "exact", "within"):
                if mode == "contains":
                    predicate = (
                        lambda aggregate: aggregate >= wanted
                    )
                elif mode == "within":
                    predicate = (
                        lambda aggregate: aggregate <= wanted
                    )
                else:
                    predicate = (
                        lambda aggregate: aggregate == wanted
                    )

                expected = frozenset(
                    oracle_id
                    for oracle_id, aggregate in aggregates.items()
                    if predicate(aggregate)
                )
                assert db.by_color(
                    query, match=mode
                ) == expected


def test_open_missing_database_suggests_initialization(
    tmp_path,
):
    from mtg_carddb.database import CardDatabase
    from mtg_carddb.freshness import DatabaseNotInitializedError
    from mtg_carddb.storage import DatabaseOpenError

    target = tmp_path / "missing-carddb.sqlite"

    with pytest.raises(DatabaseNotInitializedError) as exc_info:
        CardDatabase.open(target)

    assert isinstance(exc_info.value, DatabaseOpenError)
    assert isinstance(exc_info.value, FileNotFoundError)
    assert "init" in str(exc_info.value)
    assert not target.exists()
