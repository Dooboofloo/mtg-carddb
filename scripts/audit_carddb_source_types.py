r"""Audit every current Scryfall printing for CardDB normalization failures.

Run from the mtg-carddb repository root with the local package installed
in editable mode. Does not build or modify a CardDB SQLite database.

    python .\scripts\audit_carddb_source_types.py

Or reuse a previously downloaded default-cards.jsonl.gz export:

    python .\scripts\audit_carddb_source_types.py --cards C:\\path\\default-cards.jsonl.gz
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mtg_carddb.acquisition import acquire_bulk_sources
from mtg_carddb.bulk import iter_default_cards
from mtg_carddb.catalogs import fetch_reference_data
from mtg_carddb.normalization import NormalizationError, normalize_printing


@dataclass
class Failure:
    count: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)


def _sample(record: dict[str, Any]) -> dict[str, Any]:
    faces = record.get("card_faces")
    return {
        "card_name": record.get("name"),
        "printing_id": record.get("id"),
        "set": record.get("set"),
        "layout": record.get("layout"),
        "type_line": record.get("type_line"),
        "face_type_lines": (
            [face.get("type_line") for face in faces if isinstance(face, dict)]
            if isinstance(faces, list) else None
        ),
    }


def audit(cards_path: Path, vocabulary: Any, *, example_limit: int) -> dict[str, Any]:
    failures: dict[str, Failure] = {}
    counts: Counter[str] = Counter()

    # Explicitly close the lazy generator if parsing aborts; Windows requires it.
    with closing(iter_default_cards(cards_path)) as printings:
        for printing in printings:
            counts["examined"] += 1
            if printing.get("layout") == "art_series":
                counts["art_series_excluded"] += 1
                continue
            try:
                normalize_printing(printing, vocabulary)
            except NormalizationError as exc:
                counts["normalization_failures"] += 1
                message = str(exc)
                # Normalization messages that contain a printing-specific UUID
                # are grouped under a stable placeholder for readability.
                printing_id = printing.get("id")
                if isinstance(printing_id, str):
                    message = message.replace(printing_id, "<printing-id>")
                failure = failures.setdefault(message, Failure())
                failure.count += 1
                if len(failure.examples) < example_limit:
                    failure.examples.append(_sample(printing))
            else:
                counts["normalized"] += 1

    rows = [
        {"error": msg, "count": failure.count, "examples": failure.examples}
        for msg, failure in sorted(
            failures.items(), key=lambda x: (-x[1].count, x[0])
        )
    ]
    return {
        "source": str(cards_path.resolve()),
        "examined": counts["examined"],
        "art_series_excluded": counts["art_series_excluded"],
        "normalized": counts["normalized"],
        "normalization_failures": counts["normalization_failures"],
        "distinct_failure_groups": len(rows),
        "failures": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cards", type=Path, metavar="FILE",
        help="Use an existing default-cards.jsonl.gz (otherwise download fresh Scryfall exports)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("out/carddb-source-audit.json"),
        help="Save the complete JSON report (default: out/carddb-source-audit.json)",
    )
    parser.add_argument("--examples", type=int, default=3, help="Examples retained per error (default 3)")
    parser.add_argument("--show", type=int, default=40, help="Maximum error groups printed (default 40)")
    args = parser.parse_args()
    if args.examples < 1 or args.show < 0:
        parser.error("--examples must be >= 1 and --show must be >= 0")

    print("Loading CardDB's current Scryfall type catalogs...", flush=True)
    vocabulary = fetch_reference_data().vocabulary

    with TemporaryDirectory(prefix="carddb-source-audit-") as temporary:
        if args.cards is None:
            print("Downloading current Scryfall bulk exports for audit...", flush=True)
            # Reuse CardDB's verified downloader; the tag export is harmlessly
            # downloaded alongside default_cards, but is not needed for parsing.
            bulk = acquire_bulk_sources(Path(temporary) / "bulk")
            cards_path = bulk.cards_path
        else:
            cards_path = args.cards.expanduser().resolve()
            if not cards_path.is_file():
                parser.error(f"card export does not exist: {cards_path}")

        print(f"Auditing every printing in {cards_path.name}...", flush=True)
        report = audit(cards_path, vocabulary, example_limit=args.examples)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(
        f"Printings: {report['examined']:,}; "
        f"skipped art-series: {report['art_series_excluded']:,}; "
        f"normalized: {report['normalized']:,}; "
        f"failures: {report['normalization_failures']:,} "
        f"in {report['distinct_failure_groups']} group(s)"
    )
    for entry in report["failures"][:args.show]:
        example = entry["examples"][0]
        print(f"\n{entry['count']:>5} × {entry['error']}")
        print(f"       Example: {example['card_name']!r} [{example['set']}] (printing {example['printing_id']})")
        print(f"       Type: {example['type_line']!r}; faces: {example['face_type_lines']!r}")

    print(f"\nFull report: {args.output.resolve()}")
    print("Database files were not modified.")


if __name__ == "__main__":
    main()
