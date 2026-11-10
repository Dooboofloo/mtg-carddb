"""Command-line lifecycle and inspection interface for MTG CardDB."""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path
from typing import Sequence
from contextlib import nullcontext
from threading import Event, Lock, Thread
from time import monotonic

from .acquisition import BulkAcquisitionError
from .activation import SnapshotActivationError
from .database import CardDatabase, _default_database_path
from .freshness import (
    DEFAULT_STALE_AFTER_DAYS,
    report_database_freshness,
)
from .reference_sources import ReferenceArchiveError
from .storage import DatabaseOpenError
from .workflow import (
    DatabaseWorkflowError,
    WorkflowProgress,
    initialize_database,
    update_database,
)


def _positive_days(value: str) -> float:
    try:
        days = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a positive number"
        ) from exc

    if not math.isfinite(days) or days <= 0:
        raise argparse.ArgumentTypeError(
            "must be a finite positive number"
        )

    return days


_PHASE_LABELS = {
    "bulk": "Downloading Scryfall bulk exports",
    "references": "Capturing Scryfall reference catalogs",
    "build": "Building and validating SQLite snapshot",
    "activation": "Activating validated database",
}


def _elapsed(seconds: float) -> str:
    """Format monotonic elapsed time as MM:SS or HH:MM:SS."""
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    return f"{minutes:02}:{seconds:02}"


class _ProgressDisplay:
    """Stream workflow progress and periodic heartbeats to stderr."""

    def __init__(
        self,
        *,
        interval_seconds: float = 10.0,
    ) -> None:
        self._interval = interval_seconds
        self._stop = Event()
        self._lock = Lock()

        self._started = 0.0
        self._stage: str | None = None
        self._stage_started = 0.0
        self._thread: Thread | None = None

    def _write(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def __enter__(self) -> _ProgressDisplay:
        self._started = monotonic()

        self._thread = Thread(
            target=self._heartbeat,
            name="carddb-cli-progress",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()

        if self._thread is not None:
            self._thread.join()

    def __call__(
        self,
        event: WorkflowProgress,
    ) -> None:
        now = monotonic()

        with self._lock:
            total = _elapsed(now - self._started)
            label = _PHASE_LABELS[event.stage]

            if event.status == "start":
                self._stage = event.stage
                self._stage_started = now

                self._write(
                    f"[{total}] {label}..."
                )

            elif event.status == "complete":
                duration = (
                    now - self._stage_started
                    if self._stage == event.stage
                    else 0.0
                )

                extra = (
                    f" — {event.detail}"
                    if event.detail
                    else ""
                )

                self._write(
                    f"[{total}] Completed "
                    f"{label.lower()} "
                    f"({_elapsed(duration)}){extra}"
                )

                self._stage = None

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._stage is None:
                    continue

                now = monotonic()
                label = _PHASE_LABELS[self._stage]

                self._write(
                    f"[{_elapsed(now - self._started)}] "
                    f"Still {label.lower()} "
                    f"({_elapsed(now - self._stage_started)} "
                    "in phase)"
                )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtg-carddb",
        description=(
            "Manage and inspect a local "
            "Scryfall-derived CardDB database."
        ),
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    for name, help_text in (
        ("init", "Create a new CardDB database"),
        (
            "update",
            "Rebuild and safely replace an existing database",
        ),
        ("info", "Inspect database metadata and freshness"),
    ):
        command = commands.add_parser(
            name,
            help=help_text,
        )

        command.add_argument(
            "--database",
            "-d",
            metavar="PATH",
            type=Path,
            help=(
                "SQLite database path "
                "(default: per-user CardDB location)"
            ),
        )

        if name in ("init", "update"):
            command.add_argument(
                "--no-progress",
                action="store_true",
                help=(
                    "Suppress phase messages and heartbeats "
                    "(the final summary is still printed)"
                ),
            )

        if name == "info":
            command.add_argument(
                "--no-stale-warning",
                action="store_true",
                help=(
                    "Do not log stale/unknown "
                    "freshness warnings"
                ),
            )

            command.add_argument(
                "--stale-after-days",
                type=_positive_days,
                default=DEFAULT_STALE_AFTER_DAYS,
                metavar="DAYS",
                help="Freshness threshold in days (default: 7)",
            )

    return parser


def _print_build(result: object) -> None:
    print(f"Database {result.operation}: {result.path}")
    print(f"Cards: {result.build.cards}")
    print(f"Sets: {result.build.sets}")
    print(f"Tags: {result.build.tags}")
    print(f"Printings processed: {result.build.printings}")
    print(
        f"Source records: "
        f"{result.build.provenance_sources}"
    )


def _print_info(
    path: Path,
    *,
    warn: bool,
    threshold: float,
) -> None:
    # The CLI controls warning emission explicitly so it
    # can show structured freshness status even when
    # warnings are suppressed.
    with CardDatabase.open(
        path,
        warn_if_stale=False,
    ) as database:
        metadata = database.info()

        freshness = report_database_freshness(
            metadata,
            path,
            stale_after_days=threshold,
            enabled=warn,
        )

        print(f"Database: {path}")
        print(f"Cards: {len(database)}")
        print(f"Schema version: {metadata.schema_version}")
        print(f"Builder version: {metadata.builder_version}")
        print(f"Built at: {metadata.created_at}")
        print(f"Source records: {len(metadata.sources)}")
        print(f"Freshness: {freshness.status}")

        if freshness.source_updated_at is not None:
            print(f"Oldest bulk source: {freshness.source_type}")
            print(
                "Oldest bulk source updated: "
                f"{freshness.source_updated_at.isoformat()}"
            )

        if freshness.age is not None:
            age_days = (
                freshness.age.total_seconds() / 86400
            )
            print(f"Source age (days): {age_days:.1f}")


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 on success and 1 for operational errors.

    argparse handles usage errors with exit status 2.
    """
    args = _parser().parse_args(argv)

    target = (
        _default_database_path()
        if args.database is None
        else args.database
    ).absolute()

    try:
        if args.command == "init":
            if args.database is None:
                target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

            with (
                _ProgressDisplay()
                if not args.no_progress
                else nullcontext(None)
            ) as progress:
                result = initialize_database(
                    target,
                    on_progress=progress,
                )

            _print_build(result)

        elif args.command == "update":
            with (
                _ProgressDisplay()
                if not args.no_progress
                else nullcontext(None)
            ) as progress:
                result = update_database(
                    target,
                    on_progress=progress,
                )

            _print_build(result)

        elif args.command == "info":
            _print_info(
                target,
                warn=not args.no_stale_warning,
                threshold=args.stale_after_days,
            )

    except (
        OSError,
        ValueError,
        sqlite3.Error,
        DatabaseOpenError,
        DatabaseWorkflowError,
        SnapshotActivationError,
        BulkAcquisitionError,
        ReferenceArchiveError,
    ) as exc:
        print(
            f"mtg-carddb: {exc}",
            file=sys.stderr,
        )

        if (
            args.command == "update"
            and isinstance(exc, FileNotFoundError)
        ):
            print(
                "No installed database to update. "
                "Run `mtg-carddb init` first.",
                file=sys.stderr,
            )

        return 1

    return 0
