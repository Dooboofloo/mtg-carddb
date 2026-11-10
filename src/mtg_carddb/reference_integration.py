"""Replay verified Scryfall references and persist their provenance."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .catalogs import ReferenceData, fetch_reference_data
from .reference_sources import (
    ReferenceArchive,
    ReferenceArchiveError,
    load_reference_sources,
)


def replay_reference_archive(
    archive: ReferenceArchive | str | Path,
) -> tuple[ReferenceData, ReferenceArchive]:
    """Parse archived responses through the canonical catalog parser.

    Every archived response must be consumed. Missing URLs fail
    immediately; there is no network fallback.
    """
    directory = (
        archive.directory
        if isinstance(archive, ReferenceArchive)
        else Path(archive)
    )

    # Verify the manifest, checksums, and page continuity again.
    verified = load_reference_sources(directory)
    consumed: set[str] = set()

    def get_json(url: str) -> dict:
        document = verified.json_for_url(url)
        consumed.add(url)
        return document

    references = fetch_reference_data(
        fetch_json=get_json,
        delay_seconds=0.0,
    )

    expected = {
        source.source_url
        for source in verified.sources
    }

    if consumed != expected:
        unused = sorted(expected - consumed)

        raise ReferenceArchiveError(
            "archive responses were not all used: "
            f"{unused!r}"
        )

    return references, verified


def write_reference_sources(
    connection: sqlite3.Connection,
    archive: ReferenceArchive,
) -> int:
    """Persist every Sets page and type-catalog source record."""

    connection.executemany(
        "INSERT INTO source_metadata "
        "(source_type, source_updated_at, downloaded_at, "
        "source_url, sha256, record_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                source.source_type,
                source.source_updated_at,
                source.downloaded_at,
                source.source_url,
                source.sha256,
                source.record_count,
            )
            for source in archive.sources
        ),
    )

    return len(archive.sources)
