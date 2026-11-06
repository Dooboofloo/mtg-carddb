"""Export only the Scryfall printings that currently fail normalization."""

import argparse
import json
from collections import Counter
from pathlib import Path

from mtg_carddb.bulk import iter_default_cards
from mtg_carddb.catalogs import fetch_reference_data
from mtg_carddb.normalization import normalize_printing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mtg-carddb-edge-cases.jsonl"),
    )
    args = parser.parse_args()

    vocabulary = fetch_reference_data().vocabulary
    failures = Counter()

    # Exclusive creation prevents accidental overwrites.
    with args.output.open("x", encoding="utf-8") as output:
        for printing in iter_default_cards(args.cards):
            if printing.get("layout") == "art_series":
                continue

            try:
                normalize_printing(printing, vocabulary)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                failures[type(exc).__name__] += 1

                output.write(json.dumps({
                    "error": reason,
                    "printing": printing,
                }, ensure_ascii=False) + "\n")

    print("Failed printings:", sum(failures.values()))
    print("Failure classes:", dict(failures))
    print("Output:", args.output.resolve())
    print("Bytes:", args.output.stat().st_size)


if __name__ == "__main__":
    main()
