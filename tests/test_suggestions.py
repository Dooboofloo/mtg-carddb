"""Opt-in fuzzy suggestions must not change exact lookups or cold starts."""

import sqlite3

import pytest

from mtg_carddb import CardDatabase
from mtg_carddb.storage import DatabaseClosedError
from test_database import db_path


def test_suggest_names_uses_card_and_face_names_without_loading_indexes(db_path):
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        assert db._suggestion_names is None
        assert db.by_name("Armd") == frozenset()
        assert db._suggestion_names is None
        assert db.suggest_names("Armd") == ("Armed",)
        assert db.suggest_names("Arcan Relic")[0] == "Arcane Relic"
        assert db.suggest_names("ARMED---DANGEROUS")[0] == "Armed // Dangerous"
        assert db.by_name("Armd") == frozenset()  # No silent corrections.
        assert db._ids is None
        assert not db._indexes.is_loaded("name")


def test_suggest_names_is_ordered_repeatable_and_bounded(db_path):
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        suggestions = db.suggest_names("Dangerous", limit=2)
        assert suggestions[0] == "Dangerous"
        assert len(suggestions) <= 2
        assert db.suggest_names("Dangerous", limit=2) == suggestions
        assert db.suggest_names("Dangerous", limit=1) == ("Dangerous",)
        assert db.suggest_names("fffffff") == ()
        assert db.suggest_names("\t--- \t") == ()
        assert db.suggest_names("Armd", cutoff=1.0) == ()
        assert db.suggest_names("Armed", cutoff=1.0) == ("Armed",)


def test_suggest_names_normalizes_unicode_and_punctuation(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("unicode", "Maße, Scholar", "normal", "Creature", 2, 0, 0, None, 0, None),
        )
        conn.execute(
            "INSERT INTO faces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("unicode", 0, "Maße, Scholar", "Creature", None, None,
             0, None, None, None, None, None, None, None),
        )
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        assert db.suggest_names("MASSE -- SCHOLAR")[0] == "Maße, Scholar"
        assert db.by_name("MASSE, SCHOLAR") == frozenset({"unicode"})


def test_suggest_names_deduplicates_card_and_face_names(db_path):
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        results = db.suggest_names("colorless grove", cutoff=1.0)
        assert results == ("Colorless Grove",)


def test_suggest_names_validates_arguments_and_closed_handle(db_path):
    db = CardDatabase.open(db_path, warn_if_stale=False)
    for invalid in (0, -1):
        with pytest.raises(ValueError):
            db.suggest_names("Armed", limit=invalid)
    for invalid in (True, 3.2, "2", None):
        with pytest.raises(TypeError):
            db.suggest_names("Armed", limit=invalid)
    for invalid in (-0.1, 1.1, float("nan"), float("inf"), 10**1000):
        with pytest.raises(ValueError):
            db.suggest_names("Armed", cutoff=invalid)
    for invalid in (None, True, "0.6"):
        with pytest.raises(TypeError):
            db.suggest_names("Armed", cutoff=invalid)
    with pytest.raises(TypeError):
        db.suggest_names(None)
    db.close()
    with pytest.raises(DatabaseClosedError):
        db.suggest_names("Armed")


def test_suggest_names_uses_a_lazy_per_handle_catalog(db_path, monkeypatch):
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        original = db._store.all_names
        calls = []
        def counted():
            calls.append(1)
            return original()
        monkeypatch.setattr(db._store, "all_names", counted)
        db.by_name("Armed")
        assert calls == []
        db.suggest_names("Armd")
        db.suggest_names("Arcan Relic")
        assert len(calls) == 1
        db.preload_names()
        db.suggest_names("Armd")
        assert len(calls) == 1
    with CardDatabase.open(db_path, warn_if_stale=False) as reopened:
        assert reopened._suggestion_names is None
