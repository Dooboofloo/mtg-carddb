"""Validate and activate a completed CardDB SQLite snapshot.

Activation never modifies the source snapshot and never opens the target
for writing. Replacement happens with one same-directory os.replace call.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import stat
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import logging

from .database import CardDatabase
from .lockfiles import acquire_lock


class SnapshotActivationError(RuntimeError):
    """A snapshot could not be verified or safely activated."""


_LOGGER = logging.getLogger("mtg_carddb")


@dataclass(frozen=True, slots=True)
class TargetState:
    """Identity, metadata and bytes of the database being updated."""

    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


def _file_identity(
    info: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _same_file_via_path(
    opened: os.stat_result,
    by_path: os.stat_result,
) -> bool:
    """Compare descriptor and path metadata across platforms.

    On Windows, fstat() and stat() may report different meanings
    for st_ctime_ns. Exclude ctime from this cross-API comparison.
    """
    return (
        opened.st_dev == by_path.st_dev
        and opened.st_ino == by_path.st_ino
        and opened.st_size == by_path.st_size
        and opened.st_mtime_ns == by_path.st_mtime_ns
    )


def capture_target_state(
    path: str | Path,
) -> TargetState:
    """Fingerprint an installed database and reject unstable inputs."""

    target = Path(path)

    if target.is_symlink():
        raise SnapshotActivationError(
            f"database target is a symlink: {target}"
        )

    digest = hashlib.sha256()

    try:
        with target.open("rb") as stream:
            before = os.fstat(stream.fileno())

            if not stat.S_ISREG(before.st_mode):
                raise SnapshotActivationError(
                    "database target is not a regular file: "
                    f"{target}"
                )

            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)

            after = os.fstat(stream.fileno())

        current = target.stat()

    except OSError as exc:
        raise SnapshotActivationError(
            f"cannot fingerprint database target {target}: {exc}"
        ) from exc

    if (
        target.is_symlink()
        or _file_identity(before) != _file_identity(after)
        or not _same_file_via_path(after, current)
    ):
        raise SnapshotActivationError(
            f"database target changed during fingerprint: {target}"
        )

    return TargetState(
        *_file_identity(after),
        digest.hexdigest(),
    )


def _check_target_state(
    target: Path,
    expected: TargetState,
) -> None:
    """Refuse activation when the installed file no longer matches."""

    if capture_target_state(target) != expected:
        raise SnapshotActivationError(
            f"database target changed during update: {target}; "
            "the existing target was not replaced"
        )


def _reject_sidecars(
    source: Path,
    target: Path,
) -> None:
    """Reject SQLite databases with pending WAL or journal files."""

    for database_path in (source, target):
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(
                str(database_path) + suffix
            )

            if sidecar.exists():
                raise SnapshotActivationError(
                    "SQLite sidecar present; close/checkpoint "
                    f"database first: {sidecar}"
                )


def _validate_snapshot(path: Path) -> None:
    """Verify the copied file, not a potentially changing source file."""
    try:
        with closing(
            sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
        ) as connection:
            result = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()

            if result != ("ok",):
                raise SnapshotActivationError(
                    f"SQLite integrity_check failed: {result!r}"
                )

            errors = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

            if errors:
                raise SnapshotActivationError(
                    f"SQLite foreign-key violations: {errors[:5]!r}"
                )

        # Also verify compatibility with the CardDB public API.
        with CardDatabase.open(path) as database:
            if len(database) != len(database.all_cards()):
                raise SnapshotActivationError(
                    "public CardDatabase count differs "
                    "from its Oracle-ID universe"
                )

            database.info()

    except SnapshotActivationError:
        raise

    except Exception as exc:
        raise SnapshotActivationError(
            f"invalid CardDB snapshot {path}: {exc}"
        ) from exc


def activate_snapshot(
    snapshot_path: str | Path,
    target_path: str | Path,
    *,
    replace_existing: bool = True,
    expected_target_state: TargetState | None = None,
) -> Path:
    """Atomically activate a verified snapshot at an explicit destination.

    Copies the source into the target directory, validates that copy,
    then replaces the destination in one filesystem operation.

    A failure before or during replacement leaves the previous
    destination unchanged.

    The lock file is an exclusive advisory lock for cooperating
    activators. If the process is killed, a stale lock may need
    manual removal.
    """
    if type(replace_existing) is not bool:
        raise TypeError("replace_existing must be a bool")

    if expected_target_state is not None:
        if not isinstance(expected_target_state, TargetState):
            raise TypeError(
                "expected_target_state must be TargetState or None"
            )

        if not replace_existing:
            raise ValueError(
                "a target fingerprint is only valid for replacement"
            )

    source = Path(snapshot_path)
    target = Path(target_path)

    if not source.is_file():
        raise FileNotFoundError(
            f"snapshot not found: {source}"
        )

    if not target.parent.is_dir():
        raise FileNotFoundError(
            f"target directory not found: {target.parent}"
        )

    if target.is_symlink():
        raise SnapshotActivationError(
            "refusing to replace a symlink target"
        )

    if target.exists() and not target.is_file():
        raise SnapshotActivationError(
            "target exists but is not a regular file"
        )

    if not replace_existing and target.exists():
        raise FileExistsError(
            f"database already exists: {target}"
        )

    if source.resolve() == target.resolve():
        raise ValueError(
            "source snapshot and target must be different files"
        )

    lock = target.with_name(target.name + ".lock")

    owner = acquire_lock(
        lock,
        purpose="activation",
        error_type=SnapshotActivationError,
        collision_message="activation lock already exists",
    )

    staged: Path | None = None

    try:
        # The candidate and destination must not have pending
        # WAL or journal data.
        _reject_sidecars(source, target)

        if expected_target_state is not None:
            _check_target_state(
                target,
                expected_target_state,
            )

        # Always stage in the target directory so replacement
        # occurs on the same filesystem.
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".sqlite",
            dir=target.parent,
            delete=False,
        ) as output:
            staged = Path(output.name)

            with source.open("rb") as input_file:
                shutil.copyfileobj(
                    input_file,
                    output,
                    length=1024 * 1024,
                )

            output.flush()
            os.fsync(output.fileno())

        # Validate the exact bytes that will be activated.
        _validate_snapshot(staged)

        # Detect external changes during validation as late
        # as possible, before replacing the installed file.
        _reject_sidecars(source, target)

        if expected_target_state is not None:
            _check_target_state(
                target,
                expected_target_state,
            )

        try:
            if replace_existing:
                # Updates atomically replace the old database.
                os.replace(staged, target)
                staged = None
            else:
                # Initialization installs only if the target does not
                # exist. The new hard link points to the verified copy.
                # Keep `staged` for deletion by the existing finally block.
                os.link(staged, target)

        except FileExistsError:
            # For create-only installation, never overwrite a target
            # that appeared during validation.
            raise

        except OSError as exc:
            action = (
                "replace" if replace_existing else "install"
            )
            raise SnapshotActivationError(
                f"could not {action} {target}; "
                f"existing database unchanged: {exc}"
            ) from exc

        return target

    finally:
        try:
            if staged is not None:
                staged.unlink(missing_ok=True)

        except OSError:
            _LOGGER.warning(
                "Could not clean up staged CardDB file %s",
                staged,
                exc_info=True,
            )

        finally:
            owner.release()
