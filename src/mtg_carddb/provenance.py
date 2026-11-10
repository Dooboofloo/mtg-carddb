"""Verified provenance for locally supplied Scryfall bulk exports.

A manifest records facts supplied by the downloader or caller.
This module verifies local bytes and importer record counts;
it never invents download timestamps or source URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping
from urllib.parse import urlsplit


class SourceProvenanceError(ValueError):
    """Source metadata is invalid or disagrees with imported data."""


@dataclass(frozen=True, slots=True)
class BulkSource:
    source_type: str
    source_url: str
    downloaded_at: str
    sha256: str
    record_count: int
    source_updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.source_type not in {
            "default_cards",
            "oracle_tags",
        }:
            raise SourceProvenanceError(
                "unsupported bulk source type: "
                f"{self.source_type!r}"
            )

        if not isinstance(self.source_url, str):
            raise SourceProvenanceError(
                "source_url must be an absolute URL"
            )

        parsed = urlsplit(self.source_url)

        valid_remote = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
        )
        valid_local = (
            parsed.scheme == "file"
            and bool(parsed.path)
        )

        if not (valid_remote or valid_local):
            raise SourceProvenanceError(
                "source_url must be an absolute URL"
            )

        for field, value in (
            ("downloaded_at", self.downloaded_at),
            ("source_updated_at", self.source_updated_at),
        ):
            if value is None and field == "source_updated_at":
                continue

            if not isinstance(value, str):
                raise SourceProvenanceError(
                    f"{field} must be an ISO timestamp"
                )

            try:
                timestamp = datetime.fromisoformat(value)
            except ValueError as exc:
                raise SourceProvenanceError(
                    f"{field} must be an ISO timestamp"
                ) from exc

            if (
                timestamp.tzinfo is None
                or timestamp.utcoffset() is None
            ):
                raise SourceProvenanceError(
                    f"{field} must have a UTC offset"
                )

        if (
            not isinstance(self.sha256, str)
            or re.fullmatch(
                r"[0-9a-f]{64}", self.sha256
            ) is None
        ):
            raise SourceProvenanceError(
                "sha256 must be lowercase hex"
            )

        if (
            type(self.record_count) is not int
            or self.record_count < 0
        ):
            raise SourceProvenanceError(
                "record_count must be nonnegative"
            )


def sha256_file(path: str | Path) -> str:
    """Hash the exact compressed source bytes in bounded memory."""
    digest = hashlib.sha256()

    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def verify_bulk_sources(
    sources: Iterable[BulkSource],
    inputs: Mapping[str, tuple[Path, int]],
) -> tuple[BulkSource, ...]:
    """Verify both required bulk sources against imported data."""

    records = tuple(sources)
    kinds = {"default_cards", "oracle_tags"}

    if (
        len(records) != 2
        or {s.source_type for s in records} != kinds
    ):
        raise SourceProvenanceError(
            "exactly one default_cards and one oracle_tags "
            "record are required"
        )

    if set(inputs) != kinds:
        raise SourceProvenanceError(
            "invalid bulk source input mapping"
        )

    for source in records:
        path, count = inputs[source.source_type]

        if source.record_count != count:
            raise SourceProvenanceError(
                f"{source.source_type}: claimed "
                f"{source.record_count} records; ingested {count}"
            )

        actual = sha256_file(path)

        if source.sha256 != actual:
            raise SourceProvenanceError(
                f"{source.source_type}: SHA-256 mismatch "
                f"for {path}"
            )

    return tuple(
        sorted(records, key=lambda source: source.source_type)
    )


def write_bulk_sources(
    connection: sqlite3.Connection,
    sources: Iterable[BulkSource],
) -> int:
    """Persist verified source descriptions in the v1 schema."""

    records = tuple(sources)

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
            for source in records
        ),
    )

    return len(records)
