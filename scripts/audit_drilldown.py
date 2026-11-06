"""Diagnose unusual source types, legality conflicts and missing tag IDs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from mtg_carddb.bulk import iter_default_cards
from mtg_carddb.oracle_tags import iter_oracle_tags


EXAMPLE_IDS = {
    "00185f3b-2777-4417-a8e0-4691f41c0ec1",
    "00358a89-2058-448b-a2fd-389f639ba2da",
    "004584f8-fd89-4a3e-9782-c117dbe1532b",
}


def oracle_ids(printing):
    if printing.get("layout") == "reversible_card":
        return {
            face["oracle_id"]
            for face in printing.get("card_faces", [])
            if isinstance(face, dict)
            and isinstance(face.get("oracle_id"), str)
        }

    value = printing.get("oracle_id")
    return {value} if isinstance(value, str) else set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True)
    args = parser.parse_args()

    raw_ids = set()
    layout_types = Counter()
    reverse_same = reverse_distinct = 0
    baseline = {}
    disagreements = defaultdict(set)
    examples = defaultdict(list)
    selected = defaultdict(list)

    for printing in iter_default_cards(args.cards):
        layout = printing.get("layout")
        layout_types[(layout, printing.get("type_line"))] += 1

        ids = oracle_ids(printing)
        raw_ids.update(ids)

        if layout == "reversible_card":
            faces = printing.get("card_faces", [])
            if len(ids) == 1 and len(faces) == 2:
                reverse_same += 1
            else:
                reverse_distinct += 1
            continue

        legal = printing.get("legalities")
        if not isinstance(legal, dict):
            continue

        # Match the current representative selector's
        # broad printing-eligibility exclusion.
        if (
            printing.get("border_color") == "gold"
            or printing.get("set_type") == "memorabilia"
        ):
            continue

        for oracle_id in ids:
            if oracle_id in EXAMPLE_IDS:
                selected[oracle_id].append((
                    printing.get("id"),
                    printing.get("set"),
                    legal,
                ))

            if oracle_id not in baseline:
                baseline[oracle_id] = (
                    legal, printing.get("id")
                )
                continue

            earlier, earlier_print = baseline[oracle_id]

            for fmt in earlier.keys() | legal.keys():
                if earlier.get(fmt) != legal.get(fmt):
                    disagreements[oracle_id].add(fmt)

                    if len(examples[fmt]) < 3:
                        examples[fmt].append((
                            oracle_id,
                            earlier_print,
                            earlier.get(fmt),
                            printing.get("id"),
                            legal.get(fmt),
                        ))

    print(
        f"Raw Oracle IDs in default_cards: {len(raw_ids):,}"
    )
    print(
        f"Reversible printings with shared ID: {reverse_same:,}"
    )
    print(
        f"Reversible printings with distinct IDs: {reverse_distinct:,}"
    )

    print("\nNon-standard source type lines:")

    unusual = [
        ((layout, type_line), count)
        for (layout, type_line), count
        in layout_types.most_common()
        if type_line in {"Card", "Event", "Boss"}
        or (
            isinstance(type_line, str)
            and (
                type_line.startswith("Plane — ")
                or "Cyborg" in type_line
            )
        )
    ]

    for (layout, type_line), count in unusual[:25]:
        print(
            f"  {count:>5,}  {layout:20} {type_line}"
        )

    print(
        f"  ... {len(unusual)} unusual "
        "layout/type-line combinations"
    )

    formats = Counter(
        fmt
        for differences in disagreements.values()
        for fmt in differences
    )

    print(
        "\nIDs with cross-printing legality differences: "
        f"{len(disagreements):,}"
    )

    for fmt, count in formats.most_common():
        print(
            f"  {fmt:22} {count:>6,} identities"
        )
        for example in examples[fmt][:2]:
            print(f"    {example}")

    for oracle_id in sorted(EXAMPLE_IDS):
        entries = selected[oracle_id]
        if entries:
            print(
                f"\nSample {oracle_id} "
                f"({len(entries)} eligible printings)"
            )

            varying = disagreements[oracle_id]

            for printing_id, set_code, legal in entries[:10]:
                statuses = {
                    fmt: legal.get(fmt)
                    for fmt in sorted(varying)
                }
                print(
                    f"  {printing_id} [{set_code}] {statuses}"
                )

    missing = Counter()
    missing_unique = set()
    missing_sample = []
    assignments = 0

    for tag in iter_oracle_tags(args.tags):
        for assignment in tag.get("taggings", []):
            assignments += 1
            oracle_id = assignment.get("oracle_id")

            if oracle_id not in raw_ids:
                missing[tag.get("slug")] += 1
                missing_unique.add(oracle_id)

                if len(missing_sample) < 12:
                    missing_sample.append((
                        tag.get("slug"), oracle_id
                    ))

    print(
        f"\nTag assignments examined: {assignments:,}"
    )
    print(
        "Assignments absent from ALL raw printings: "
        f"{sum(missing.values()):,}"
    )
    print(
        "Distinct absent Oracle IDs: "
        f"{len(missing_unique):,}"
    )
    print(
        "Most affected tags:",
        missing.most_common(12),
    )
    print("Examples:", missing_sample)


if __name__ == "__main__":
    main()
