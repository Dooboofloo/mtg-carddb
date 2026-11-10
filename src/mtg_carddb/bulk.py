"""Stream local Scryfall default_cards gzip-compressed JSON Lines exports.

No downloads, representative selection, or database writes happen here.
Fully exhaust the iterator to check the gzip trailer (CRC and length).
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .jsonl_limits import (
    JSONLSizeError,
    iter_bounded_gzip_lines,
)

_GZIP_MAGIC = b"\x1f\x8b"


class BulkReadError(RuntimeError):
    """The local bulk file could not be read or decompressed completely."""


class BulkFormatError(ValueError):
    """The decompressed source is not valid Scryfall card JSON Lines."""


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def iter_default_cards(
    path: str | Path,
) -> Iterator[dict[str, Any]]:
    """Yield validated card objects from bounded gzip JSONL.

    Exhaustion verifies the gzip trailer. The reader retains
    only the current decompressed record.
    """
    if not isinstance(path, (str, Path)):
        raise TypeError(
            "path must be a string or Path"
        )

    source = Path(path).expanduser()
    count = 0
    line_number = 0

    try:
        with source.open("rb") as probe:
            if probe.read(2) != _GZIP_MAGIC:
                raise BulkFormatError(
                    f"{source}: expected "
                    "gzip-compressed JSON Lines"
                )

        for line_number, line in iter_bounded_gzip_lines(
            source
        ):
            if not line.strip():
                raise BulkFormatError(
                    f"{source}: blank record "
                    f"on line {line_number}"
                )

            try:
                card = json.loads(
                    line,
                    parse_constant=_reject_non_json_number,
                )
            except (
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                raise BulkFormatError(
                    f"{source}: invalid JSON "
                    f"on line {line_number}: {exc}"
                ) from exc

            if (
                not isinstance(card, dict)
                or card.get("object") != "card"
            ):
                raise BulkFormatError(
                    f"{source}: line {line_number} "
                    "is not a Scryfall Card object"
                )

            count += 1
            yield card

        # Reaching EOF verifies the gzip CRC and trailer.
        if count == 0:
            raise BulkFormatError(
                f"{source}: no card records found"
            )

    except JSONLSizeError as exc:
        raise BulkFormatError(
            f"{source}: {exc}"
        ) from exc

    except (
        OSError,
        EOFError,
        zlib.error,
        UnicodeError,
    ) as exc:
        raise BulkReadError(
            f"could not read {source} "
            f"near line {line_number}: {exc}"
        ) from exc
