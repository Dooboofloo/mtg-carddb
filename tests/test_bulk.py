"""Local, streaming JSONL/gzip reader tests (no network)."""

import gzip
import json

import pytest

from mtg_carddb.bulk import BulkFormatError, BulkReadError, iter_default_cards


def write_cards(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for card in records:
            stream.write(json.dumps(card, ensure_ascii=False) + "\n")


def test_reads_printings_without_normalizing_faces(tmp_path):
    path = tmp_path / "default_cards.jsonl.gz"
    printings = [
        {"object": "card", "id": "one", "oracle_id": "oracle-one",
         "name": "Crème Brûlée", "rarity": "rare"},
        {"object": "card", "id": "two", "layout": "reversible_card",
         "card_faces": [{"oracle_id": "first"}, {"oracle_id": "second"}]},
    ]
    write_cards(path, printings)
    assert list(iter_default_cards(path)) == printings


def test_reader_is_lazy_and_reports_bad_second_line(tmp_path):
    path = tmp_path / "cards.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write('{"object":"card","id":"first"}\n')
        stream.write('{"object":"card",NOT JSON}\n')
    reader = iter_default_cards(path)
    assert next(reader)["id"] == "first"
    with pytest.raises(BulkFormatError, match="line 2"):
        next(reader)


def test_many_records_iterator_does_not_need_full_export(tmp_path):
    path = tmp_path / "many.gz"
    write_cards(path, ({"object": "card", "id": str(i)} for i in range(2000)))
    assert sum(1 for _ in iter_default_cards(path)) == 2000


@pytest.mark.parametrize("payload", [
    b'[{"object":"card"}]\n',
    b'{"object":"card"}\n',
])
def test_plaintext_formats_are_rejected(tmp_path, payload):
    path = tmp_path / "fake.gz"
    path.write_bytes(payload)
    with pytest.raises(BulkFormatError, match="gzip-compressed"):
        list(iter_default_cards(path))


def test_compressed_legacy_array_is_rejected(tmp_path):
    path = tmp_path / "legacy.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write('[{"object":"card"}]')
    with pytest.raises(BulkFormatError, match="line 1"):
        list(iter_default_cards(path))


def test_blank_line_is_rejected_with_line_number(tmp_path):
    path = tmp_path / "blank.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write('{"object":"card"}\n\n')
    with pytest.raises(BulkFormatError, match="line 2"):
        list(iter_default_cards(path))


def test_wrong_object_and_empty_source_are_rejected(tmp_path):
    path = tmp_path / "wrong.gz"
    write_cards(path, [{"object": "ruling", "id": "x"}])
    with pytest.raises(BulkFormatError, match="Card object"):
        list(iter_default_cards(path))
    write_cards(path, [])
    with pytest.raises(BulkFormatError, match="no card records"):
        list(iter_default_cards(path))


def test_non_json_numeric_constants_are_rejected(tmp_path):
    path = tmp_path / "nan.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write('{"object":"card","cmc":NaN}\n')
    with pytest.raises(BulkFormatError, match="non-JSON numeric constant"):
        list(iter_default_cards(path))


def test_truncated_gzip_file_fails_even_after_valid_record(tmp_path):
    path = tmp_path / "truncated.gz"
    write_cards(path, [{"object": "card", "id": "x"}])
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(BulkReadError):
        list(iter_default_cards(path))


def test_corrupted_gzip_trailer_fails(tmp_path):
    path = tmp_path / "crc.gz"
    write_cards(path, [{"object": "card", "id": "x"}])
    contents = bytearray(path.read_bytes())
    contents[-8] ^= 0xFF  # Corrupt the CRC32 field in the gzip trailer.
    path.write_bytes(contents)
    with pytest.raises(BulkReadError):
        list(iter_default_cards(path))


def test_missing_path_reports_read_failure(tmp_path):
    with pytest.raises(BulkReadError, match="could not read"):
        list(iter_default_cards(tmp_path / "missing.jsonl.gz"))


def test_bulk_reader_maps_oversized_record_to_format_error(
    tmp_path,
    monkeypatch,
):
    from mtg_carddb import bulk
    from mtg_carddb.jsonl_limits import (
        iter_bounded_gzip_lines,
    )

    path = tmp_path / "oversized.gz"
    write_cards(
        path,
        [{"object": "card", "id": "x"}],
    )

    monkeypatch.setattr(
        bulk,
        "iter_bounded_gzip_lines",
        lambda src: iter_bounded_gzip_lines(
            src,
            max_record_bytes=8,
        ),
    )

    with pytest.raises(
        BulkFormatError,
        match="line 1 exceeds 8 bytes",
    ):
        list(iter_default_cards(path))


def test_bulk_reader_maps_total_limit_to_format_error(
    tmp_path,
    monkeypatch,
):
    from mtg_carddb import bulk
    from mtg_carddb.jsonl_limits import (
        iter_bounded_gzip_lines,
    )

    path = tmp_path / "oversized-total.gz"

    write_cards(
        path,
        [
            {"object": "card", "id": "a"},
            {"object": "card", "id": "b"},
        ],
    )

    monkeypatch.setattr(
        bulk,
        "iter_bounded_gzip_lines",
        lambda src: iter_bounded_gzip_lines(
            src,
            max_total_bytes=32,
        ),
    )

    with pytest.raises(
        BulkFormatError,
        match="export exceeds 32 bytes",
    ):
        list(iter_default_cards(path))
