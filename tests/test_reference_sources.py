"""Offline tests for exact-response Scryfall reference archives."""

import hashlib
from io import BytesIO
import json

import pytest

from mtg_carddb import reference_sources as rs


SETS = rs._SETS_URL


def wire_responses(*, paginated=False):
    next_url = SETS + "?page=2"

    first = {
        "object": "list",
        "has_more": paginated,
        "data": [
            {
                "object": "set",
                "id": "set-1",
                "code": "one",
            },
        ],
    }

    if paginated:
        first["next_page"] = next_url

    responses = {
        SETS: json.dumps(first).encode("utf-8"),
    }

    if paginated:
        responses[next_url] = json.dumps({
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "object": "set",
                    "id": "set-2",
                    "code": "two",
                },
            ],
        }).encode("utf-8")

    for name in rs._CATALOG_NAMES:
        responses[
            rs._CATALOG_ROOT + name
        ] = json.dumps({
            "object": "catalog",
            "data": ["Alpha", "Beta"],
        }).encode("utf-8")

    return responses


def stub_http(monkeypatch, responses):
    seen = []

    def fake_open(request, *, timeout):
        seen.append(request.full_url)
        return BytesIO(
            responses[request.full_url]
        )

    monkeypatch.setattr(
        rs, "urlopen", fake_open
    )
    monkeypatch.setattr(
        rs, "sleep", lambda _: None
    )

    return seen


def test_complete_archive_preserves_exact_response_bytes(
    tmp_path, monkeypatch
):
    responses = wire_responses(paginated=True)
    seen = stub_http(monkeypatch, responses)
    directory = tmp_path / "ref"

    archive = rs.capture_reference_sources(
        directory
    )

    assert len(archive.sources) == 9
    assert len(seen) == 9
    assert archive.json_for_url(SETS)[
        "has_more"
    ] is True

    for source in archive.sources:
        raw = (
            directory / source.filename
        ).read_bytes()

        assert raw == responses[source.source_url]
        assert source.sha256 == hashlib.sha256(
            raw
        ).hexdigest()
        assert source.record_count in (1, 2)

    reopened = rs.load_reference_sources(
        directory
    )

    assert reopened.sources == archive.sources


def test_existing_destination_is_preserved(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()

    sentinel = destination / "secret.txt"
    sentinel.write_text("do not delete")

    with pytest.raises(FileExistsError):
        rs.capture_reference_sources(
            destination
        )

    assert sentinel.read_text() == "do not delete"


def test_second_catalog_download_failure_cleans_up(
    tmp_path, monkeypatch
):
    responses = wire_responses()

    del responses[
        rs._CATALOG_ROOT + "creature-types"
    ]

    stub_http(monkeypatch, responses)
    destination = tmp_path / "failed"

    with pytest.raises(KeyError):
        rs.capture_reference_sources(
            destination
        )

    assert not destination.exists()


def test_rejects_foreign_next_page(
    tmp_path, monkeypatch
):
    responses = wire_responses()
    data = json.loads(responses[SETS])

    data["has_more"] = True
    data["next_page"] = (
        "https://example.org/suspicious"
    )

    responses[SETS] = json.dumps(
        data
    ).encode()

    stub_http(monkeypatch, responses)
    destination = tmp_path / "bad-next"

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="pagination URL",
    ):
        rs.capture_reference_sources(
            destination
        )

    assert not destination.exists()


def test_cyclic_pagination_rejected(
    tmp_path, monkeypatch
):
    responses = wire_responses()
    data = json.loads(responses[SETS])

    data["has_more"] = True
    data["next_page"] = SETS

    responses[SETS] = json.dumps(
        data
    ).encode()

    stub_http(monkeypatch, responses)

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="cyclic",
    ):
        rs.capture_reference_sources(
            tmp_path / "cycle"
        )


def test_archive_checksum_tampering_is_rejected(
    tmp_path, monkeypatch
):
    stub_http(
        monkeypatch,
        wire_responses(),
    )

    directory = tmp_path / "source"

    rs.capture_reference_sources(
        directory
    )

    (
        directory / "catalog-creature-types.json"
    ).write_text("{}")

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="checksum mismatch",
    ):
        rs.load_reference_sources(
            directory
        )


def test_manifest_rejects_filename_traversal(
    tmp_path, monkeypatch
):
    stub_http(
        monkeypatch,
        wire_responses(),
    )

    directory = tmp_path / "source"

    rs.capture_reference_sources(
        directory
    )

    manifest_path = directory / "manifest.json"
    manifest = json.loads(
        manifest_path.read_text()
    )

    manifest["sources"][0]["filename"] = (
        "../escape.json"
    )

    manifest_path.write_text(
        json.dumps(manifest)
    )

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="filename",
    ):
        rs.load_reference_sources(
            directory
        )


def test_missing_required_catalog_is_rejected(
    tmp_path, monkeypatch
):
    stub_http(
        monkeypatch,
        wire_responses(),
    )

    directory = tmp_path / "source"

    rs.capture_reference_sources(
        directory
    )

    manifest_path = directory / "manifest.json"
    manifest = json.loads(
        manifest_path.read_text()
    )

    manifest["sources"] = [
        source
        for source in manifest["sources"]
        if source["source_type"]
        != "catalog:spell-types"
    ]

    manifest_path.write_text(
        json.dumps(manifest)
    )

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="missing or extra",
    ):
        rs.load_reference_sources(
            directory
        )


def test_unknown_url_is_not_silently_fetched(
    tmp_path, monkeypatch
):
    stub_http(
        monkeypatch,
        wire_responses(),
    )

    archive = rs.capture_reference_sources(
        tmp_path / "source"
    )

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="absent",
    ):
        archive.json_for_url(
            "https://api.scryfall.com/catalog/unexpected"
        )


def test_captured_catalog_invalid_payload_is_rejected(
    tmp_path, monkeypatch
):
    responses = wire_responses()

    responses[
        rs._CATALOG_ROOT + "supertypes"
    ] = (
        b'{"object":"catalog",'
        b'"data":[{"not":"a subtype"}]}'
    )

    stub_http(monkeypatch, responses)
    destination = tmp_path / "bad"

    with pytest.raises(
        rs.ReferenceArchiveError,
        match="invalid catalog",
    ):
        rs.capture_reference_sources(
            destination
        )

    assert not destination.exists()
