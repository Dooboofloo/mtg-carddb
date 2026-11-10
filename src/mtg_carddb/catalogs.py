"""Load Scryfall set metadata and type catalogs for a staging build.

Network acquisition is explicit. Parsing and SQLite persistence work offline.
No catalog values or HTTP responses become permanent printing records.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .normalization import TypeVocabulary
from .printed_types import (
    SUPPLEMENTAL_SUPERTYPES,
    SUPPLEMENTAL_CARD_TYPES,
    SUPPLEMENTAL_SUBTYPES,
)

SETS_URL = "https://api.scryfall.com/sets"
CATALOG_BASE = "https://api.scryfall.com/catalog/"

TYPE_CATALOGS = (
    "supertypes",
    "artifact-types",
    "creature-types",
    "enchantment-types",
    "land-types",
    "planeswalker-types",
    "spell-types",
)

# The documented Catalog API does not consistently advertise a general
# card-types catalog. This is parser vocabulary, not card data:
# actual types must still occur in each source printing's type_line.
CARD_TYPES = frozenset({
    "Artifact", "Battle", "Conspiracy", "Creature", "Dungeon",
    "Emblem", "Enchantment", "Hero", "Instant", "Kindred",
    "Land", "Phenomenon", "Plane", "Planeswalker", "Scheme",
    "Sorcery", "Stickers", "Token", "Tribal", "Vanguard",
    "Card", "Event", "Boss",
})

# Source-backed exceptions to the standard subtype catalogs.
ADDITIONAL_SUBTYPES = frozenset({
    "Siege",
    "Cyborg",
})


class CatalogError(ValueError):
    """A set or catalog response violates the import contract."""


class CatalogFetchError(RuntimeError):
    """An explicitly requested catalog could not be downloaded."""


@dataclass(frozen=True, slots=True)
class SetRecord:
    set_id: str
    code: str
    name: str
    set_type: str
    released_at: str | None
    digital: bool


@dataclass(frozen=True, slots=True)
class ReferenceData:
    sets: tuple[SetRecord, ...]
    vocabulary: TypeVocabulary


def _required_string(
    data: Mapping[str, Any],
    key: str,
    where: str,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}: missing/invalid {key}")
    return value


def _set_record(item: Any) -> SetRecord:
    if not isinstance(item, dict) or item.get("object") != "set":
        raise CatalogError("sets: expected a Scryfall Set object")

    set_id = _required_string(item, "id", "set")
    code = _required_string(item, "code", set_id)
    name = _required_string(item, "name", set_id)
    set_type = _required_string(item, "set_type", set_id)

    digital = item.get("digital")
    if type(digital) is not bool:
        raise CatalogError(f"{set_id}: digital must be a Boolean")

    released_at = item.get("released_at")
    if released_at is not None:
        if not isinstance(released_at, str):
            raise CatalogError(f"{set_id}: invalid released_at")

        try:
            if date.fromisoformat(released_at).isoformat() != released_at:
                raise ValueError("noncanonical date")
        except ValueError as exc:
            raise CatalogError(
                f"{set_id}: invalid released_at"
            ) from exc

    return SetRecord(
        set_id, code, name, set_type, released_at, digital
    )


def parse_set_pages(
    pages: Iterable[Mapping[str, Any]],
) -> tuple[SetRecord, ...]:
    """Validate a complete paginated /sets response."""
    records: list[SetRecord] = []
    ids: set[str] = set()
    codes: set[str] = set()
    previous_has_more: bool | None = None
    count = 0

    for page in pages:
        count += 1
        if count > 100:
            raise CatalogError(
                "sets: pagination exceeds 100 pages"
            )

        if previous_has_more is False:
            raise CatalogError(
                "sets: unexpected page after last page"
            )

        if not isinstance(page, dict) or page.get("object") != "list":
            raise CatalogError(
                "sets: expected a Scryfall List object"
            )

        has_more = page.get("has_more")
        data = page.get("data")

        if type(has_more) is not bool or not isinstance(data, list):
            raise CatalogError(
                "sets: invalid pagination or data"
            )

        next_page = page.get("next_page")
        if has_more and (
            not isinstance(next_page, str) or not next_page
        ):
            raise CatalogError(
                "sets: has_more requires next_page"
            )

        if page.get("warnings"):
            raise CatalogError(
                "sets: API returned warnings"
            )

        for item in data:
            record = _set_record(item)
            folded = record.code.casefold()

            if record.set_id in ids or folded in codes:
                raise CatalogError(
                    f"sets: duplicate set ID or code: {record.code!r}"
                )

            ids.add(record.set_id)
            codes.add(folded)
            records.append(record)

        previous_has_more = has_more

    if count == 0 or not records or previous_has_more:
        raise CatalogError(
            "sets: incomplete or empty response"
        )

    return tuple(sorted(
        records,
        key=lambda item: (item.code.casefold(), item.set_id),
    ))


def parse_type_catalog(
    name: str,
    payload: Mapping[str, Any],
) -> frozenset[str]:
    """Validate one Scryfall Catalog without altering its values."""
    if (
        not isinstance(payload, dict)
        or payload.get("object") != "catalog"
    ):
        raise CatalogError(
            f"{name}: expected a Scryfall Catalog object"
        )

    data = payload.get("data")
    total = payload.get("total_values")

    if (
        not isinstance(data, list)
        or not data
        or any(
            not isinstance(x, str) or not x.strip()
            for x in data
        )
    ):
        raise CatalogError(
            f"{name}: invalid catalog entries"
        )

    if (
        type(total) is not int
        or total != len(data)
        or len(data) != len(set(data))
    ):
        raise CatalogError(
            f"{name}: catalog count or uniqueness mismatch"
        )

    return frozenset(data)


def make_type_vocabulary(
    catalogs: Mapping[str, Mapping[str, Any]],
) -> TypeVocabulary:
    """Merge Scryfall type catalogs into the normalizer's vocabulary."""
    missing = set(TYPE_CATALOGS) - set(catalogs)
    if missing:
        raise CatalogError(
            f"missing type catalogs: {sorted(missing)}"
        )

    parsed = {
        name: parse_type_catalog(name, catalogs[name])
        for name in TYPE_CATALOGS
    }

    subtypes = set(ADDITIONAL_SUBTYPES) | SUPPLEMENTAL_SUBTYPES
    for name in TYPE_CATALOGS:
        if name != "supertypes":
            subtypes.update(parsed[name])

    # Additional catalogs may be supplied when available, but the
    # network downloader does not rely on undocumented endpoints.
    for optional in ("battle-types", "card-types"):
        if optional in catalogs:
            values = parse_type_catalog(
                optional, catalogs[optional]
            )

            if optional == "battle-types":
                subtypes.update(values)
            else:
                return TypeVocabulary(
                    parsed["supertypes"] | SUPPLEMENTAL_SUPERTYPES,
                    CARD_TYPES | SUPPLEMENTAL_CARD_TYPES | values,
                    frozenset(subtypes),
                )

    return TypeVocabulary(
        parsed["supertypes"] | SUPPLEMENTAL_SUPERTYPES,
        CARD_TYPES | SUPPLEMENTAL_CARD_TYPES,
        frozenset(subtypes),
    )


def write_sets(
    connection: sqlite3.Connection,
    records: Iterable[SetRecord],
) -> int:
    """Populate staging sets atomically without committing caller work.

    This requires an empty v1 `sets` table. Existing rows are never
    overwritten or replaced.
    """
    if (
        connection.execute("PRAGMA user_version").fetchone()[0]
        != 1
    ):
        raise CatalogError(
            "sets: expected CardDB schema v1"
        )

    if connection.execute(
        "SELECT 1 FROM sets LIMIT 1"
    ).fetchone():
        raise CatalogError(
            "sets: staging table is already populated"
        )

    connection.execute("SAVEPOINT carddb_write_sets")

    try:
        seen_ids: set[str] = set()
        seen_codes: set[str] = set()
        count = 0

        for record in records:
            if not isinstance(record, SetRecord):
                raise TypeError(
                    "write_sets expects SetRecord values"
                )

            if (
                record.set_id in seen_ids
                or record.code.casefold() in seen_codes
            ):
                raise CatalogError(
                    f"sets: duplicate set ID/code: {record.code!r}"
                )

            seen_ids.add(record.set_id)
            seen_codes.add(record.code.casefold())

            connection.execute(
                "INSERT INTO sets "
                "(set_id, code, name, set_type, released_at, digital) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.set_id,
                    record.code,
                    record.name,
                    record.set_type,
                    record.released_at,
                    int(record.digital),
                ),
            )
            count += 1

        if count == 0:
            raise CatalogError(
                "sets: cannot write an empty catalog"
            )

    except BaseException:
        connection.execute(
            "ROLLBACK TO SAVEPOINT carddb_write_sets"
        )
        connection.execute(
            "RELEASE SAVEPOINT carddb_write_sets"
        )
        raise

    connection.execute(
        "RELEASE SAVEPOINT carddb_write_sets"
    )
    return count


def _check_api_url(
    url: str,
    *,
    sets_page: bool = False,
) -> None:
    parsed = urlsplit(url)

    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.scryfall.com"
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise CatalogError(
            f"unsafe Scryfall API URL: {url!r}"
        )

    if sets_page and parsed.path != "/sets":
        raise CatalogError(
            f"unexpected sets pagination URL: {url!r}"
        )


def _http_json(url: str) -> dict[str, Any]:
    _check_api_url(url)

    request = Request(
        url,
        headers={
            "User-Agent": (
                "mtg-carddb/0.1 "
                "(+https://github.com/Dooboofloo/mtg-carddb)"
            ),
            "Accept": "application/json;q=0.9,*/*;q=0.8",
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            _check_api_url(response.geturl())
            data = response.read(16 * 1024 * 1024 + 1)

            if len(data) > 16 * 1024 * 1024:
                raise CatalogFetchError(
                    "Scryfall response exceeds 16 MiB"
                )

    except (HTTPError, URLError, OSError) as exc:
        raise CatalogFetchError(
            f"failed to fetch {url}: {exc}"
        ) from exc

    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise CatalogFetchError(
            f"invalid JSON from {url}"
        ) from exc

    if not isinstance(payload, dict):
        raise CatalogFetchError(
            f"expected JSON object from {url}"
        )

    return payload


def fetch_reference_data(
    *,
    fetch_json: Callable[[str], dict[str, Any]] | None = None,
    delay_seconds: float = 0.12,
) -> ReferenceData:
    """Explicitly fetch /sets and documented type catalogs from Scryfall.

    Inject `fetch_json` for offline tests. No requests occur during
    library import, CardDatabase.open(), or ordinary queries.
    """
    if (
        isinstance(delay_seconds, bool)
        or not isinstance(delay_seconds, (int, float))
    ):
        raise TypeError(
            "delay_seconds must be a nonnegative number"
        )

    if not 0 <= delay_seconds <= 60:
        raise ValueError(
            "delay_seconds must be between 0 and 60"
        )

    get = _http_json if fetch_json is None else fetch_json
    count = 0

    def fetch(url: str) -> dict[str, Any]:
        nonlocal count
        _check_api_url(url)

        if count:
            time.sleep(delay_seconds)

        count += 1
        result = get(url)

        if not isinstance(result, dict):
            raise CatalogError(
                f"invalid response at {url}"
            )

        return result

    def pages():
        url = SETS_URL
        visited: set[str] = set()

        for _ in range(100):
            _check_api_url(url, sets_page=True)

            if url in visited:
                raise CatalogError(
                    f"sets: repeated pagination URL {url!r}"
                )

            visited.add(url)
            page = fetch(url)
            yield page

            if page.get("has_more") is False:
                return

            next_page = page.get("next_page")
            if not isinstance(next_page, str):
                raise CatalogError(
                    "sets: pagination missing next_page"
                )

            url = next_page

        raise CatalogError("sets: too many pages")

    sets = parse_set_pages(pages())

    catalogs = {
        name: fetch(CATALOG_BASE + name)
        for name in TYPE_CATALOGS
    }

    return ReferenceData(
        sets,
        make_type_vocabulary(catalogs),
    )
