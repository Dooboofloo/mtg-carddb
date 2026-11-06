"""Persistence reconstructs immutable model values, not printing records."""

import sqlite3
from dataclasses import replace

import pytest

from mtg_carddb.models import Legality
from mtg_carddb.normalization import normalize_printing
from mtg_carddb.persistence import write_representative
from mtg_carddb.representatives import SelectedRepresentative, select_representative
from mtg_carddb.schema import staging_database
from mtg_carddb.storage import SQLiteStore
from test_normalization import printing, vocab


def test_insert_selected_card_and_resolve_after_commit(tmp_path, vocab):
    path = tmp_path / "stage.sqlite"
    with staging_database(path) as conn:
        selected = select_representative([normalize_printing(printing(), vocab)[0]])
        write_representative(conn, selected)
        conn.execute("INSERT INTO database_metadata VALUES (1, ?, ?)", ("2026-11-06", "0.1.0"))
        conn.commit()
    with SQLiteStore.open(path) as storage:
        card = storage.load_card("oracle-1")
        assert card == selected.card
        assert storage.legalities_for("oracle-1") == selected.legalities
        assert card.faces[0].subtypes == frozenset({"Time Lord", "Doctor"})
        assert card.produced_mana == frozenset({"C"})
        assert card.game_changer is False


def test_insert_rolls_back_partially_written_card(tmp_path, vocab):
    with staging_database(tmp_path / "stage.sqlite") as conn:
        selected = select_representative([normalize_printing(printing(), vocab)[0]])
        broken = replace(selected, legalities=(
            Legality("modern", "legal"), Legality("modern", "banned"),
        ))
        with pytest.raises(sqlite3.IntegrityError):
            write_representative(conn, broken)
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM face_types").fetchone()[0] == 0
        # A clean write can still succeed without resetting the connection.
        write_representative(conn, selected)
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1


def test_repeated_write_is_not_a_silent_overwrite(tmp_path, vocab):
    with staging_database(tmp_path / "stage.sqlite") as conn:
        selected = select_representative([normalize_printing(printing(), vocab)[0]])
        write_representative(conn, selected)
        with pytest.raises(sqlite3.IntegrityError):
            write_representative(conn, selected)
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1
