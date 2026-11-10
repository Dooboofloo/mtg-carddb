"""Acquire, build, and activate a CardDB database in one workflow.

Source acquisition and snapshot building occur in an isolated workspace.
Only the final activation touches the installed database.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import os
import logging
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator, Literal
from collections.abc import Callable

from .acquisition import acquire_bulk_sources
from .activation import activate_snapshot, capture_target_state
from .builder import SnapshotBuildResult, build_snapshot
from .reference_sources import capture_reference_sources
from .database import CardDatabase
from .storage import DatabaseOpenError


class DatabaseWorkflowError(RuntimeError):
    """A database workflow could not safely proceed."""

_LOGGER = logging.getLogger("mtg_carddb")

@dataclass(frozen=True, slots=True)
class DatabaseWorkflowResult:
    operation: Literal["init", "update"]
    path: Path
    build: SnapshotBuildResult


WorkflowStage = Literal[
    "bulk",
    "references",
    "build",
    "activation",
]

WorkflowStatus = Literal["start", "complete"]


@dataclass(frozen=True, slots=True)
class WorkflowProgress:
    stage: WorkflowStage
    status: WorkflowStatus
    detail: str = ""


ProgressCallback = Callable[[WorkflowProgress], None]


def _validate_update_target(target: Path) -> None:
    """Reject non-CardDB files before an update can replace them."""
    try:
        with CardDatabase.open(
            target,
            warn_if_stale=False,
        ) as database:
            database.info()

    except (
        DatabaseOpenError,
        sqlite3.DatabaseError,
        ValueError,
        TypeError,
    ) as exc:
        raise DatabaseWorkflowError(
            "cannot update: target is not a readable, compatible "
            f"CardDB database: {target}"
        ) from exc


def _check_target(target: Path, *, update: bool) -> None:
    if target.is_symlink():
        raise DatabaseWorkflowError(
            f"refusing a symbolic-link database destination: {target}"
        )

    if update:
        if not target.is_file():
            raise FileNotFoundError(
                "cannot update: database is not an "
                f"existing regular file: {target}"
            )

        _validate_update_target(target)

    elif target.exists():
        raise FileExistsError(
            f"database already exists: {target}"
        )


@contextmanager
def _workflow_lock(target: Path) -> Iterator[None]:
    """Prevent cooperating init/update operations from overlapping."""
    lock_path = target.with_name(
        target.name + ".workflow.lock"
    )

    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise DatabaseWorkflowError(
            "another database workflow may be running: "
            f"{lock_path}"
        ) from exc

    os.close(descriptor)

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _run(
    target_path: str | Path,
    *,
    update: bool,
    on_progress: ProgressCallback | None = None,
) -> DatabaseWorkflowResult:
    target = Path(target_path).absolute()

    if not target.parent.is_dir():
        raise FileNotFoundError(
            "database parent directory does not exist: "
            f"{target.parent}"
        )

    _check_target(target, update=update)

    callback = on_progress

    def report(
        stage: WorkflowStage,
        status: WorkflowStatus,
        detail: str = "",
    ) -> None:
        nonlocal callback

        if callback is None:
            return

        try:
            callback(
                WorkflowProgress(stage, status, detail)
            )

        except Exception:
            # Progress is observational. Never report an unsuccessful
            # update merely because its notification handler failed.
            callback = None

            _LOGGER.warning(
                "CardDB progress callback failed; further "
                "notifications are disabled",
                exc_info=True,
            )

    with _workflow_lock(target):
        # Capture the identity before validating, so an external
        # edit during schema inspection is detectable too.
        original_target_state = (
            capture_target_state(target)
            if update
            else None
        )

        _check_target(target, update=update)

        if original_target_state is not None:
            if (
                capture_target_state(target)
                != original_target_state
            ):
                raise DatabaseWorkflowError(
                    "database target changed during "
                    f"initial validation: {target}"
                )

        with TemporaryDirectory(
            prefix=f".{target.stem}-build-",
            dir=target.parent,
        ) as temporary:
            workspace = Path(temporary)

            report("bulk", "start")

            bulk = acquire_bulk_sources(
                workspace / "bulk"
            )

            report("bulk", "complete")

            report("references", "start")

            references = capture_reference_sources(
                workspace / "references"
            )

            report("references", "complete")

            report("build", "start")

            built = build_snapshot(
                cards_path=bulk.cards_path,
                tags_path=bulk.tags_path,
                output_path=workspace / "snapshot.sqlite",
                bulk_sources=bulk.bulk_sources,
                reference_archive=references,
            )

            report(
                "build",
                "complete",
                f"{built.cards:,} Oracle cards, "
                f"{built.printings:,} source printings",
            )

            _check_target(target, update=update)

            report("activation", "start")

            activate_snapshot(
                built.path,
                target,
                replace_existing=update,
                expected_target_state=original_target_state,
            )

            report("activation", "complete")

            return DatabaseWorkflowResult(
                operation="update" if update else "init",
                path=target,
                build=replace(built, path=target),
            )


def initialize_database(
    target_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> DatabaseWorkflowResult:
    """Install a new database without overwriting one."""
    return _run(
        target_path,
        update=False,
        on_progress=on_progress,
    )


def update_database(
    target_path: str | Path,
    *,
    on_progress: ProgressCallback | None = None,
) -> DatabaseWorkflowResult:
    """Replace a database after building a verified snapshot."""
    return _run(
        target_path,
        update=True,
        on_progress=on_progress,
    )
