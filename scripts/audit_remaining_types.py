
"""Profile remaining type failures and Oracle tag references."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from mtg_carddb.bulk import iter_default_cards
from mtg_carddb.catalogs import fetch_reference_data
from mtg_carddb.normalization import normalize_printing
from mtg_carddb.oracle_tags import iter_oracle_tags


def source_ids(printing: dict) -> set[str]:
    if printing.get("layout") == "reversible_card":
        return {
            face["oracle_id"]
            for face in printing.get("card_faces", [])
            if isinstance(face, dict)
            and isinstance(face.get("oracle_id"), str)
        }

    oracle_id = printing.get("oracle_id")
    return {oracle_id} if isinstance(oracle_id, str) else set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True)
    args = parser.parse_args()

    vocabulary = fetch_reference_data().vocabulary

    all_raw_ids = set()
    art_ids = set()
    non_art_ids = set()
    normalized_ids = set()
    complete_ids = set()

    errors = Counter()
    examples = defaultdict(list)

    total = excluded = normalized_printings = 0

    for printing in iter_default_cards(args.cards):
        total += 1

        ids = source_ids(printing)
        all_raw_ids.update(ids)
        layout = str(printing.get("layout"))

        if layout == "art_series":
            excluded += 1
            art_ids.update(ids)
            continue

        non_art_ids.update(ids)

        try:
            candidates = normalize_printing(
                printing, vocabulary
            )
        except Exception as exc:
            message = str(exc).replace(
                str(printing.get("id")), "<printing-id>"
            )
            key = (
                layout,
                str(printing.get("type_line")),
                f"{type(exc).__name__}: {message}",
            )
            errors[key] += 1

            if len(examples[key]) < 2:
                examples[key].append(printing.get("id"))

            continue

        normalized_printings += 1

        for candidate in candidates:
            normalized_ids.add(candidate.oracle_id)

            if not candidate.missing:
                complete_ids.add(candidate.oracle_id)

    print(f"Printings examined: {total:,}")
    print(f"Art-series printings excluded: {excluded:,}")
    print(f"Printings normalized: {normalized_printings:,}")
    print(f"Normalization failures: {sum(errors.values()):,}")
    print(f"Raw Oracle IDs: {len(all_raw_ids):,}")
    print(f"Non-art Oracle IDs: {len(non_art_ids):,}")
    print(f"IDs with normalized candidates: {len(normalized_ids):,}")
    print(f"IDs with complete mechanics: {len(complete_ids):,}")

    print("\nTop remaining normalization failures:")

    for key, count in errors.most_common(40):
        layout, type_line, error = key
        print(
            f"{count:>5,} | {layout} | "
            f"{type_line!r} | {error}"
        )
        print(f"         Examples: {examples[key]}")

    categories = Counter()
    affected_tags = defaultdict(Counter)
    samples = defaultdict(list)
    assignments = 0

    for tag in iter_oracle_tags(args.tags):
        for tagging in tag.get("taggings", []):
            assignments += 1
            oracle_id = tagging.get("oracle_id")

            if oracle_id not in all_raw_ids:
                category = "absent from raw export"
            elif (
                oracle_id in art_ids
                and oracle_id not in non_art_ids
            ):
                category = "art-series only"
            elif oracle_id not in normalized_ids:
                category = "normalization failed"
            elif oracle_id not in complete_ids:
                category = "no complete mechanics candidate"
            else:
                category = "complete candidate available"

            categories[category] += 1
            affected_tags[category][tag.get("slug")] += 1

            if len(samples[category]) < 5:
                samples[category].append((
                    tag.get("slug"), oracle_id
                ))

    print(f"\nDirect tag assignments: {assignments:,}")

    for category, count in categories.most_common():
        print(f"\n{count:>8,} | {category}")
        print(
            "Top tags:",
            affected_tags[category].most_common(5),
        )
        print("Examples:", samples[category])


if __name__ == "__main__":
    main()
