
"""Bounded gzip reader tests using tiny compressed fixtures."""

import gzip

import pytest

from mtg_carddb.jsonl_limits import (
    JSONLSizeError,
    iter_bounded_gzip_lines,
)


def test_reads_utf8_crlf_and_final_line_without_newline(
    tmp_path,
):
    path = tmp_path / "lines.gz"

    path.write_bytes(
        gzip.compress(
            "Café\r\nend".encode("utf-8")
        )
    )

    assert list(iter_bounded_gzip_lines(
        path,
        max_record_bytes=7,
        max_total_bytes=10,
    )) == [
        (1, "Café\r\n"),
        (2, "end"),
    ]


def test_one_long_line_is_rejected_before_full_line_is_read(
    tmp_path,
):
    path = tmp_path / "single.gz"

    path.write_bytes(
        gzip.compress(b"x" * 1000 + b"\n")
    )

    with pytest.raises(
        JSONLSizeError,
        match="line 1 exceeds 16 bytes",
    ):
        list(iter_bounded_gzip_lines(
            path,
            max_record_bytes=16,
            max_total_bytes=2000,
        ))


def test_total_decompressed_limit_rejects_short_lines(
    tmp_path,
):
    path = tmp_path / "many.gz"

    path.write_bytes(
        gzip.compress(b"abc\nabc\nabc\n")
    )

    reader = iter_bounded_gzip_lines(
        path,
        max_record_bytes=10,
        max_total_bytes=8,
    )

    assert next(reader) == (1, "abc\n")
    assert next(reader) == (2, "abc\n")

    with pytest.raises(
        JSONLSizeError,
        match="export exceeds 8 bytes at line 3",
    ):
        next(reader)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_record_bytes": 0},
        {"max_total_bytes": -1},
        {"max_record_bytes": True},
    ],
)
def test_invalid_limits_are_rejected(
    tmp_path,
    limits,
):
    path = tmp_path / "one.gz"
    path.write_bytes(
        gzip.compress(b"ok\n")
    )

    with pytest.raises(
        ValueError,
        match="positive integers",
    ):
        list(iter_bounded_gzip_lines(
            path,
            **limits,
        ))
