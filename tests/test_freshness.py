"""Tests for database availability and freshness policy."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import logging

import pytest

from mtg_carddb.freshness import (
    DatabaseNotInitializedError,
    database_freshness,
    report_database_freshness,
    require_database_file,
)


NOW = datetime(
    2026, 11, 10, 12,
    tzinfo=timezone.utc,
)


def info(updated=None, *, source=True):
    return SimpleNamespace(
        created_at=NOW.isoformat(),
        sources=(
            (
                SimpleNamespace(
                    source_type="default_cards",
                    source_updated_at=updated,
                ),
            )
            if source else ()
        ),
    )


def test_missing_database_suggests_init(tmp_path):
    target = tmp_path / "carddb.sqlite"

    with pytest.raises(
        DatabaseNotInitializedError,
        match="init",
    ):
        require_database_file(target)


def test_existing_database_accepted(tmp_path):
    target = tmp_path / "carddb.sqlite"
    target.touch()

    assert require_database_file(target) == target


def test_directory_is_not_missing_database(tmp_path):
    with pytest.raises(IsADirectoryError):
        require_database_file(tmp_path)


def test_six_day_source_is_current():
    result = database_freshness(
        info((NOW - timedelta(days=6)).isoformat()),
        now=NOW,
    )

    assert result.status == "current"


def test_seven_day_source_is_stale():
    result = database_freshness(
        info((NOW - timedelta(days=7)).isoformat()),
        now=NOW,
    )

    assert result.status == "stale"
    assert result.age == timedelta(days=7)


def test_custom_threshold():
    source = info(
        (NOW - timedelta(days=10)).isoformat()
    )

    assert database_freshness(
        source, now=NOW
    ).status == "stale"

    assert database_freshness(
        source,
        now=NOW,
        stale_after_days=14,
    ).status == "current"


def test_missing_source_unknown_even_with_recent_build():
    assert database_freshness(
        info(source=False),
        now=NOW,
    ).status == "unknown"


def test_no_upstream_update_timestamp_unknown():
    assert database_freshness(
        info(),
        now=NOW,
    ).status == "unknown"


@pytest.mark.parametrize(
    "invalid",
    [
        "not-a-date",
        "2026-11-08T12:00:00",
    ],
)
def test_invalid_source_timestamp_unknown(invalid):
    assert database_freshness(
        info(invalid),
        now=NOW,
    ).status == "unknown"


def test_future_source_timestamp_unknown():
    assert database_freshness(
        info(
            (NOW + timedelta(days=1)).isoformat()
        ),
        now=NOW,
    ).status == "unknown"


def test_stale_logs_warning_without_throwing(caplog):
    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        result = report_database_freshness(
            info(
                (NOW - timedelta(days=8)).isoformat()
            ),
            "cards.sqlite",
            now=NOW,
        )

    assert result.status == "stale"
    assert "8.0 days old" in caplog.text
    assert "update" in caplog.text


def test_disabled_warning_returns_status_silently(caplog):
    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        result = report_database_freshness(
            info(
                (NOW - timedelta(days=8)).isoformat()
            ),
            "cards.sqlite",
            now=NOW,
            enabled=False,
        )

    assert result.status == "stale"
    assert caplog.records == []


def test_fresh_database_logs_nothing(caplog):
    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        result = report_database_freshness(
            info(
                (NOW - timedelta(hours=12)).isoformat()
            ),
            "cards.sqlite",
            now=NOW,
        )

    assert result.status == "current"
    assert caplog.records == []


@pytest.mark.parametrize(
    "invalid",
    [
        0,
        float("nan"),
        "7",
    ],
)
def test_invalid_threshold_rejected(invalid):
    with pytest.raises(
        ValueError,
        match="positive",
    ):
        database_freshness(
            info(),
            now=NOW,
            stale_after_days=invalid,
        )
