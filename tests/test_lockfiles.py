"""Lock ownership, diagnostics and safe best-effort cleanup."""

import json
import os

import pytest

from mtg_carddb import lockfiles


def _acquire(path, purpose="workflow"):
    return lockfiles.acquire_lock(
        path,
        purpose=purpose,
        error_type=RuntimeError,
        collision_message="lock already exists",
    )


def test_acquire_writes_diagnostic_metadata_and_release(
    tmp_path,
):
    path = tmp_path / "cards.sqlite.workflow.lock"
    owner = _acquire(path)

    try:
        info = json.loads(
            path.read_text(encoding="utf-8")
        )

        assert info["pid"] == os.getpid()
        assert info["purpose"] == "workflow"
        assert info["token"]
        assert "PID=" in lockfiles.describe_lock(path)

    finally:
        owner.release()

    assert not path.exists()
    assert lockfiles.describe_lock(path) == "absent"


def test_collision_reports_owner_and_preserves_existing_file(
    tmp_path,
):
    path = tmp_path / "cards.sqlite.lock"
    owner = _acquire(path)
    original = path.read_bytes()

    try:
        with pytest.raises(
            RuntimeError,
            match="PID=.*mtg-carddb locks",
        ):
            _acquire(path)

        assert path.read_bytes() == original

    finally:
        owner.release()


def test_legacy_lock_is_described_without_deletion(
    tmp_path,
):
    path = tmp_path / "cards.sqlite.lock"
    path.write_text("legacy writer")

    assert (
        "owner unknown"
        in lockfiles.describe_lock(path)
    )

    with pytest.raises(
        RuntimeError,
        match="owner unknown",
    ):
        _acquire(path)

    assert path.read_text() == "legacy writer"


def test_oversized_lock_metadata_is_not_read_unbounded(
    tmp_path,
):
    path = tmp_path / "cards.sqlite.lock"
    path.write_bytes(b"x" * 100000)

    assert (
        "too large"
        in lockfiles.describe_lock(path)
    )


def test_owner_does_not_remove_external_replacement(
    tmp_path, caplog,
):
    path = tmp_path / "cards.sqlite.lock"
    alternate = tmp_path / "replacement"

    owner = _acquire(path)

    alternate.write_bytes(path.read_bytes())
    os.replace(alternate, path)

    owner.release()

    assert path.exists()
    assert "ownership changed" in caplog.text


def test_owner_does_not_remove_modified_lock(
    tmp_path, caplog,
):
    path = tmp_path / "cards.sqlite.lock"
    owner = _acquire(path)

    path.write_text('"changed"')
    owner.release()

    assert path.exists()
    assert "metadata changed" in caplog.text


def test_lock_release_failure_does_not_raise(
    tmp_path, monkeypatch, caplog,
):
    path = tmp_path / "cards.sqlite.lock"
    owner = _acquire(path)

    original_unlink = type(path).unlink

    def blocked(self, *args, **kwargs):
        if self == path:
            raise PermissionError(
                "simulated lock denied"
            )
        return original_unlink(
            self, *args, **kwargs
        )

    monkeypatch.setattr(
        type(path),
        "unlink",
        blocked,
    )

    owner.release()

    assert (
        "Could not remove CardDB lock"
        in caplog.text
    )
    assert path.exists()


def test_incomplete_lock_creation_is_cleaned(
    tmp_path, monkeypatch,
):
    path = tmp_path / "cards.sqlite.lock"

    def fail_fsync(_):
        raise OSError(
            "simulated disk failure"
        )

    monkeypatch.setattr(
        lockfiles.os,
        "fsync",
        fail_fsync,
    )

    with pytest.raises(
        OSError,
        match="disk failure",
    ):
        _acquire(path)

    assert not path.exists()
