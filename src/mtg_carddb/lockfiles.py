"""Cooperative lock-file ownership and read-only recovery diagnostics.

Lock presence does not establish that the recorded PID remains alive.
No lock is removed automatically merely because it looks old.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
from uuid import uuid4


_LOGGER = logging.getLogger("mtg_carddb")
_MAX_LOCK_BYTES = 4096


def describe_lock(path: str | Path) -> str:
    """Describe a lock without guessing whether its owner is running."""
    lock = Path(path)

    try:
        with lock.open("rb") as stream:
            raw = stream.read(_MAX_LOCK_BYTES + 1)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        return f"present (metadata unreadable: {exc})"

    if len(raw) > _MAX_LOCK_BYTES:
        return "present (metadata too large to inspect)"

    try:
        data = json.loads(raw)

        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or type(data.get("pid")) is not int
            or data["pid"] <= 0
            or not isinstance(data.get("hostname"), str)
            or not data["hostname"]
            or not isinstance(data.get("started_at"), str)
            or not isinstance(data.get("purpose"), str)
        ):
            raise ValueError("invalid lock metadata")

        timestamp = datetime.fromisoformat(
            data["started_at"]
        )

        if (
            timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ValueError(
                "timestamp is not timezone-aware"
            )

    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
    ):
        return (
            "present (owner unknown; "
            "legacy or incomplete lock metadata)"
        )

    return (
        f"present (purpose={data['purpose']}; "
        f"PID={data['pid']}; "
        f"host={data['hostname']}; "
        f"started={data['started_at']}; "
        "process status not verified)"
    )


def _same_file(
    path: Path,
    device: int,
    inode: int,
) -> bool:
    try:
        current = path.stat(
            follow_symlinks=False
        )
    except FileNotFoundError:
        return False

    return (
        current.st_dev == device
        and current.st_ino == inode
    )


@dataclass(slots=True)
class OwnedLock:
    path: Path
    contents: bytes
    device: int
    inode: int

    def release(self) -> None:
        """Best-effort cleanup; never delete a changed lock."""
        try:
            if (
                not self.path.exists()
                and not self.path.is_symlink()
            ):
                return

            if not _same_file(
                self.path,
                self.device,
                self.inode,
            ):
                _LOGGER.warning(
                    "CardDB lock ownership changed; "
                    "leaving lock untouched: %s",
                    self.path,
                )
                return

            with self.path.open("rb") as stream:
                actual = stream.read(
                    len(self.contents) + 1
                )

            if actual != self.contents:
                _LOGGER.warning(
                    "CardDB lock metadata changed; "
                    "leaving lock untouched: %s",
                    self.path,
                )
                return

            self.path.unlink()

        except OSError:
            _LOGGER.warning(
                "Could not remove CardDB lock %s; "
                "inspect it manually",
                self.path,
                exc_info=True,
            )


def acquire_lock(
    path: str | Path,
    *,
    purpose: str,
    error_type: type[Exception],
    collision_message: str,
) -> OwnedLock:
    """Exclusively create an owned, inspectable lock file."""
    lock = Path(path)

    try:
        descriptor = os.open(
            lock,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise error_type(
            f"{collision_message}: {lock}; "
            f"{describe_lock(lock)}. "
            "Inspect with `mtg-carddb locks --database PATH`. "
            "Only remove the lock after confirming no CardDB "
            "process is still using the database."
        ) from exc

    info = os.fstat(descriptor)

    contents = (
        json.dumps(
            {
                "version": 1,
                "purpose": purpose,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "started_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "token": uuid4().hex,
            },
            sort_keys=True,
        ) + "\n"
    ).encode("utf-8")

    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())

    except BaseException:
        # This invocation exclusively created the lock.
        # Remove it after an incomplete write only if it
        # remains the same filesystem object.
        try:
            if _same_file(
                lock,
                info.st_dev,
                info.st_ino,
            ):
                lock.unlink()
        except OSError:
            _LOGGER.warning(
                "Could not clean up incomplete "
                "CardDB lock: %s",
                lock,
                exc_info=True,
            )
        raise

    return OwnedLock(
        lock,
        contents,
        info.st_dev,
        info.st_ino,
    )
