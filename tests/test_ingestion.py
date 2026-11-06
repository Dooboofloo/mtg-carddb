"""Import normalization candidates through a temporary SQLite spool."""

import sqlite3

import pytest

from mtg_carddb.database import CardDatabase
from mtg_carddb.ingestion import IngestionError, ingest_printings
from mtg_carddb.normalization import NormalizationError
from mtg_carddb.representatives import RepresentativeConflictError
from mtg_carddb.schema import staging_database
from test_normalization import printing, vocab


@pytest.fixture
def conn(tmp_path):
    with staging_database(tmp_path / "stage.sqlite") as database:
        database.executemany(
            "INSERT INTO sets VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("sid1", "dgm", "Dragon's Maze", "expansion", "2013-05-03", 0),
                ("sid2", "m21", "Core Set 2021", "core", "2020-07-03", 0),
            ],
        )
        yield database


def source(**updates):
    raw = printing(
        id="p1", set_id="sid1", set="dgm", rarity="uncommon",
        games=["paper"],
    )
    raw.update(updates)
    return raw


def test_one_pass_import_and_carddatabase_integration(conn, vocab):
    first = source(id="first", released_at="2015-01-01", games=["paper", "mtgo", "paper"])
    newer = source(id="newer", released_at="2025-01-01", set_id="sid2", set="m21", rarity="rare")
    land = source(
        id="land", oracle_id="land-oracle", name="Colorless Grove",
        type_line="Land", cmc=0, colors=[], color_identity=[],
        produced_mana=["C"], reserved=False, keywords=[],
        game_changer=None, mana_cost="", oracle_text="{T}: Add {C}.",
        set_id="sid1", set="dgm", rarity="common",
    )
    stats = ingest_printings(conn, iter([first, newer, land]), vocab)
    assert (stats.printings, stats.candidates, stats.oracle_cards, stats.historical_observations) == (3, 3, 2, 4)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM card_set_rarities").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM face_types").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(*) FROM card_keywords").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM card_legalities").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'carddb_%'").fetchall() == []

    conn.execute("INSERT INTO database_metadata VALUES (1, ?, ?)", ("2026-11-06", "0.1.0"))
    conn.commit()
    with CardDatabase.open(conn.execute("PRAGMA database_list").fetchone()[2]) as db:
        assert db.by_name("Test Doctor") == frozenset({"oracle-1"})
        assert db["oracle-1"].mana_value == 2.5
        assert db["land-oracle"].colors == frozenset()
        assert db["land-oracle"].game_changer is None
        assert db.by_color("C") == frozenset({"land-oracle"})
        assert db.by_set_rarity("dgm", "uncommon") == frozenset({"oracle-1"})
        assert db.by_set_rarity("m21", "rare") == frozenset({"oracle-1"})
        assert db.by_set("dgm", game="mtgo") == frozenset({"oracle-1"})
        assert db.by_set_rarity("dgm", "uncommon", game="mtgo") == frozenset({"oracle-1"})


def test_history_preserves_set_rarity_game_pairing(conn, vocab):
    a = source(id="a", set="dgm", set_id="sid1", rarity="common", games=["paper"])
    b = source(id="b", set="m21", set_id="sid2", rarity="rare", games=["paper", "mtgo"])
    ingest_printings(conn, [a, b], vocab)
    assert conn.execute(
        "SELECT s.code, h.rarity, h.game FROM card_set_rarities h "
        "JOIN sets s ON s.set_id = h.set_id ORDER BY 1,2,3"
    ).fetchall() == [
        ("dgm", "common", "paper"),
        ("m21", "rare", "mtgo"),
        ("m21", "rare", "paper"),
    ]


def test_split_card_has_two_faces_and_one_oracle_id(conn, vocab):
    split = source(
        layout="split", name="Armed // Dangerous", type_line="Sorcery // Sorcery",
        cmc=6, colors=None, color_identity=["R", "G"],
        card_faces=[
            {"name": "Armed", "type_line": "Sorcery", "colors": ["R"], "mana_cost": "{1}{R}"},
            {"name": "Dangerous", "type_line": "Sorcery", "colors": ["G"], "mana_cost": "{3}{G}"},
        ],
    )
    assert ingest_printings(conn, [split], vocab).oracle_cards == 1
    assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM face_types").fetchone()[0] == 2
    assert conn.execute("SELECT color_identity, colors FROM cards").fetchone() == (24, None)


def test_reversible_printing_history_applies_to_both_oracle_ids(conn, vocab):
    def land(oracle_id, name, printing_id):
        return source(
            id=printing_id, oracle_id=oracle_id, name=name,
            layout="normal", type_line="Land", cmc=0,
            colors=[], color_identity=[], keywords=[], reserved=False,
            game_changer=False, mana_cost="", oracle_text="",
        )

    reverse = source(
        id="reverse", layout="reversible_card", name="Side A // Side B",
        set_id="sid2", set="m21", rarity="rare", games=["paper", "mtgo"],
        card_faces=[
            {"oracle_id": "side-a", "name": "Side A", "layout": "normal",
             "type_line": "Land", "cmc": 0, "colors": [], "mana_cost": "", "oracle_text": ""},
            {"oracle_id": "side-b", "name": "Side B", "layout": "normal",
             "type_line": "Land", "cmc": 0, "colors": [], "mana_cost": "", "oracle_text": ""},
        ],
    )
    del reverse["oracle_id"]
    stats = ingest_printings(conn, [reverse, land("side-a", "Side A", "a"), land("side-b", "Side B", "b")], vocab)
    assert stats.candidates == 4
    assert stats.oracle_cards == 2
    assert stats.historical_observations == 6
    assert conn.execute("SELECT name FROM cards ORDER BY oracle_id").fetchall() == [
        ("Side A",), ("Side B",),
    ]
    assert conn.execute("SELECT COUNT(*) FROM card_set_rarities WHERE rarity = 'rare'").fetchone()[0] == 4


def test_bad_set_metadata_is_an_atomic_failure(conn, vocab):
    with pytest.raises(IngestionError, match="authoritative catalog"):
        ingest_printings(conn, [source(set_id="sid1", set="wrong")], vocab)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM card_set_rarities").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'carddb_%'").fetchall() == []
    # Retains existing set metadata from the caller's outer transaction.
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 2


def test_missing_games_or_set_or_rarity_is_error(conn, vocab):
    for changes in ({"games": []}, {"games": "paper"}, {"set": ""}, {"set_id": None}, {"rarity": None}):
        with pytest.raises(IngestionError):
            ingest_printings(conn, [source(**changes)], vocab)
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_stream_failure_rolls_back_everything(conn, vocab):
    def printings():
        yield source(id="first")
        yield source(id="broken", type_line="Thing")

    with pytest.raises(NormalizationError):
        ingest_printings(conn, printings(), vocab)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM card_set_rarities").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'carddb_%'").fetchall() == []


def test_conflicting_mechanics_roll_back_everything(conn, vocab):
    with pytest.raises(RepresentativeConflictError, match="color_identity"):
        ingest_printings(conn, [source(id="one"), source(id="two", color_identity=["U"])], vocab)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_duplicate_printing_id_is_rejected(conn, vocab):
    with pytest.raises(sqlite3.IntegrityError):
        ingest_printings(conn, [source(), source()], vocab)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_reversible_only_incomplete_is_not_silently_dropped(conn, vocab):
    reverse = source(
        layout="reversible_card", card_faces=[
            {"oracle_id": "a", "name": "A", "layout": "normal", "type_line": "Land", "cmc": 0},
            {"oracle_id": "b", "name": "B", "layout": "normal", "type_line": "Land", "cmc": 0},
        ],
    )
    del reverse["oracle_id"]
    with pytest.raises(NormalizationError, match="no complete mechanics candidate"):
        ingest_printings(conn, [reverse], vocab)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_empty_export_rejected(conn, vocab):
    with pytest.raises(IngestionError, match="empty"):
        ingest_printings(conn, iter([]), vocab)


def test_larger_generator_bounded_by_one_oracle_group(conn, vocab):
    def printings():
        for i in range(600):
            yield source(id=f"p{i:04d}", games=["paper", "mtgo"])

    stats = ingest_printings(conn, printings(), vocab)
    assert stats.printings == 600
    assert stats.oracle_cards == 1
    assert stats.historical_observations == 2
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1


def test_no_implicit_commit_of_callers_transaction(conn, vocab):
    # The sets fixture was inserted in an active caller-owned transaction.
    assert conn.in_transaction
    ingest_printings(conn, [source()], vocab)
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM card_set_rarities").fetchone()[0] == 0


def test_second_oracle_failure_rolls_back_first_oracle(conn, vocab):
    normal = source(id="a", oracle_id="a-healthy")
    broken = source(id="b", oracle_id="z-broken")
    del broken["reserved"]
    with pytest.raises(NormalizationError, match="no complete mechanics candidate"):
        ingest_printings(conn, [normal, broken], vocab)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0


def test_foreign_keys_must_be_enabled(conn, vocab):
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    with pytest.raises(IngestionError, match="foreign key"):
        ingest_printings(conn, [source()], vocab)
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_accepts_existing_gzip_bulk_iterator(conn, vocab, tmp_path):
    import gzip
    import json

    from mtg_carddb.bulk import iter_default_cards

    path = tmp_path / "default_cards.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as file:
        for item in (source(id="a"), source(id="b", set="m21", set_id="sid2")):
            file.write(json.dumps(item) + "\n")
    stats = ingest_printings(conn, iter_default_cards(path), vocab)
    assert stats.printings == 2
    assert stats.oracle_cards == 1
    assert stats.historical_observations == 2
