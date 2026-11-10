"""Read-only database availability and source-freshness diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import math
from pathlib import Path
from typing import Literal

from .storage import DatabaseOpenError

DEFAULT_STALE_AFTER_DAYS = 7.0
_LOGGER = logging.getLogger("mtg_carddb")


class DatabaseNotInitializedError(DatabaseOpenError, FileNotFoundError):
    """The requested CardDB database has not been initialized."""


@dataclass(frozen=True, slots=True)
class DatabaseFreshness:
    status: Literal["current", "stale", "unknown"]
    source_updated_at: datetime | None
    age: timedelta | None
    source_type: str | None = None


def require_database_file(path: str | Path) -> Path:
    """Reject missing databases with an actionable error."""
    resolved = Path(path)

    if resolved.is_file():
        return resolved

    if resolved.exists():
        raise IsADirectoryError(
            f"CardDB path is not a file: {resolved}"
        )

    raise DatabaseNotInitializedError(
        f"CardDB database not found: {resolved}. "
        "Initialize it first with initialize_database(path) "
        "(or `mtg-carddb init` when the CLI is installed)."
    )


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
    ):
        return None

    return parsed.astimezone(timezone.utc)


def database_freshness(
    info: object,
    *,
    stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
    now: datetime | None = None,
) -> DatabaseFreshness:
    """Judge data age from Scryfall provenance, not file modification time."""

    if (
        isinstance(stale_after_days, bool)
        or not isinstance(stale_after_days, (int, float))
        or not math.isfinite(stale_after_days)
        or stale_after_days <= 0
    ):
        raise ValueError(
            "stale_after_days must be a finite positive number"
        )

    current = (
        now if now is not None
        else datetime.now(timezone.utc)
    )

    if (
        current.tzinfo is None
        or current.utcoffset() is None
    ):
        raise ValueError(
            "now must be timezone-aware"
        )

    current = current.astimezone(timezone.utc)

    # Both bulk exports are required. A recently updated card export
    # cannot make old or missing Oracle Tags current.
    sources = {
        source.source_type: source
        for source in info.sources
    }

    observed: list[tuple[str, datetime]] = []

    for source_type in ("default_cards", "oracle_tags"):
        source = sources.get(source_type)

        updated = (
            _timestamp(source.source_updated_at)
            if source is not None
            else None
        )

        if (
            updated is None
            or updated > current + timedelta(minutes=5)
        ):
            return DatabaseFreshness(
                "unknown", None, None
            )

        observed.append((source_type, updated))

    oldest_type, oldest_update = min(
        observed,
        key=lambda item: item[1],
    )

    age = max(
        current - oldest_update,
        timedelta(0),
    )

    threshold = timedelta(
        days=stale_after_days
    )

    return DatabaseFreshness(
        "stale" if age >= threshold else "current",
        oldest_update,
        age,
        oldest_type,
    )


def report_database_freshness(
    info: object,
    path: str | Path,
    *,
    stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
    enabled: bool = True,
    now: datetime | None = None,
) -> DatabaseFreshness:
    """Log nonfatal freshness warnings without fetching or updating."""

    if type(enabled) is not bool:
        raise TypeError(
            "enabled must be a bool"
        )

    result = database_freshness(
        info,
        stale_after_days=stale_after_days,
        now=now,
    )

    if not enabled:
        return result

    if result.status == "stale":
        assert result.age is not None

        _LOGGER.warning(
            "CardDB database at %s is stale: Scryfall "
            "%s data is %.1f days old. "
            "Run `mtg-carddb update` or "
            "update_database(path) to refresh it.",
            path,
            result.source_type,
            result.age.total_seconds() / 86400,
        )

    elif result.status == "unknown":
        _LOGGER.warning(
            "CardDB database at %s has unknown source "
            "freshness (missing or invalid default_cards/oracle_tags "
            "source_updated_at). Run `mtg-carddb update` "
            "or update_database(path) to record verified "
            "source provenance.",
            path,
        )

    return result
