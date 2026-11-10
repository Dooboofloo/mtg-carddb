"""Offline acquisition tests: no requests to Scryfall are made."""

from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
from io import BytesIO
import json

import pytest

from mtg_carddb import acquisition
from mtg_carddb.acquisition import (
    BulkAcquisitionError,
    acquire_bulk_sources,
)


UPDATED = "2026-11-10T12:00:00Z"

URLS = {
    "default_cards": (
        "https://data.scryfall.io/"
        "default-cards/mock.jsonl.gz"
    ),
    "oracle_tags": (
        "https://data.scryfall.io/"
        "oracle-tags/mock.jsonl.gz"
    ),
}


def _fixture_responses(
    *,
    bad_tags=None,
    legacy=False,
    bad_size=False,
):
    payloads = {
        "default_cards": gzip.compress(
            b'{"object":"card","id":"card-1"}\n'
        ),
        "oracle_tags": gzip.compress(
            b'{"object":"tag","type":"oracle","slug":"x"}\n'
        ),
    }

    if bad_tags is not None:
        payloads["oracle_tags"] = bad_tags

    responses = {}

    for kind in URLS:
        meta = {
            "object": "bulk_data",
            "type": kind,
            "updated_at": UPDATED,
            "compressed_size": (
                len(payloads[kind])
                + (
                    1
                    if bad_size and kind == "oracle_tags"
                    else 0
                )
            ),
            (
                "download_uri"
                if legacy
                else "jsonl_download_uri"
            ): URLS[kind],
        }

        responses[
            f"https://api.scryfall.com/bulk-data/{kind}"
        ] = json.dumps(meta).encode("utf-8")

        responses[URLS[kind]] = payloads[kind]

    return responses, payloads


def _mock_http(monkeypatch, responses):
    calls = []

    def open_url(request, *, timeout):
        calls.append(request.full_url)
        return BytesIO(responses[request.full_url])

    monkeypatch.setattr(
        acquisition, "urlopen", open_url
    )

    return calls


def test_acquires_two_verified_gzip_exports(
    tmp_path, monkeypatch
):
    responses, payloads = _fixture_responses()
    calls = _mock_http(monkeypatch, responses)

    acquired = acquire_bulk_sources(
        tmp_path / "new-snapshot"
    )

    assert acquired.cards_path.read_bytes() == (
        payloads["default_cards"]
    )
    assert acquired.tags_path.read_bytes() == (
        payloads["oracle_tags"]
    )

    assert {
        source.source_type
        for source in acquired.bulk_sources
    } == {"default_cards", "oracle_tags"}

    assert {
        source.record_count
        for source in acquired.bulk_sources
    } == {1}

    for source in acquired.bulk_sources:
        assert source.sha256 == hashlib.sha256(
            payloads[source.source_type]
        ).hexdigest()

        assert source.source_updated_at == UPDATED
        assert source.source_url == URLS[source.source_type]

        assert datetime.fromisoformat(
            source.downloaded_at
        ).tzinfo is not None

    assert len(calls) == 4


def test_does_not_overwrite_existing_directory(
    tmp_path, monkeypatch
):
    destination = tmp_path / "existing"
    destination.mkdir()

    original = destination / "user-data.txt"
    original.write_text("keep me")

    with pytest.raises(FileExistsError):
        acquire_bulk_sources(destination)

    assert original.read_text() == "keep me"


def test_rejects_legacy_array_metadata(
    tmp_path, monkeypatch
):
    responses, _ = _fixture_responses(legacy=True)
    _mock_http(monkeypatch, responses)

    destination = tmp_path / "legacy"

    with pytest.raises(
        BulkAcquisitionError,
        match="jsonl_download_uri",
    ):
        acquire_bulk_sources(destination)

    assert not destination.exists()


def test_rejects_corrupted_gzip_and_cleans_up(
    tmp_path, monkeypatch
):
    responses, _ = _fixture_responses(
        bad_tags=b"not a gzip stream"
    )
    _mock_http(monkeypatch, responses)

    destination = tmp_path / "corrupt"

    with pytest.raises(
        BulkAcquisitionError,
        match="invalid gzip JSONL",
    ):
        acquire_bulk_sources(destination)

    assert not destination.exists()


def test_rejects_invalid_json_object_and_cleans_up(
    tmp_path, monkeypatch
):
    bad = gzip.compress(
        b'{"object":"card"}\n'
    )
    responses, _ = _fixture_responses(
        bad_tags=bad
    )
    _mock_http(monkeypatch, responses)

    destination = tmp_path / "wrong-object"

    with pytest.raises(
        BulkAcquisitionError,
        match="unexpected object",
    ):
        acquire_bulk_sources(destination)

    assert not destination.exists()


def test_rejects_corrupt_json_record(
    tmp_path, monkeypatch
):
    bad = gzip.compress(b'{not-json}\n')
    responses, _ = _fixture_responses(
        bad_tags=bad
    )
    _mock_http(monkeypatch, responses)

    with pytest.raises(
        BulkAcquisitionError,
        match="invalid gzip JSONL",
    ):
        acquire_bulk_sources(tmp_path / "bad-json")


def test_rejects_compressed_size_mismatch(
    tmp_path, monkeypatch
):
    responses, _ = _fixture_responses(
        bad_size=True
    )
    _mock_http(monkeypatch, responses)

    destination = tmp_path / "size-mismatch"

    with pytest.raises(
        BulkAcquisitionError,
        match="compressed size mismatch",
    ):
        acquire_bulk_sources(destination)

    assert not destination.exists()


def test_second_download_failure_removes_first(
    tmp_path, monkeypatch
):
    responses, _ = _fixture_responses()
    del responses[URLS["oracle_tags"]]

    _mock_http(monkeypatch, responses)

    destination = tmp_path / "interrupted"

    with pytest.raises(KeyError):
        acquire_bulk_sources(destination)

    assert not destination.exists()


def test_rejects_non_https_download_url(
    tmp_path, monkeypatch
):
    responses, _ = _fixture_responses()

    metadata_url = (
        "https://api.scryfall.com/bulk-data/default_cards"
    )
    metadata = json.loads(responses[metadata_url])

    metadata["jsonl_download_uri"] = (
        "http://bad.example/cards.jsonl.gz"
    )
    responses[metadata_url] = json.dumps(
        metadata
    ).encode()

    _mock_http(monkeypatch, responses)

    with pytest.raises(
        BulkAcquisitionError,
        match="HTTPS",
    ):
        acquire_bulk_sources(
            tmp_path / "insecure"
        )


def test_rejects_empty_export(
    tmp_path, monkeypatch
):
    responses, _ = _fixture_responses(
        bad_tags=gzip.compress(b"")
    )
    _mock_http(monkeypatch, responses)

    with pytest.raises(
        BulkAcquisitionError,
        match="empty bulk export",
    ):
        acquire_bulk_sources(tmp_path / "empty")
