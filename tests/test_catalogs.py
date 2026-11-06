"""Offline reference-data acquisition and ingestion integration tests."""

import pytest

from mtg_carddb.catalogs import (
    CATALOG_BASE,
    SETS_URL,
    TYPE_CATALOGS,
    CatalogError,
    SetRecord,
    fetch_reference_data,
    make_type_vocabulary,
    parse_set_pages,
    parse_type_catalog,
    write_sets,
)
from mtg_carddb.database import CardDatabase
from mtg_carddb.ingestion import ingest_printings
from mtg_carddb.schema import staging_database
from test_normalization import printing


def make_set(code="m21", set_id="uuid-1", **overrides):
    item = dict(
        object="set",
        id=set_id,
        code=code,
        name="Core Set 2021",
        set_type="core",
        released_at="2020-07-03",
        digital=False,
    )
    item.update(overrides)
    return item


def list_page(items, *, more=False, next_page=None):
    page = dict(object="list", has_more=more, data=items)
    if next_page is not None:
        page["next_page"] = next_page
    return page


def catalog(values):
    return dict(
        object="catalog",
        data=list(values),
        total_values=len(values),
    )


def sample_catalogs():
    return {
        "supertypes": catalog([
            "Basic", "Legendary", "Snow",
        ]),
        "artifact-types": catalog(["Equipment"]),
        "creature-types": catalog([
            "Human", "Time", "Lord", "Time Lord", "Doctor",
        ]),
        "enchantment-types": catalog(["Aura"]),
        "land-types": catalog(["Forest", "Swamp"]),
        "planeswalker-types": catalog(["Jace"]),
        "spell-types": catalog(["Arcane", "Lesson"]),
    }


def test_parse_complete_sets_and_null_date():
    result = parse_set_pages([
        list_page([
            make_set(
                "zen", "id2",
                released_at=None,
                digital=True,
            ),
            make_set(),
        ])
    ])

    assert [record.code for record in result] == ["m21", "zen"]
    assert result[0].set_id == "uuid-1"
    assert result[1].released_at is None
    assert result[1].digital is True


def test_parse_paginated_set_list():
    pages = [
        list_page(
            [make_set()],
            more=True,
            next_page="https://api.scryfall.com/sets?page=2",
        ),
        list_page([make_set("znr", "uuid-2")]),
    ]

    assert {
        record.code
        for record in parse_set_pages(iter(pages))
    } == {"m21", "znr"}


@pytest.mark.parametrize(
    "pages,match",
    [
        ([], "incomplete or empty"),
        ([list_page([])], "incomplete or empty"),
        ([
            list_page(
                [make_set()],
                more=True,
                next_page="/sets?page=2",
            )
        ], "incomplete"),
        ([
            list_page([make_set()], more=True)
        ], "requires next_page"),
        ([
            list_page([make_set()], more=False),
            list_page([make_set("p", "p")]),
        ], "unexpected page"),
        ([
            dict(object="catalog", data=[])
        ], "Scryfall List"),
        ([
            list_page([make_set(digital=1)])
        ], "digital"),
        ([
            list_page([
                make_set(released_at="2020-13-99")
            ])
        ], "released_at"),
        ([
            list_page([
                make_set(code="m21"),
                make_set(code="M21", set_id="other"),
            ])
        ], "duplicate"),
        ([
            list_page([
                make_set(),
                make_set("znr", "uuid-1"),
            ])
        ], "duplicate"),
    ],
)
def test_set_pages_reject_malformed_or_partial_data(pages, match):
    with pytest.raises(CatalogError, match=match):
        parse_set_pages(pages)


def test_vocabulary_preserves_multiword_subtypes_and_battles():
    vocabulary = make_type_vocabulary(sample_catalogs())

    supertypes, card_types, subtypes = vocabulary.parse(
        "Legendary Creature — Time Lord Doctor"
    )

    assert supertypes == frozenset({"Legendary"})
    assert card_types == frozenset({"Creature"})
    assert subtypes == frozenset({
        "Time Lord", "Doctor"
    })

    assert vocabulary.parse(
        "Battle — Siege"
    )[2] == frozenset({"Siege"})

    assert vocabulary.parse(
        "Kindred Instant — Arcane"
    )[1] == frozenset({"Kindred", "Instant"})

    assert vocabulary.parse(
        "Artifact — Equipment"
    )[2] == frozenset({"Equipment"})


def test_missing_catalog_and_invalid_catalog_rejected():
    data = sample_catalogs()
    del data["land-types"]

    with pytest.raises(
        CatalogError,
        match="missing type catalogs",
    ):
        make_type_vocabulary(data)

    with pytest.raises(
        CatalogError,
        match="count or uniqueness",
    ):
        parse_type_catalog(
            "spell-types",
            dict(
                object="catalog",
                data=["Arcane"],
                total_values=2,
            ),
        )

    with pytest.raises(
        CatalogError,
        match="count or uniqueness",
    ):
        parse_type_catalog(
            "spell-types",
            catalog(["Arcane", "Arcane"]),
        )

    with pytest.raises(
        CatalogError,
        match="invalid catalog entries",
    ):
        parse_type_catalog(
            "spell-types",
            catalog(["Arcane", 5]),
        )


def test_optional_additional_catalogs_not_required():
    data = sample_catalogs()
    data["battle-types"] = catalog([
        "Siege", "Skirmish",
    ])
    data["card-types"] = catalog(["ExtraType"])

    vocabulary = make_type_vocabulary(data)

    assert vocabulary.parse(
        "ExtraType — Skirmish"
    ) == (
        frozenset(),
        frozenset({"ExtraType"}),
        frozenset({"Skirmish"}),
    )


def test_explicit_fetch_paginates_and_fetches_required_catalogs():
    requests = []

    first = list_page(
        [make_set()],
        more=True,
        next_page=SETS_URL + "?page=2",
    )

    data = {
        SETS_URL: first,
        SETS_URL + "?page=2": list_page([
            make_set("zen", "id2"),
        ]),
    }
    data.update({
        CATALOG_BASE + name: value
        for name, value in sample_catalogs().items()
    })

    def fake_fetch(url):
        requests.append(url)
        return data[url]

    result = fetch_reference_data(
        fetch_json=fake_fetch,
        delay_seconds=0,
    )

    assert len(result.sets) == 2
    assert len(requests) == 2 + len(TYPE_CATALOGS)
    assert requests[:2] == [
        SETS_URL,
        SETS_URL + "?page=2",
    ]
    assert "Time Lord" in result.vocabulary.subtypes


@pytest.mark.parametrize(
    "url",
    [
        "http://api.scryfall.com/sets?page=2",
        "https://evil.example/sets?page=2",
        "https://api.scryfall.com/cards/search",
    ],
)
def test_fetch_rejects_untrusted_pagination_targets(url):
    calls = []

    def fake_fetch(requested):
        calls.append(requested)
        return list_page(
            [make_set()],
            more=True,
            next_page=url,
        )

    with pytest.raises(CatalogError):
        fetch_reference_data(
            fetch_json=fake_fetch,
            delay_seconds=0,
        )

    assert calls == [SETS_URL]


def test_fetch_rejects_pagination_cycles():
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return list_page(
            [make_set()],
            more=True,
            next_page=SETS_URL,
        )

    with pytest.raises(
        CatalogError,
        match="repeated pagination",
    ):
        fetch_reference_data(
            fetch_json=fake_fetch,
            delay_seconds=0,
        )

    assert calls == [SETS_URL]


def test_write_sets_then_integrate_with_ingestion_and_query(tmp_path):
    result = parse_set_pages([
        list_page([make_set()])
    ])
    vocabulary = make_type_vocabulary(sample_catalogs())

    path = tmp_path / "staging.sqlite"

    with staging_database(path) as conn:
        count = write_sets(conn, result)
        assert count == 1

        assert conn.execute(
            "SELECT code, digital FROM sets"
        ).fetchall() == [("m21", 0)]

        raw = printing(
            id="id-print",
            set_id="uuid-1",
            set="m21",
            rarity="rare",
            games=["paper"],
        )

        stats = ingest_printings(
            conn,
            [raw],
            vocabulary,
        )
        assert stats.oracle_cards == 1

        conn.execute(
            "INSERT INTO database_metadata "
            "VALUES (1, 'today', '0.1.0')"
        )
        conn.commit()

    with CardDatabase.open(path) as db:
        assert db.by_set("m21") == frozenset({
            "oracle-1"
        })
        assert db.by_subtype(
            "Time Lord"
        ) == frozenset({"oracle-1"})
        assert db.by_set_rarity(
            "m21", "rare"
        ) == frozenset({"oracle-1"})
        assert db.get_set("m21").set_type == "core"


def test_write_sets_failure_rolls_back_only_owned_rows(tmp_path):
    with staging_database(tmp_path / "build.sqlite") as conn:
        conn.execute(
            "INSERT INTO database_metadata "
            "VALUES (1, 'today', '0.1.0')"
        )

        values = [
            SetRecord(
                "id1", "m21", "M21", "core", None, False,
            ),
            SetRecord(
                "id2", "M21", "duplicate",
                "core", None, False,
            ),
        ]

        with pytest.raises(
            CatalogError, match="duplicate"
        ):
            write_sets(conn, values)

        assert conn.execute(
            "SELECT * FROM sets"
        ).fetchall() == []

        assert conn.execute(
            "SELECT * FROM database_metadata"
        ).fetchall() == [(1, "today", "0.1.0")]

        assert write_sets(conn, values[:1]) == 1

        with pytest.raises(
            CatalogError, match="already populated"
        ):
            write_sets(conn, values[:1])


def test_write_sets_does_not_commit_callers_transaction(tmp_path):
    with staging_database(tmp_path / "build.sqlite") as conn:
        record = SetRecord(
            "id", "m21", "M21", "core", None, False,
        )

        conn.execute("BEGIN")
        write_sets(conn, [record])
        conn.rollback()

        assert conn.execute(
            "SELECT COUNT(*) FROM sets"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "type_line, expected_types, expected_subtypes",
    [
        ("Card", {"Card"}, set()),
        ("Event", {"Event"}, set()),
        ("Boss", {"Boss"}, set()),
        ("Plane — Dominaria", {"Plane"}, {"Dominaria"}),
        ("Plane — New Phyrexia", {"Plane"}, {"New Phyrexia"}),
        (
            "Artifact Creature — Cyborg Knight",
            {"Artifact", "Creature"},
            {"Cyborg", "Knight"},
        ),
        (
            "Legendary Creature — Time Lord Doctor",
            {"Creature"},
            {"Time Lord", "Doctor"},
        ),
    ],
)
def test_observed_source_type_lines(
    type_line, expected_types, expected_subtypes
):
    data = sample_catalogs()

    # Knight is supplied by the ordinary creature subtype catalog.
    data["creature-types"] = catalog([
        *data["creature-types"]["data"],
        "Knight",
    ])

    vocabulary = make_type_vocabulary(data)
    _, types, subtypes = vocabulary.parse(type_line)

    assert types == frozenset(expected_types)
    assert subtypes == frozenset(expected_subtypes)


@pytest.mark.parametrize(
    "type_line",
    [
        "Creature — New Phyrexia",
        "Plane — ",
        "Imaginary",
    ],
)
def test_unrecognized_types_still_fail(type_line):
    from mtg_carddb.normalization import NormalizationError

    vocabulary = make_type_vocabulary(sample_catalogs())

    with pytest.raises(NormalizationError):
        vocabulary.parse(type_line)


def test_realistic_planar_printing_normalizes():
    from mtg_carddb.normalization import normalize_printing

    vocabulary = make_type_vocabulary(sample_catalogs())

    source = printing(
        id="plane-print",
        oracle_id="plane-oracle",
        layout="planar",
        name="Example Plane",
        type_line="Plane — New Phyrexia",
        cmc=0,
        colors=[],
        color_identity=[],
        mana_cost="",
        power=None,
        toughness=None,
    )

    candidate, = normalize_printing(source, vocabulary)

    assert candidate.to_card().types == frozenset({"Plane"})
    assert candidate.faces[0].subtypes == frozenset({
        "New Phyrexia"
    })
