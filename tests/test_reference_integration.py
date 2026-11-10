"""Offline tests of archive replay and reference-source provenance."""

import sqlite3

import pytest

from mtg_carddb import reference_integration as ri
from mtg_carddb import reference_sources as rs
from test_reference_sources import stub_http, wire_responses


def captured(tmp_path, monkeypatch, *, paginated=False):
    stub_http(
        monkeypatch,
        wire_responses(paginated=paginated),
    )
    return rs.capture_reference_sources(
        tmp_path / "references"
    )


def test_replay_supplies_all_exact_responses(
    tmp_path, monkeypatch
):
    archive = captured(
        tmp_path, monkeypatch, paginated=True
    )

    def parse(*, fetch_json, delay_seconds):
        assert delay_seconds == 0.0

        return {
            source.source_type: fetch_json(source.source_url)
            for source in archive.sources
        }

    monkeypatch.setattr(
        ri, "fetch_reference_data", parse
    )

    references, verified = ri.replay_reference_archive(
        archive
    )

    assert verified.sources == archive.sources
    assert len(references) == 9

    assert references["sets:page:2"][
        "data"
    ][0]["code"] == "two"


def test_replay_rejects_unused_response(
    tmp_path, monkeypatch
):
    archive = captured(tmp_path, monkeypatch)

    def parse(*, fetch_json, delay_seconds):
        assert delay_seconds == 0.0
        return fetch_json(rs._SETS_URL)

    monkeypatch.setattr(
        ri, "fetch_reference_data", parse
    )

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="not all used",
    ):
        ri.replay_reference_archive(archive)


def test_replay_never_falls_back_to_network(
    tmp_path, monkeypatch
):
    archive = captured(tmp_path, monkeypatch)

    def parse(*, fetch_json, delay_seconds):
        assert delay_seconds == 0.0

        return fetch_json(
            "https://api.scryfall.com/sets?page=99"
        )

    monkeypatch.setattr(
        ri, "fetch_reference_data", parse
    )

    with pytest.raises(
        rs.ReferenceArchiveError, match="absent"
    ):
        ri.replay_reference_archive(archive)


def test_replay_rechecks_files_before_parsing(
    tmp_path, monkeypatch
):
    archive = captured(tmp_path, monkeypatch)

    (
        archive.directory
        / "catalog-supertypes.json"
    ).write_text("{}")

    def fail(*, get_json):
        pytest.fail(
            "parser should not run on tampered archive"
        )

    monkeypatch.setattr(
        ri, "fetch_reference_data", fail
    )

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="checksum mismatch",
    ):
        ri.replay_reference_archive(archive)


def test_all_archived_sources_are_recorded(
    tmp_path, monkeypatch
):
    archive = captured(
        tmp_path, monkeypatch, paginated=True
    )

    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE source_metadata ("
            "source_type TEXT PRIMARY KEY, "
            "source_updated_at TEXT, "
            "downloaded_at TEXT NOT NULL, "
            "source_url TEXT NOT NULL, "
            "sha256 TEXT NOT NULL, "
            "record_count INTEGER NOT NULL)"
        )

        assert ri.write_reference_sources(
            db, archive
        ) == 9

        rows = db.execute(
            "SELECT source_type, sha256, record_count "
            "FROM source_metadata"
        ).fetchall()

    recorded = {
        kind: (sha, count)
        for kind, sha, count in rows
    }

    assert recorded == {
        source.source_type: (
            source.sha256,
            source.record_count,
        )
        for source in archive.sources
    }


def test_builder_refuses_two_reference_providers(
    tmp_path,
):
    from types import SimpleNamespace
    from mtg_carddb.builder import build_snapshot

    cards = tmp_path / "cards.gz"
    tags = tmp_path / "tags.gz"
    cards.touch()
    tags.touch()

    output = tmp_path / "output.sqlite"

    with pytest.raises(
        ValueError, match="references"
    ):
        build_snapshot(
            cards,
            tags,
            output,
            references=SimpleNamespace(
                sets=[],
                vocabulary=object(),
            ),
            reference_archive=tmp_path / "archive",
        )

    assert not output.exists()
