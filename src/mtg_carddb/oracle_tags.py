"""Read and import Scryfall's Oracle Tag bulk export into a staging database.

Only direct taggings are stored. Parent/child memberships are expanded by the
public query API; they are never written as inferred card_tags rows.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonl_limits import (
    JSONLSizeError,
    iter_bounded_gzip_lines,
)

class OracleTagFormatError(ValueError):
    """The Oracle Tag source has invalid or inconsistent content."""


class OracleTagReadError(RuntimeError):
    """The Oracle Tag file could not be read or fully decompressed."""


@dataclass(frozen=True, slots=True)
class OracleTagStats:
    tags: int
    aliases: int
    relationships: int
    taggings: int


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def iter_oracle_tags(
    path: str | Path,
) -> Iterator[dict[str, Any]]:
    """Yield Oracle Tag records from bounded gzip JSONL.

    Exhaustion verifies the gzip checksum and trailer.
    """
    if not isinstance(path, (str, Path)):
        raise TypeError(
            "path must be a string or Path"
        )

    source = Path(path).expanduser()
    line_no = 0
    count = 0

    try:
        with source.open("rb") as stream:
            if stream.read(2) != b"\x1f\x8b":
                raise OracleTagFormatError(
                    f"{source}: expected "
                    "gzip-compressed JSON Lines"
                )

        for line_no, line in iter_bounded_gzip_lines(
            source
        ):
            if not line.strip():
                raise OracleTagFormatError(
                    f"{source}: blank JSONL record "
                    f"on line {line_no}"
                )

            try:
                obj = json.loads(
                    line,
                    parse_constant=_reject_constant,
                )
            except (
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                raise OracleTagFormatError(
                    f"{source}: invalid JSON "
                    f"on line {line_no}: {exc}"
                ) from exc

            if (
                not isinstance(obj, dict)
                or obj.get("object") != "tag"
            ):
                raise OracleTagFormatError(
                    f"{source}: line {line_no} "
                    "is not a Scryfall Tag object"
                )

            count += 1
            yield obj

        if not count:
            raise OracleTagFormatError(
                f"{source}: no tag records found"
            )

    except JSONLSizeError as exc:
        raise OracleTagFormatError(
            f"{source}: {exc}"
        ) from exc

    except (
        OSError,
        EOFError,
        zlib.error,
        UnicodeError,
    ) as exc:
        raise OracleTagReadError(
            f"could not read {source} "
            f"near line {line_no}: {exc}"
        ) from exc


def _required(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OracleTagFormatError(f"{where}: missing/invalid {key}")
    return value


def _optional(data: Mapping[str, Any], key: str, where: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise OracleTagFormatError(f"{where}: invalid {key}")
    return value


def _list_of_strings(
    data: Mapping[str, Any], key: str, where: str, *, required: bool = False
) -> tuple[tuple[str, ...], bool]:
    """Return items and whether the source explicitly supplied the list."""
    value = data.get(key)
    if value is None and not required:
        return (), False
    if (not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    )):
        raise OracleTagFormatError(f"{where}: invalid {key}")
    if len(set(value)) != len(value):
        raise OracleTagFormatError(f"{where}: duplicate entries in {key}")
    return tuple(value), True


def _validate_acyclic(
    ids: set[str], edges: list[tuple[str, str]]
) -> None:
    """Kahn's algorithm, iterative and linear in the size of the tag DAG."""
    children: dict[str, list[str]] = {tag_id: [] for tag_id in ids}
    indegree = dict.fromkeys(ids, 0)
    for parent, child in edges:
        children[parent].append(child)
        indegree[child] += 1
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        parent = queue.popleft()
        visited += 1
        for child in children[parent]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(ids):
        cyclic = sorted(key for key, degree in indegree.items() if degree > 0)
        raise OracleTagFormatError(
            f"Oracle Tag hierarchy contains a cycle involving {cyclic[:5]!r}"
        )


def ingest_oracle_tags(
    connection: sqlite3.Connection,
    tags: Iterable[Mapping[str, Any]],
) -> OracleTagStats:
    """Import a stream of Oracle tags into an already card-populated v1 DB.

    Expects foreign keys enabled and all four tag tables empty. A savepoint
    makes the whole operation atomic without committing the caller's work.
    Temporary relationship claims permit tags to arrive in arbitrary order.
    The Python memory footprint is proportional to tag IDs/aliases/edges,
    not to all card-tag assignments.
    """
    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise OracleTagFormatError("expected CardDB SQLite schema version 1")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise OracleTagFormatError("foreign key enforcement must be enabled")
    for table in ("tags", "tag_aliases", "tag_relationships", "card_tags"):
        if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise OracleTagFormatError(f"{table} is already populated")

    connection.execute("SAVEPOINT carddb_oracle_tags")
    try:
        # Each DDL statement runs inside the caller-preserving savepoint.
        # Never use executescript() here: it implicitly commits transactions.
        connection.execute("""
            CREATE TEMP TABLE carddb_tag_claims (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                from_parent INTEGER NOT NULL DEFAULT 0,
                from_child INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (parent_id, child_id)
            ) WITHOUT ROWID
        """)
        connection.execute("""
            CREATE TEMP TABLE carddb_tag_declarations (
                tag_id TEXT PRIMARY KEY,
                has_parents INTEGER NOT NULL,
                has_children INTEGER NOT NULL
            ) WITHOUT ROWID
        """)

        canonical: dict[str, str] = {}
        aliases: dict[str, str] = {}
        tag_ids: set[str] = set()
        n_tags = n_aliases = n_taggings = 0
        for obj in tags:
            if not isinstance(obj, Mapping) or obj.get("object") != "tag":
                raise OracleTagFormatError("expected Scryfall Tag object")
            if obj.get("type") != "oracle":
                raise OracleTagFormatError("oracle_tags contains a non-Oracle tag")
            tag_id = _required(obj, "id", "tag")
            slug = _required(obj, "slug", tag_id)
            label = _required(obj, "label", tag_id)
            description = _optional(obj, "description", tag_id)
            parent_ids, has_parents = _list_of_strings(obj, "parent_ids", slug)
            child_ids, has_children = _list_of_strings(obj, "child_ids", slug)
            alias_names, _ = _list_of_strings(obj, "aliases", slug)
            taggings = obj.get("taggings")
            if not isinstance(taggings, list):
                raise OracleTagFormatError(f"{slug}: missing/invalid taggings")

            folded = slug.casefold()
            if folded in canonical or (
                folded in aliases and aliases[folded] != tag_id
            ):
                raise OracleTagFormatError(f"duplicate/colliding tag slug {slug!r}")
            canonical[folded] = tag_id
            if tag_id in tag_ids:
                raise OracleTagFormatError(f"duplicate tag ID {tag_id!r}")
            tag_ids.add(tag_id)

            connection.execute(
                "INSERT INTO tags (tag_id, slug, label, description) "
                "VALUES (?, ?, ?, ?)",
                (tag_id, slug, label, description),
            )
            connection.execute(
                "INSERT INTO temp.carddb_tag_declarations "
                "VALUES (?, ?, ?)",
                (tag_id, int(has_parents), int(has_children)),
            )
            for alias in alias_names:
                key = alias.casefold()
                if (key in aliases and aliases[key] != tag_id) or (
                    key in canonical and canonical[key] != tag_id
                ):
                    raise OracleTagFormatError(f"colliding alias {alias!r}")
                # Same-target alias with different case is also ambiguous data.
                if key in aliases:
                    raise OracleTagFormatError(f"duplicate alias {alias!r}")
                aliases[key] = tag_id
                connection.execute(
                    "INSERT INTO tag_aliases (tag_id, alias) VALUES (?, ?)",
                    (tag_id, alias),
                )
                n_aliases += 1

            for parent in parent_ids:
                connection.execute("""
                    INSERT INTO temp.carddb_tag_claims
                    (parent_id, child_id, from_child)
                    VALUES (?, ?, 1)
                    ON CONFLICT(parent_id, child_id)
                    DO UPDATE SET from_child = 1
                """, (parent, tag_id))
            for child in child_ids:
                connection.execute("""
                    INSERT INTO temp.carddb_tag_claims
                    (parent_id, child_id, from_parent)
                    VALUES (?, ?, 1)
                    ON CONFLICT(parent_id, child_id)
                    DO UPDATE SET from_parent = 1
                """, (tag_id, child))

            for tagging in taggings:
                if not isinstance(tagging, Mapping):
                    raise OracleTagFormatError(f"{slug}: invalid tagging object")
                oracle_id = _required(tagging, "oracle_id", slug)
                weight = _required(tagging, "weight", slug)
                annotation = _optional(tagging, "annotation", slug)
                if tagging.get("illustration_id") is not None:
                    raise OracleTagFormatError(
                        f"{slug}: illustration tagging in Oracle export"
                    )
                try:
                    connection.execute(
                        "INSERT INTO card_tags (tag_id, oracle_id, weight, annotation) "
                        "VALUES (?, ?, ?, ?)",
                        (tag_id, oracle_id, weight, annotation),
                    )
                except sqlite3.IntegrityError as exc:
                    raise OracleTagFormatError(
                        f"{slug}: unknown or duplicate Oracle ID {oracle_id!r}"
                    ) from exc
                n_taggings += 1
            n_tags += 1

        if not n_tags:
            raise OracleTagFormatError("empty Oracle Tag export")

        claims = connection.execute(
            "SELECT parent_id, child_id, from_parent, from_child "
            "FROM temp.carddb_tag_claims ORDER BY parent_id, child_id"
        ).fetchall()
        declarations = {
            tag_id: (bool(parents), bool(children))
            for tag_id, parents, children in connection.execute(
                "SELECT tag_id, has_parents, has_children "
                "FROM temp.carddb_tag_declarations"
            )
        }
        edges: list[tuple[str, str]] = []
        for parent, child, from_parent, from_child in claims:
            if parent not in tag_ids or child not in tag_ids:
                raise OracleTagFormatError(
                    f"unknown tag in relationship {parent!r} -> {child!r}"
                )
            if parent == child:
                raise OracleTagFormatError(f"self-referential tag {parent!r}")
            # When the opposite tag explicitly advertises a complete list,
            # the edge must appear in that list too. Omitted lists are unknown.
            if declarations[parent][1] and not from_parent:
                raise OracleTagFormatError(
                    f"inconsistent child_ids for parent {parent!r} -> {child!r}"
                )
            if declarations[child][0] and not from_child:
                raise OracleTagFormatError(
                    f"inconsistent parent_ids for parent {parent!r} -> {child!r}"
                )
            edges.append((parent, child))

        _validate_acyclic(tag_ids, edges)
        connection.executemany(
            "INSERT INTO tag_relationships (parent_id, child_id) VALUES (?, ?)",
            edges,
        )
        connection.execute("DROP TABLE temp.carddb_tag_claims")
        connection.execute("DROP TABLE temp.carddb_tag_declarations")
        connection.execute("RELEASE SAVEPOINT carddb_oracle_tags")
        return OracleTagStats(n_tags, n_aliases, len(edges), n_taggings)
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT carddb_oracle_tags")
        connection.execute("RELEASE SAVEPOINT carddb_oracle_tags")
        raise
