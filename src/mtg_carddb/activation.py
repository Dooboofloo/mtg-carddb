"""Validate and activate a completed CardDB SQLite snapshot.

Activation never modifies the source snapshot and never opens the target
for writing. Replacement happens with one same-directory os.replace call.
"""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .database import CardDatabase


class SnapshotActivationError(RuntimeError):
    """A snapshot could not be verified or safely activated."""


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

    if source.resolve() == target.resolve():
        raise ValueError(
            "source snapshot and target must be different files"
        )

    lock = target.with_name(target.name + ".lock")

    try:
        descriptor = os.open(
            lock,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise SnapshotActivationError(
            f"activation lock already exists: {lock}"
        ) from exc
    else:
        os.close(descriptor)

    staged: Path | None = None

    try:
        # A database with pending journal/WAL data cannot safely
        # be activated by copying or replacing its main file alone.
        for database_path in (source, target):
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(str(database_path) + suffix)

                if sidecar.exists():
                    raise SnapshotActivationError(
                        "SQLite sidecar present; close/checkpoint "
                        f"database first: {sidecar}"
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

        try:
            os.replace(staged, target)
        except OSError as exc:
            raise SnapshotActivationError(
                f"could not replace {target}; "
                f"existing database unchanged: {exc}"
            ) from exc

        staged = None
        return target

    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)

        lock.unlink(missing_ok=True)
