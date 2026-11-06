"""Audit real Scryfall exports without modifying a published database."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from mtg_carddb.bulk import iter_default_cards
from mtg_carddb.catalogs import fetch_reference_data, write_sets
from mtg_carddb.database import CardDatabase
from mtg_carddb.ingestion import _decode, _encode, ingest_printings
from mtg_carddb.normalization import normalize_printing
from mtg_carddb.oracle_tags import ingest_oracle_tags, iter_oracle_tags
from mtg_carddb.representatives import select_representative
from mtg_carddb.schema import staging_database


HEADERS = {
    "User-Agent": (
        "mtg-carddb-real-data-audit/0.1 "
        "(+https://github.com/Dooboofloo/mtg-carddb)"
    ),
    "Accept": "application/json",
}


def check_url(url: str, host: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"unexpected Scryfall URL: {url}")


def get_json(url: str) -> dict:
    check_url(url, "api.scryfall.com")
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=40) as response:
        check_url(response.geturl(), "api.scryfall.com")
        result = json.load(response)

    if not isinstance(result, dict):
        raise ValueError(f"invalid metadata from {url}")
    return result


def bulk_source(kind: str, cache: Path) -> Path:
    metadata = get_json(
        f"https://api.scryfall.com/bulk-data/"
        f"{kind.replace('_', '-')}"
    )

    if (
        metadata.get("object") != "bulk_data"
        or metadata.get("type") != kind
    ):
        raise ValueError(f"unexpected bulk metadata for {kind}")

    url = metadata.get("jsonl_download_uri")
    if not isinstance(url, str):
        raise ValueError(f"{kind}: missing jsonl_download_uri")

    check_url(url, "data.scryfall.io")
    filename = Path(urlparse(url).path).name

    if not filename.endswith(".jsonl.gz"):
        raise ValueError(f"unexpected bulk filename: {filename}")

    cache.mkdir(parents=True, exist_ok=True)
    target = cache / filename

    if target.exists():
        print(f"Using cached {kind}: {target}")
        return target

    partial = cache / (filename + ".partial")
    partial.unlink(missing_ok=True)

    print(f"Downloading {kind} ...", flush=True)

    try:
        request = Request(url, headers=HEADERS)

        with urlopen(request, timeout=120) as response:
            check_url(response.geturl(), "data.scryfall.io")

            with partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)

        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)

    return target


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def record_failure(
    counts: Counter,
    examples: dict[str, list[str]],
    exc: Exception,
    context: str,
) -> None:
    message = str(exc)
    reason = message.partition(": ")[2] or message

    # Remove variable printing IDs from common conflict messages.
    reason = reason.split(" in printings ", 1)[0]
    reason = reason.split(
        " between equally eligible printings ", 1
    )[0]

    if reason.startswith("no complete mechanics candidate"):
        reason = "no complete mechanics candidate"

    category = f"{type(exc).__name__}: {reason[:150]}"
    counts[category] += 1

    if len(examples[category]) < 3:
        examples[category].append(context)


def report_failures(
    title: str,
    counts: Counter,
    examples: dict[str, list[str]],
) -> None:
    print(f"\n{title}: {sum(counts.values()):,} failure(s)")

    for reason, count in counts.most_common(15):
        print(f"  {count:>7,}  {reason}")

        for example in examples[reason]:
            print(f"             e.g. {example}")


def audit_cards(
    path: Path,
    vocabulary,
    spool: Path,
):
    """Check every printing and every Oracle group."""

    conn = sqlite3.connect(spool)
    conn.execute(
        "CREATE TABLE candidates ("
        "oracle_id TEXT, printing_id TEXT, payload TEXT)"
    )

    layouts = Counter()
    failures = Counter()
    examples = defaultdict(list)
    missing_fields = Counter()

    identities: set[str] = set()
    printing_count = 0
    candidate_count = 0

    for printing in iter_default_cards(path):
        printing_count += 1
        layout = str(printing.get("layout", "<missing>"))
        layouts[layout] += 1

        try:
            candidates = normalize_printing(
                printing, vocabulary
            )

            for candidate in candidates:
                identities.add(candidate.oracle_id)
                candidate_count += 1

                for field in candidate.missing:
                    key = (
                        "reversible" if candidate.reversible else layout,
                        field,
                    )
                    missing_fields[key] += 1

                conn.execute(
                    "INSERT INTO candidates VALUES (?, ?, ?)",
                    (
                        candidate.oracle_id,
                        candidate.printing_id,
                        _encode(candidate),
                    ),
                )

        except Exception as exc:
            record_failure(
                failures,
                examples,
                exc,
                (
                    f"{printing.get('id')} "
                    f"{printing.get('name')} [{layout}]"
                ),
            )

        if printing_count % 25000 == 0:
            print(
                f"Examined {printing_count:,} printings",
                flush=True,
            )

    conn.commit()
    conn.execute(
        "CREATE INDEX candidate_oracle "
        "ON candidates(oracle_id, printing_id)"
    )

    print(f"\nPrintings examined: {printing_count:,}")
    print(f"Candidates created: {candidate_count:,}")
    print(f"Distinct Oracle IDs: {len(identities):,}")
    print("Layouts:", layouts.most_common(20))
    print(
        "Missing required candidate fields:",
        missing_fields.most_common(12),
    )

    report_failures(
        "Printing normalization",
        failures,
        examples,
    )

    selection_failures = Counter()
    selection_examples = defaultdict(list)
    selected = 0

    for (oracle_id,) in conn.execute(
        "SELECT DISTINCT oracle_id "
        "FROM candidates ORDER BY oracle_id"
    ):
        group = (
            _decode(row[0])
            for row in conn.execute(
                "SELECT payload FROM candidates "
                "WHERE oracle_id = ? ORDER BY printing_id",
                (oracle_id,),
            )
        )

        try:
            select_representative(group)
            selected += 1

        except Exception as exc:
            record_failure(
                selection_failures,
                selection_examples,
                exc,
                oracle_id,
            )

    conn.close()

    print(
        f"\nSelected Oracle identities: "
        f"{selected:,}/{len(identities):,}"
    )

    report_failures(
        "Representative selection",
        selection_failures,
        selection_examples,
    )

    return identities, failures, selection_failures


def audit_tags(path: Path, identities: set[str]) -> None:
    """Check source tagging values and unresolved Oracle IDs."""

    tag_count = 0
    assignment_count = 0
    unknown_count = 0
    unknown_examples = []

    weights = Counter()
    types = Counter()

    for tag in iter_oracle_tags(path):
        tag_count += 1
        types[tag.get("type", "<missing>")] += 1

        taggings = tag.get("taggings")

        if not isinstance(taggings, list):
            print(
                f"Invalid taggings field: {tag.get('id')}"
            )
            continue

        for tagging in taggings:
            assignment_count += 1

            if not isinstance(tagging, dict):
                continue

            weights[str(
                tagging.get("weight", "<missing>")
            )] += 1

            oracle_id = tagging.get("oracle_id")

            if oracle_id not in identities:
                unknown_count += 1

                if len(unknown_examples) < 10:
                    unknown_examples.append(
                        (tag.get("slug"), oracle_id)
                    )

    print(f"\nOracle tags: {tag_count:,}")
    print(f"Direct assignments: {assignment_count:,}")
    print("Tag types:", types)
    print("Tag weights:", weights)
    print(f"Unresolved Oracle IDs: {unknown_count:,}")

    for example in unknown_examples:
        print("  ", example)


def try_full_import(
    cards: Path,
    tags: Path,
    references,
    workspace: Path,
) -> None:
    """Attempt actual ingestion, never publishing the result."""

    target = workspace / "audit-staging.sqlite"
    print("\nAttempting full staging import ...", flush=True)

    try:
        with staging_database(target) as conn:
            set_count = write_sets(
                conn, references.sets
            )

            card_stats = ingest_printings(
                conn,
                iter_default_cards(cards),
                references.vocabulary,
            )

            print(f"Sets: {set_count}")
            print(f"Card ingestion: {card_stats}", flush=True)

            tag_stats = ingest_oracle_tags(
                conn,
                iter_oracle_tags(tags),
            )

            print(f"Tag ingestion: {tag_stats}", flush=True)

            conn.execute(
                "INSERT INTO database_metadata "
                "VALUES (1, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "audit-only",
                ),
            )

            integrity = conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            foreign_keys = conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

            print(f"SQLite integrity: {integrity}")
            print(
                f"Foreign-key violations: {len(foreign_keys)}"
            )

            if integrity != "ok" or foreign_keys:
                raise RuntimeError(
                    "database integrity validation failed"
                )

        with CardDatabase.open(target) as db:
            print(
                f"Public API loaded {len(db):,} Oracle cards"
            )
            print(
                "Red-green cards:",
                len(db.by_color("RG")),
            )
            print(
                "Creatures:",
                len(db.by_type("Creature")),
            )
            print(
                "Game Changers:",
                len(db.game_changers()),
            )

        print("\nFULL INGESTION SUCCESS")
        print("(Temporary audit database only)")

    except Exception as exc:
        print(
            f"\nFULL INGESTION FAILED: "
            f"{type(exc).__name__}: {exc}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--cards",
        type=Path,
        help="Existing default_cards .jsonl.gz",
    )
    parser.add_argument(
        "--tags",
        type=Path,
        help="Existing oracle_tags .jsonl.gz",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=(
            Path.home() / ".cache" / "mtg-carddb-audit"
        ),
    )

    args = parser.parse_args()

    print(
        "Fetching Scryfall set/type catalogs ...",
        flush=True,
    )
    references = fetch_reference_data()

    cards = args.cards or bulk_source(
        "default_cards", args.cache_dir
    )
    tags = args.tags or bulk_source(
        "oracle_tags", args.cache_dir
    )

    for path in (cards, tags):
        print(
            f"Source: {path} "
            f"({path.stat().st_size:,} bytes)"
        )
        print(f"SHA-256: {fingerprint(path)}")

    with tempfile.TemporaryDirectory(
        prefix="carddb-real-audit-"
    ) as directory:
        workspace = Path(directory)

        identities, norm_errors, selection_errors = audit_cards(
            cards,
            references.vocabulary,
            workspace / "candidate-audit.sqlite",
        )

        audit_tags(tags, identities)

        if norm_errors or selection_errors:
            print(
                "\nThe strict importer is expected to reject "
                "at least some of these records."
            )

        try_full_import(
            cards, tags, references, workspace
        )


if __name__ == "__main__":
    main()
