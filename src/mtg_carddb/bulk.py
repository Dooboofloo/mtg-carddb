"""Stream local Scryfall default_cards gzip-compressed JSON Lines exports.

No downloads, representative selection, or database writes happen here.
Fully exhaust the iterator to check the gzip trailer (CRC and length).
"""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_GZIP_MAGIC = b"\x1f\x8b"


class BulkReadError(RuntimeError):
    """The local bulk file could not be read or decompressed completely."""


class BulkFormatError(ValueError):
    """The decompressed source is not valid Scryfall card JSON Lines."""


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def iter_default_cards(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield one printing dict at a time from a local `.jsonl.gz` file.

    The payload must be gzip-compressed UTF-8 JSON Lines. Every nonempty
    record must be a JSON object with `object == 'card'`. Empty sources,
    blank lines, legacy JSON arrays, and truncated gzip streams are errors.

    Only the current record is retained by this reader. Consumers should
    stream or stage their data rather than collect every result into a list.
    """
    if not isinstance(path, (str, Path)):
        raise TypeError("path must be a string or Path")
    source = Path(path).expanduser()
    count = 0
    line_number = 0

    try:
        with source.open("rb") as probe:
            if probe.read(2) != _GZIP_MAGIC:
                raise BulkFormatError(
                    f"{source}: expected gzip-compressed JSON Lines"
                )

        with gzip.open(source, "rt", encoding="utf-8", errors="strict") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise BulkFormatError(
                        f"{source}: blank record on line {line_number}"
                    )
                try:
                    card = json.loads(
                        line, parse_constant=_reject_non_json_number
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    raise BulkFormatError(
                        f"{source}: invalid JSON on line {line_number}: {exc}"
                    ) from exc
                if not isinstance(card, dict) or card.get("object") != "card":
                    raise BulkFormatError(
                        f"{source}: line {line_number} is not a Scryfall Card object"
                    )
                count += 1
                yield card

        # Reaching the end of the stream also verifies the gzip trailer.
        if count == 0:
            raise BulkFormatError(f"{source}: no card records found")

    except (OSError, EOFError, zlib.error, UnicodeError) as exc:
        raise BulkReadError(
            f"could not read {source} near line {line_number}: {exc}"
        ) from exc
