"""Audit a complete CardDB staging build using local Scryfall bulk exports.

This is diagnostic tooling: it NEVER writes to a published carddb.sqlite.
Temporary databases are deleted after completion, including on failure.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable
from contextlib import closing

from mtg_carddb.bulk import iter_default_cards
from mtg_carddb.catalogs import fetch_reference_data, write_sets
from mtg_carddb.database import CardDatabase
from mtg_carddb.ingestion import _decode, _encode, ingest_printings
from mtg_carddb.normalization import TypeVocabulary, normalize_printing
from mtg_carddb.oracle_tags import ingest_oracle_tags, iter_oracle_tags
from mtg_carddb.representatives import select_representative
from mtg_carddb.schema import staging_database


def printing_oracle_ids(printing: dict[str, Any]) -> set[str]:
    if printing.get("layout") == "reversible_card":
        return {
            face["oracle_id"]
            for face in printing.get("card_faces", [])
            if isinstance(face, dict)
            and isinstance(face.get("oracle_id"), str)
        }

    value = printing.get("oracle_id")
    return {value} if isinstance(value, str) else set()


def audit_representatives(
    cards_path: Path,
    vocabulary: TypeVocabulary,
    spool_path: Path,
) -> tuple[frozenset[str], frozenset[str], int]:
    """Check every Oracle identity without loading the export into RAM."""
    art_ids: set[str] = set()
    non_art_ids: set[str] = set()
    total = excluded = candidates_count = 0

    with closing(sqlite3.connect(spool_path)) as db:
        db.execute(
            "CREATE TABLE candidates ("
            "oracle_id TEXT NOT NULL, "
            "printing_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, "
            "PRIMARY KEY (oracle_id, printing_id)"
            ") WITHOUT ROWID"
        )

        for printing in iter_default_cards(cards_path):
            total += 1
            ids = printing_oracle_ids(printing)

            if printing.get("layout") == "art_series":
                excluded += 1
                art_ids.update(ids)
                continue

            non_art_ids.update(ids)

            for candidate in normalize_printing(printing, vocabulary):
                db.execute(
                    "INSERT INTO candidates VALUES (?, ?, ?)",
                    (
                        candidate.oracle_id,
                        candidate.printing_id,
                        _encode(candidate),
                    ),
                )
                candidates_count += 1

            if total % 25000 == 0:
                print(
                    f"  Scanned {total:,} printings",
                    flush=True,
                )

        db.commit()

        selected = 0
        aggregated_oldschool = 0
        errors: Counter[str] = Counter()
        examples: dict[str, list[str]] = defaultdict(list)

        for (oracle_id,) in db.execute(
            "SELECT DISTINCT oracle_id FROM candidates "
            "ORDER BY oracle_id"
        ):
            group = (
                _decode(row[0])
                for row in db.execute(
                    "SELECT payload FROM candidates "
                    "WHERE oracle_id = ? ORDER BY printing_id",
                    (oracle_id,),
                )
            )

            try:
                representative = select_representative(group)
                selected += 1

                if "oldschool" in representative.aggregated_formats:
                    aggregated_oldschool += 1

            except Exception as exc:
                category = type(exc).__name__
                errors[category] += 1

                if len(examples[category]) < 8:
                    examples[category].append(
                        f"{oracle_id}: {exc}"
                    )

    art_only = frozenset(art_ids - non_art_ids)

    print(f"Printings examined: {total:,}")
    print(f"Art-series printings excluded: {excluded:,}")
    print(f"Candidates created: {candidates_count:,}")
    print(f"Non-art Oracle identities: {len(non_art_ids):,}")
    print(f"Oracle identities selected: {selected:,}")
    print(f"Oldschool legality aggregates: {aggregated_oldschool:,}")
    print(f"Art-series-only Oracle IDs: {len(art_only):,}")
    print(f"Representative failures: {sum(errors.values()):,}")

    for category, count in errors.most_common():
        print(f"  {category}: {count:,}")

        for sample in examples[category]:
            print(f"    {sample}")

    if errors or selected != len(non_art_ids):
        raise RuntimeError(
            "representative audit failed; full import not attempted"
        )

    return frozenset(non_art_ids), art_only, aggregated_oldschool


def audit_tag_references(
    path: Path,
    included_ids: frozenset[str],
    art_only_ids: frozenset[str],
) -> int:
    """Count intentional art exclusions and reject unexpected missing IDs."""
    included = excluded = unexpected = 0
    examples: list[tuple[str, str]] = []

    for tag in iter_oracle_tags(path):
        taggings = tag.get("taggings")

        if not isinstance(taggings, list):
            raise ValueError(
                f"invalid taggings in {tag.get('slug')!r}"
            )

        for tagging in taggings:
            if not isinstance(tagging, dict):
                raise ValueError(
                    f"invalid tagging in {tag.get('slug')!r}"
                )

            oracle_id = tagging.get("oracle_id")

            if oracle_id in included_ids:
                included += 1
            elif oracle_id in art_only_ids:
                excluded += 1
            else:
                unexpected += 1

                if len(examples) < 10:
                    examples.append((
                        tag.get("slug", "?"),
                        str(oracle_id),
                    ))

    print(f"Tag assignments to included identities: {included:,}")
    print(f"Tag assignments to excluded art-only identities: {excluded:,}")
    print(f"Unexpected unresolved tag assignments: {unexpected:,}")

    for slug, oracle_id in examples:
        print(f"  {slug}: {oracle_id}")

    if unexpected:
        raise RuntimeError(
            "unexpected unresolved tag assignments"
        )

    return excluded


def filtered_tags(
    tags: Iterable[dict[str, Any]],
    art_only_ids: frozenset[str],
    excluded_by_tag: Counter[str],
) -> Iterable[dict[str, Any]]:
    """AUDIT ONLY: omit verified art-only assignments, not unknown IDs.

    Retain every tag definition, hierarchy edge, and other assignment.
    """
    for tag in tags:
        taggings = tag.get("taggings")

        if not isinstance(taggings, list):
            # Let the real importer report malformed data.
            yield tag
            continue

        kept = []

        for tagging in taggings:
            if (
                isinstance(tagging, dict)
                and tagging.get("oracle_id") in art_only_ids
            ):
                excluded_by_tag[str(tag.get("slug"))] += 1
            else:
                kept.append(tagging)

        yield {**tag, "taggings": kept}


def audit_full_build(
    cards_path: Path,
    tags_path: Path,
    references: Any,
    directory: Path,
    expected_ids: frozenset[str],
    art_only_ids: frozenset[str],
    expected_excluded_taggings: int,
) -> None:
    target = directory / "staging.sqlite"
    excluded_by_tag: Counter[str] = Counter()

    with staging_database(target) as conn:
        n_sets = write_sets(conn, references.sets)

        stats = ingest_printings(
            conn,
            iter_default_cards(cards_path),
            references.vocabulary,
        )

        print(f"Sets inserted: {n_sets:,}")
        print(f"Card ingestion: {stats}")

        if stats.oracle_cards != len(expected_ids):
            raise RuntimeError(
                "card count differs from Oracle representative audit"
            )

        tag_stats = ingest_oracle_tags(
            conn,
            filtered_tags(
                iter_oracle_tags(tags_path),
                art_only_ids,
                excluded_by_tag,
            ),
        )

        print(f"Tag ingestion: {tag_stats}")

        skipped = sum(excluded_by_tag.values())
        print(f"Audit-only art taggings excluded: {skipped:,}")
        print(
            f"Excluded by tag: {excluded_by_tag.most_common(10)}"
        )

        if skipped != expected_excluded_taggings:
            raise RuntimeError(
                "tag exclusion count changed between passes"
            )

        # Audit-only metadata, solely to permit a public-API smoke test.
        # Complete source provenance is still a future build milestone.
        conn.execute(
            "INSERT INTO database_metadata "
            "(id, created_at, builder_version) "
            "VALUES (1, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "audit-only",
            ),
        )

        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        violations = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        print(f"SQLite integrity: {integrity}")
        print(f"Foreign-key violations: {len(violations):,}")

        if integrity != "ok" or violations:
            raise RuntimeError(
                "SQLite validation failed"
            )

    with CardDatabase.open(target) as db:
        print(f"Public API cards: {len(db):,}")

        if db.all_cards() != expected_ids:
            raise RuntimeError(
                "stored Oracle IDs differ from candidate universe"
            )

        print(
            f"Creatures: {len(db.by_type('Creature')):,}"
        )
        print(
            f"RG-containing colors: {len(db.by_color('RG')):,}"
        )
        print(
            f"Game Changers: {len(db.game_changers()):,}"
        )
        print(
            "Activated-ability tag: "
            f"{len(db.by_tag('activated-ability')):,}"
        )

    print(
        "AUDIT PASSED — temporary staging database only"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True)

    args = parser.parse_args()

    for path in (args.cards, args.tags):
        if not path.is_file():
            parser.error(f"file not found: {path}")

    print(
        "Fetching current Scryfall set/type catalogs ...",
        flush=True,
    )

    references = fetch_reference_data()

    with tempfile.TemporaryDirectory(
        prefix="mtg-carddb-audit-"
    ) as work:
        directory = Path(work)

        print(
            "Auditing every Oracle representative ...",
            flush=True,
        )

        expected_ids, art_only, _ = audit_representatives(
            args.cards,
            references.vocabulary,
            directory / "candidates.sqlite",
        )

        print("Checking Oracle-tag references ...", flush=True)

        excluded = audit_tag_references(
            args.tags,
            expected_ids,
            art_only,
        )

        print(
            "Attempting complete staging build ...",
            flush=True,
        )

        audit_full_build(
            args.cards,
            args.tags,
            references,
            directory,
            expected_ids,
            art_only,
            excluded,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
