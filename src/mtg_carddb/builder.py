"""Build isolated, validated CardDB snapshots from local Scryfall exports.

This module creates a new database file. It never replaces an existing
database or activates a snapshot.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .bulk import iter_default_cards
from .catalogs import (
    ReferenceData,
    fetch_reference_data,
    write_sets,
)
from .database import CardDatabase
from .import_policy import (
    exclude_art_only_taggings,
    find_art_only_oracle_ids,
)
from .ingestion import ingest_printings
from .oracle_tags import ingest_oracle_tags, iter_oracle_tags
from .schema import staging_database
from .provenance import (
    BulkSource,
    verify_bulk_sources,
    write_bulk_sources,
)
from .reference_sources import ReferenceArchive
from .reference_integration import (
    replay_reference_archive,
    write_reference_sources,
)

class SnapshotBuildError(RuntimeError):
    """A generated snapshot failed validation."""


@dataclass(frozen=True, slots=True)
class SnapshotBuildResult:
    path: Path
    sets: int
    printings: int
    excluded_printings: int
    cards: int
    historical_observations: int
    tags: int
    taggings: int
    art_only_oracle_ids: int
    excluded_art_taggings: int
    provenance_sources: int = 0


def _validate_counts(
    connection: sqlite3.Connection,
    *,
    sets: int,
    cards: int,
    historical_observations: int,
    tags: int,
    taggings: int,
    provenance_sources: int = 0,
) -> None:
    expected = {
        "sets": sets,
        "cards": cards,
        "card_set_rarities": historical_observations,
        "tags": tags,
        "card_tags": taggings,
        "source_metadata": provenance_sources,
    }

    for table, count in expected.items():
        actual = connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]

        if actual != count:
            raise SnapshotBuildError(
                f"{table}: expected {count} rows; found {actual}"
            )


def _validate_integrity(
    connection: sqlite3.Connection,
) -> None:
    check = connection.execute(
        "PRAGMA integrity_check"
    ).fetchone()

    if check is None or check[0] != "ok":
        raise SnapshotBuildError(
            f"SQLite integrity check failed: {check!r}"
        )

    violations = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if violations:
        raise SnapshotBuildError(
            f"SQLite foreign-key violations: {violations[:5]!r}"
        )


def build_snapshot(
    cards_path: str | Path,
    tags_path: str | Path,
    output_path: str | Path,
    *,
    references: ReferenceData | None = None,
    bulk_sources: Iterable[BulkSource] | None = None,
    reference_archive: ReferenceArchive | str | Path | None = None,
) -> SnapshotBuildResult:
    """Build a new verified database without replacing existing files.

    Requires local Scryfall JSONL gzip exports. Fetches live set/type
    catalogs unless ReferenceData is explicitly supplied.

    On ingestion or validation failure, the incomplete output is
    deleted. Source files are never modified.
    """

    if references is not None and reference_archive is not None:
        raise ValueError(
            "references and reference_archive are mutually exclusive"
        )

    cards_path = Path(cards_path)
    tags_path = Path(tags_path)
    output_path = Path(output_path)

    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"database already exists: {output_path}"
        )

    for source in (cards_path, tags_path):
        if not source.is_file():
            raise FileNotFoundError(
                f"source file not found: {source}"
            )

    verified_reference_archive = None

    if reference_archive is not None:
        references, verified_reference_archive = (
            replay_reference_archive(reference_archive)
        )
    elif references is None:
        references = fetch_reference_data()

    # Derive exclusions from raw Oracle IDs, not from the subset
    # of cards that happen to normalize successfully.
    art_only = find_art_only_oracle_ids(
        iter_default_cards(cards_path)
    )

    excluded_by_tag: Counter[str] = Counter()

    with staging_database(output_path) as connection:
        sets = write_sets(connection, references.sets)

        card_stats = ingest_printings(
            connection,
            iter_default_cards(cards_path),
            references.vocabulary,
        )

        tag_stats = ingest_oracle_tags(
            connection,
            exclude_art_only_taggings(
                iter_oracle_tags(tags_path),
                art_only,
                excluded_by_tag,
            ),
        )

        provenance_count = 0

        if bulk_sources is not None:
            verified_sources = verify_bulk_sources(
                bulk_sources,
                {
                    "default_cards": (
                        cards_path,
                        card_stats.printings,
                    ),
                    "oracle_tags": (
                        tags_path,
                        tag_stats.tags,
                    ),
                },
            )

            provenance_count = write_bulk_sources(
                connection,
                verified_sources,
            )

        if verified_reference_archive is not None:
            provenance_count += write_reference_sources(
                connection,
                verified_reference_archive,
            )

        # Minimal builder metadata. Detailed Scryfall source
        # provenance will be handled by a later milestone.
        connection.execute(
            "INSERT INTO database_metadata "
            "(id, created_at, builder_version) "
            "VALUES (1, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "snapshot-builder-v1",
            ),
        )

        _validate_counts(
            connection,
            sets=sets,
            cards=card_stats.oracle_cards,
            historical_observations=(
                card_stats.historical_observations
            ),
            tags=tag_stats.tags,
            taggings=tag_stats.taggings,
            provenance_sources=provenance_count,
        )

        _validate_integrity(connection)
        connection.commit()

    # Test the actual read-only public API after closing the
    # writable database connection.
    try:
        with CardDatabase.open(output_path) as database:
            if len(database) != card_stats.oracle_cards:
                raise SnapshotBuildError(
                    "public CardDatabase count differs "
                    "from imported cards"
                )

            database.all_cards()
            database.by_type("Creature")

    except BaseException:
        # A failed public-API verification must not leave a
        # database that appears to have built successfully.
        output_path.unlink(missing_ok=True)
        raise

    return SnapshotBuildResult(
        path=output_path,
        sets=sets,
        printings=card_stats.printings,
        excluded_printings=card_stats.excluded_printings,
        cards=card_stats.oracle_cards,
        historical_observations=(
            card_stats.historical_observations
        ),
        tags=tag_stats.tags,
        taggings=tag_stats.taggings,
        art_only_oracle_ids=len(art_only),
        excluded_art_taggings=sum(
            excluded_by_tag.values()
        ),
        provenance_sources=provenance_count,
    )
