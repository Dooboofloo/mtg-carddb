"""Bounded streaming of gzip-compressed UTF-8 JSON Lines.

Limits cover decompressed bytes, including line endings.
Full iteration still verifies the gzip CRC and trailer.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path


MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 4 * 1024**3


class JSONLSizeError(ValueError):
    """A decompressed JSONL record or export exceeded its limit."""


def iter_bounded_gzip_lines(
    path: str | Path,
    *,
    max_record_bytes: int = MAX_RECORD_BYTES,
    max_total_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> Iterator[tuple[int, str]]:
    """Yield (1-based line number, UTF-8 text).

    readlines are bounded before decoding or JSON parsing.
    Source-specific validation remains with the caller.
    """
    if (
        type(max_record_bytes) is not int
        or max_record_bytes < 1
        or type(max_total_bytes) is not int
        or max_total_bytes < 1
    ):
        raise ValueError(
            "JSONL size limits must be positive integers"
        )

    total = 0

    with gzip.open(path, "rb") as stream:
        line_number = 0

        while True:
            raw = stream.readline(
                max_record_bytes + 1
            )

            if not raw:
                break

            line_number += 1

            if len(raw) > max_record_bytes:
                raise JSONLSizeError(
                    f"decompressed JSONL line {line_number} "
                    f"exceeds {max_record_bytes} bytes"
                )

            total += len(raw)

            if total > max_total_bytes:
                raise JSONLSizeError(
                    "decompressed JSONL export exceeds "
                    f"{max_total_bytes} bytes "
                    f"at line {line_number}"
                )

            yield line_number, raw.decode("utf-8")
