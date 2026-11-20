"""Representative printing images, layout-aware extraction, and v1 compatibility."""
from dataclasses import FrozenInstanceError
import sqlite3

import pytest

from mtg_carddb import CardDatabase, CardImages
from mtg_carddb.normalization import normalize_printing
from mtg_carddb.representatives import select_representative
from mtg_carddb.persistence import write_representative
from mtg_carddb.schema import staging_database
from mtg_carddb.storage import DatabaseClosedError, DatabaseCompatibilityError, SQLiteStore
from test_normalization import printing, vocab
from test_storage import make_database

FRONT = 'https://cards.scryfall.io/normal/front.jpg'
BACK = 'https://cards.scryfall.io/normal/back.jpg'
OTHER = 'https://cards.scryfall.io/normal/other.jpg'


def test_single_sided_image_from_root_and_missing(vocab):
    candidate, = normalize_printing(printing(image_uris={'normal': FRONT}), vocab)
    assert candidate.image_front == FRONT
    assert candidate.image_back is None
    missing, = normalize_printing(printing(), vocab)
    assert (missing.image_front, missing.image_back) == (None, None)


def test_split_is_single_sided_not_double_sided(vocab):
    candidate, = normalize_printing(printing(
        name='Armed // Dangerous', layout='split',
        type_line='Sorcery // Sorcery',
        cmc=6,
        image_uris={'normal': FRONT},
        card_faces=[
            {'name': 'Armed', 'type_line': 'Sorcery', 'mana_cost': '{1}{R}'},
            {'name': 'Dangerous', 'type_line': 'Sorcery', 'mana_cost': '{3}{G}'},
        ],
    ), vocab)
    assert (candidate.image_front, candidate.image_back) == (FRONT, None)


def test_double_sided_images_from_faces(vocab):
    candidate, = normalize_printing(printing(
        name='Front // Back', layout='transform',
        type_line='Creature // Creature',
        card_faces=[
            {'name': 'Front', 'type_line': 'Creature', 'cmc': 1, 'image_uris': {'normal': FRONT}},
            {'name': 'Back', 'type_line': 'Creature', 'cmc': 1, 'image_uris': {'normal': BACK}},
        ],
    ), vocab)
    assert (candidate.image_front, candidate.image_back) == (FRONT, BACK)


def test_reversible_image_is_oriented_to_its_own_oracle_identity(vocab):
    source = printing(
        layout='reversible_card',
        card_faces=[
            {'oracle_id': 'id-front', 'layout': 'normal', 'name': 'Front',
             'type_line': 'Creature', 'cmc': 1, 'image_uris': {'normal': FRONT}},
            {'oracle_id': 'id-back', 'layout': 'normal', 'name': 'Back',
             'type_line': 'Creature', 'cmc': 1, 'image_uris': {'normal': BACK}},
        ],
    )
    del source['oracle_id']
    candidates = normalize_printing(source, vocab)
    assert [(c.oracle_id, c.image_front, c.image_back) for c in candidates] == [
        ('id-front', FRONT, BACK), ('id-back', BACK, FRONT),
    ]


def test_same_oracle_id_reversible_images_do_not_conflict(vocab):
    # A printing can place the same Oracle card on both physical sides,
    # with different artwork but unchanged mechanical characteristics.
    face = {
        'oracle_id': 'same-id',
        'layout': 'normal',
        'name': 'Same Card',
        'type_line': 'Creature — Human',
        'cmc': 2,
        'colors': ['U'],
        'color_identity': ['U'],
        'keywords': [],
        'reserved': False,
        'legalities': {'commander': 'legal'},
    }
    source = printing(
        layout='reversible_card',
        card_faces=[
            {**face, 'image_uris': {'normal': FRONT}},
            {**face, 'image_uris': {'normal': BACK}},
        ],
    )
    del source['oracle_id']
    candidates = normalize_printing(source, vocab)
    assert len(candidates) == 1
    candidate, = candidates
    assert candidate.oracle_id == 'same-id'
    assert (candidate.image_front, candidate.image_back) == (FRONT, BACK)
    selected = select_representative(candidates)
    assert selected.images == CardImages(FRONT, BACK)


def test_image_selection_prefers_available_english_and_is_order_invariant(vocab):
    no_image, = normalize_printing(printing(
        id='mechanics', released_at='2026-01-01',
    ), vocab)
    older, = normalize_printing(printing(
        id='images-english', released_at='2020-01-01',
        image_uris={'normal': FRONT},
    ), vocab)
    newest_nonenglish, = normalize_printing(printing(
        id='images-foreign', released_at='2025-01-01', lang='fr',
        image_uris={'normal': OTHER},
    ), vocab)
    for group in ((no_image, older, newest_nonenglish), (newest_nonenglish, older, no_image)):
        selected = select_representative(group)
        assert selected.mechanics_printing_id == 'mechanics'
        assert selected.image_printing_id == 'images-english'
        assert selected.images == CardImages(FRONT, None)


def test_optional_image_table_persists_images_and_public_api_is_readonly(tmp_path, vocab):
    path = tmp_path / 'new.sqlite'
    with staging_database(path) as conn:
        candidate, = normalize_printing(printing(image_uris={'normal': FRONT}), vocab)
        selected = select_representative((candidate,))
        write_representative(conn, selected)
        conn.execute("INSERT INTO database_metadata VALUES (1, 'today', 'test')")
    with CardDatabase.open(path, warn_if_stale=False) as db:
        assert db.info().schema_version == 1
        assert db.images_for('oracle-1') == CardImages(FRONT, None)
        assert db.images_for('missing') is None
        with pytest.raises(TypeError):
            db.images_for(None)
        with pytest.raises(FrozenInstanceError):
            db.images_for('oracle-1').front = OTHER
    with pytest.raises(DatabaseClosedError):
        db.images_for('oracle-1')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT printing_id FROM card_images').fetchone()[0] == 'printing-1'


def test_schema1_without_image_table_still_opens(tmp_path):
    path = tmp_path / 'old.sqlite'
    make_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TABLE card_images')
    with CardDatabase.open(path, warn_if_stale=False) as db:
        assert db.info().schema_version == 1
        assert db.images_for('a') == CardImages(None, None)
        assert db.images_for('missing') is None
        assert db.by_name('Armed') == frozenset({'a'})


def test_partial_image_table_is_not_compatible(tmp_path):
    path = tmp_path / 'broken.sqlite'
    make_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TABLE card_images')
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE card_images (oracle_id TEXT PRIMARY KEY)')
    with pytest.raises(DatabaseCompatibilityError, match='card_images'):
        SQLiteStore.open(path)


def test_image_selection_prefers_regular_innistrad_remastered_over_newer_secret_lair(vocab):
    inr, = normalize_printing(printing(
        id='inr-delver', released_at='2025-01-24',
        set='inr', set_type='masters', promo=False,
        games=['paper'], image_uris={'normal': FRONT},
    ), vocab)
    sld, = normalize_printing(printing(
        id='sld-tmnt', released_at='2026-03-02',
        set='sld', set_type='box', promo=True,
        games=['paper'], image_uris={'normal': OTHER},
    ), vocab)
    for group in ((inr, sld), (sld, inr)):
        selected = select_representative(group)
        assert selected.image_printing_id == 'inr-delver'
        assert selected.images.front == FRONT
    # If the only available artwork is from a Secret Lair, use it.
    assert select_representative((sld,)).image_printing_id == 'sld-tmnt'


def test_image_selection_paper_and_regular_treatment_priority(vocab):
    digital, = normalize_printing(printing(
        id='digital', released_at='2027-01-01', games=['arena'],
        image_uris={'normal': OTHER},
    ), vocab)
    showcase, = normalize_printing(printing(
        id='showcase', released_at='2026-01-01', games=['paper'],
        frame_effects=['showcase'], image_uris={'normal': BACK},
    ), vocab)
    normal, = normalize_printing(printing(
        id='normal', released_at='2024-01-01', games=['paper'],
        image_uris={'normal': FRONT},
    ), vocab)
    assert select_representative((digital, showcase, normal)).image_printing_id == 'normal'
