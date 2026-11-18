"""Single-card Oracle-tag membership mirrors by_tag hierarchy semantics."""

import sqlite3

import pytest

from mtg_carddb import CardDatabase
from mtg_carddb.database import UnknownTagError
from mtg_carddb.storage import AmbiguousTagError, DatabaseClosedError
from test_database import db_path


def test_has_tag_direct_ancestor_aliases_and_casefold(db_path):
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        assert db.has_tag("b", "land-ramp")
        assert db.has_tag("b", "ramp")
        assert db.has_tag("b", "RAMP")
        assert db.has_tag("b", "accelerate")  # Alias for ramp.
        assert not db.has_tag("b", "ramp", include_ancestors=False)
        assert db.has_tag("b", "land-ramp", include_ancestors=False)
        assert db.has_tag("c", "support", include_ancestors=False)
        assert not db.has_tag("c", "ramp")
        assert not db.has_tag("a", "ramp")
        assert db.has_tag("b", "support")
        # Should agree with the bulk query, for either hierarchy mode.
        for slug in ("ramp", "land-ramp", "support", "accelerate"):
            for include_hierarchy in (True, False):
                matching = db.by_tag(
                    slug, include_descendants=include_hierarchy
                )
                for card_id in ("a", "b", "c"):
                    assert db.has_tag(
                        card_id, slug, include_ancestors=include_hierarchy
                    ) == (card_id in matching)


def test_has_tag_does_not_load_global_card_or_tag_indexes(db_path):
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        assert db._ids is None
        assert not db._indexes.is_loaded("tag")
        assert db.has_tag("b", "ramp")
        assert db._ids is None
        assert not db._indexes.is_loaded("tag")


def test_has_tag_handles_cycles_and_multiple_parents(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tag_relationships VALUES ('tag2', 'tag1')")
    with CardDatabase.open(db_path, warn_if_stale=False) as db:
        # The added edges make tag2 a descendant of two parents and a
        # cycle with tag1. Traversal must terminate and deduplicate.
        assert db.has_tag("b", "ramp")
        assert db.has_tag("b", "support")
        assert not db.has_tag("b", "support", include_ancestors=False)
        assert not db.has_tag("c", "ramp")


def test_has_tag_validates_unknowns_aliases_and_closure(db_path):
    db = CardDatabase.open(db_path, warn_if_stale=False)
    with pytest.raises(UnknownTagError):
        db.has_tag("b", "missing")
    with pytest.raises(AmbiguousTagError):
        db.has_tag("b", "mana development")
    with pytest.raises(KeyError):
        db.has_tag("missing", "ramp")
    with pytest.raises(TypeError):
        db.has_tag(123, "ramp")
    with pytest.raises(TypeError):
        db.has_tag("b", None)
    with pytest.raises(TypeError):
        db.has_tag("b", "ramp", include_ancestors="yes")
    db.close()
    with pytest.raises(DatabaseClosedError):
        db.has_tag("b", "ramp")
