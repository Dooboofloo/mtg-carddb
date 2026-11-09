"""Bounded-memory staging of local Scryfall default_cards printings.

Printing candidates and historical observations live in TEMP SQLite tables.
Only selected Oracle records and deduplicated historical facts survive.
Sets must have been populated from an authoritative catalog beforehand.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .models import CardFace, Legality
from .normalization import Candidate, NormalizationError, TypeVocabulary, normalize_printing
from .persistence import write_representative
from .representatives import select_representative


class IngestionError(ValueError):
    """Input set/printing facts cannot be imported without losing correctness."""


@dataclass(frozen=True, slots=True)
class IngestionStats:
    printings: int
    candidates: int
    oracle_cards: int
    historical_observations: int
    excluded_printings: int = 0


def _json_default(value: object) -> list[str]:
    if isinstance(value, frozenset):
        return sorted(value)
    raise TypeError(f"cannot encode candidate value of type {type(value).__name__}")


def _encode(candidate: Candidate) -> str:
    return json.dumps(
        asdict(candidate),
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _decode(payload: str) -> Candidate:
    raw = json.loads(payload)
    for field in ("colors", "color_identity", "produced_mana", "keywords"):
        if raw[field] is not None:
            raw[field] = frozenset(raw[field])
    faces = []
    for face in raw["faces"]:
        for field in ("colors", "color_indicator", "supertypes", "types", "subtypes"):
            if face[field] is not None:
                face[field] = frozenset(face[field])
        faces.append(CardFace(**face))
    raw["faces"] = tuple(faces)
    if raw["legalities"] is not None:
        raw["legalities"] = tuple(Legality(**item) for item in raw["legalities"])
    return Candidate(**raw)


def _required_text(printing: Mapping[str, Any], field: str) -> str:
    value = printing.get(field)
    if not isinstance(value, str) or not value:
        raise IngestionError(
            f"printing {printing.get('id', '<missing>')!r}: missing/invalid {field}"
        )
    return value


def _history(
    printing: Mapping[str, Any],
    identities: frozenset[str],
) -> Iterable[tuple[str, str, str, str, str]]:
    set_id = _required_text(printing, "set_id")
    set_code = _required_text(printing, "set")
    rarity = _required_text(printing, "rarity")
    games = printing.get("games")
    if (
        not isinstance(games, list)
        or any(
            not isinstance(game, str) or not game
            for game in games
        )
    ):
        raise IngestionError(
            f"printing {printing.get('id', '<missing>')!r}: invalid games"
        )
    for oracle_id in sorted(identities):
        for game in sorted(set(games)):
            yield oracle_id, set_id, set_code, rarity, game


def ingest_printings(
    connection: sqlite3.Connection,
    printings: Iterable[dict[str, Any]],
    vocabulary: TypeVocabulary,
) -> IngestionStats:
    """Consume a printing stream once; persist normalized Oracle cards and history.

    Requires a writable schema-v1 staging database with authoritative `sets`
    already inserted. All effects are one savepoint: a failure rolls back every
    card/history row from this call. Temporary printing rows are always removed.

    Memory scales with the largest *one Oracle ID's* candidate group, not the
    total export. No import source metadata is invented here.
    """
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise IngestionError("foreign key enforcement must be enabled")
    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise IngestionError("expected a writable CardDB schema v1 database")

    connection.execute("SAVEPOINT carddb_ingest")
    try:
        # Do not use executescript(): it commits an active transaction and
        # would silently invalidate the enclosing rollback savepoint.
        connection.execute("""
            CREATE TEMP TABLE carddb_candidate_spool (
                oracle_id TEXT NOT NULL,
                printing_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (oracle_id, printing_id)
            ) WITHOUT ROWID
        """)
        connection.execute("""
            CREATE TEMP TABLE carddb_history_spool (
                oracle_id TEXT NOT NULL,
                set_id TEXT NOT NULL,
                set_code TEXT NOT NULL,
                rarity TEXT NOT NULL,
                game TEXT NOT NULL,
                PRIMARY KEY (oracle_id, set_id, set_code, rarity, game)
            ) WITHOUT ROWID
        """)
        printing_count = 0
        candidate_count = 0
        excluded_count = 0

        for printing in printings:
            printing_count += 1

            if printing.get("layout") == "art_series":
                excluded_count += 1
                continue

            candidates = normalize_printing(printing, vocabulary)
            candidate_count += len(candidates)
            connection.executemany(
                "INSERT INTO temp.carddb_candidate_spool "
                "(oracle_id, printing_id, payload) VALUES (?, ?, ?)",
                (
                    (candidate.oracle_id, candidate.printing_id, _encode(candidate))
                    for candidate in candidates
                ),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO temp.carddb_history_spool "
                "(oracle_id, set_id, set_code, rarity, game) VALUES (?, ?, ?, ?, ?)",
                _history(printing, frozenset(c.oracle_id for c in candidates)),
            )

        if printing_count == 0:
            raise IngestionError("empty default_cards printing stream")

        if candidate_count == 0:
            raise IngestionError("no importable cards after art_series exclusion")

        # Validate canonical set IDs *and* codes; no invented set metadata.
        invalid_set = connection.execute("""
            SELECT h.set_id, h.set_code FROM temp.carddb_history_spool AS h
            LEFT JOIN main.sets AS s
                ON s.set_id = h.set_id AND s.code = h.set_code
            WHERE s.set_id IS NULL LIMIT 1
        """).fetchone()
        if invalid_set is not None:
            raise IngestionError(
                f"set ID/code not found in authoritative catalog: "
                f"{invalid_set[0]!r} / {invalid_set[1]!r}"
            )

        card_count = 0
        for (oracle_id,) in connection.execute(
            "SELECT DISTINCT oracle_id FROM temp.carddb_candidate_spool "
            "ORDER BY oracle_id"
        ):
            group = (
                _decode(row[0])
                for row in connection.execute(
                    "SELECT payload FROM temp.carddb_candidate_spool "
                    "WHERE oracle_id = ? ORDER BY printing_id",
                    (oracle_id,),
                )
            )
            write_representative(connection, select_representative(group))
            card_count += 1

        # Oracle cards must be inserted before historical foreign keys.
        connection.execute("""
            INSERT INTO main.card_set_rarities (oracle_id, set_id, rarity, game)
            SELECT oracle_id, set_id, rarity, game
            FROM temp.carddb_history_spool
        """)
        observations = connection.execute(
            "SELECT COUNT(*) FROM temp.carddb_history_spool"
        ).fetchone()[0]

        connection.execute("DROP TABLE temp.carddb_candidate_spool")
        connection.execute("DROP TABLE temp.carddb_history_spool")
        connection.execute("RELEASE SAVEPOINT carddb_ingest")
        return IngestionStats(printing_count, candidate_count, card_count, observations, excluded_count)
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT carddb_ingest")
        connection.execute("RELEASE SAVEPOINT carddb_ingest")
        raise
