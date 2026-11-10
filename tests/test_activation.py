"""Tests for validated, atomic CardDB snapshot activation."""

import pytest

from mtg_carddb import activation
from mtg_carddb.activation import (
    SnapshotActivationError,
    _validate_snapshot,
    activate_snapshot,
)


def test_activation_creates_target_without_changing_snapshot(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "installed.sqlite"
    source.write_bytes(b"verified snapshot")
    checked = []

    def validate(path):
        assert path != source
        assert path.parent == target.parent
        assert path.read_bytes() == source.read_bytes()
        checked.append(path)

    monkeypatch.setattr(
        activation, "_validate_snapshot", validate
    )

    assert activate_snapshot(source, target) == target
    assert target.read_bytes() == b"verified snapshot"
    assert source.read_bytes() == b"verified snapshot"
    assert len(checked) == 1
    assert not checked[0].exists()
    assert not (
        tmp_path / "installed.sqlite.lock"
    ).exists()


def test_activation_atomically_replaces_existing_target(
    tmp_path, monkeypatch
):
    source = tmp_path / "new.sqlite"
    target = tmp_path / "current.sqlite"
    source.write_bytes(b"new")
    target.write_bytes(b"old")

    monkeypatch.setattr(
        activation, "_validate_snapshot", lambda _: None
    )

    activate_snapshot(source, target)

    assert target.read_bytes() == b"new"
    assert source.read_bytes() == b"new"


def test_validation_failure_keeps_previous_database(
    tmp_path, monkeypatch
):
    source = tmp_path / "candidate.sqlite"
    target = tmp_path / "active.sqlite"
    source.write_bytes(b"invalid")
    target.write_bytes(b"working")

    def fail(_):
        raise SnapshotActivationError("rejected")

    monkeypatch.setattr(
        activation, "_validate_snapshot", fail
    )

    with pytest.raises(
        SnapshotActivationError, match="rejected"
    ):
        activate_snapshot(source, target)

    assert target.read_bytes() == b"working"
    assert source.read_bytes() == b"invalid"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "active.sqlite",
        "candidate.sqlite",
    ]


def test_os_replacement_failure_keeps_previous_database(
    tmp_path, monkeypatch
):
    source = tmp_path / "candidate.sqlite"
    target = tmp_path / "active.sqlite"
    source.write_bytes(b"verified")
    target.write_bytes(b"working")

    monkeypatch.setattr(
        activation, "_validate_snapshot", lambda _: None
    )

    def denied(_source, _target):
        raise PermissionError("file in use")

    monkeypatch.setattr(
        activation.os, "replace", denied
    )

    with pytest.raises(
        SnapshotActivationError,
        match="existing database unchanged",
    ):
        activate_snapshot(source, target)

    assert target.read_bytes() == b"working"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "active.sqlite",
        "candidate.sqlite",
    ]


def test_existing_activation_lock_refuses_to_run(
    tmp_path, monkeypatch
):
    source = tmp_path / "candidate.sqlite"
    target = tmp_path / "active.sqlite"
    lock = tmp_path / "active.sqlite.lock"
    source.write_bytes(b"verified")
    target.write_bytes(b"working")
    lock.write_bytes(b"another writer")

    with pytest.raises(
        SnapshotActivationError,
        match="lock already exists",
    ):
        activate_snapshot(source, target)

    assert target.read_bytes() == b"working"
    assert lock.read_bytes() == b"another writer"


def test_sqlite_sidecar_refuses_unsafe_replacement(tmp_path):
    source = tmp_path / "candidate.sqlite"
    target = tmp_path / "active.sqlite"
    source.write_bytes(b"verified")
    target.write_bytes(b"working")

    sidecar = tmp_path / "active.sqlite-wal"
    sidecar.touch()

    with pytest.raises(
        SnapshotActivationError, match="SQLite sidecar"
    ):
        activate_snapshot(source, target)

    assert target.read_bytes() == b"working"
    assert not (
        tmp_path / "active.sqlite.lock"
    ).exists()


def test_cannot_activate_snapshot_onto_itself(tmp_path):
    source = tmp_path / "candidate.sqlite"
    source.write_bytes(b"original")

    with pytest.raises(
        ValueError, match="different files"
    ):
        activate_snapshot(source, source)

    assert source.read_bytes() == b"original"


def test_validator_rejects_non_sqlite_file(tmp_path):
    file = tmp_path / "garbage.sqlite"
    file.write_bytes(b"not a sqlite file")

    with pytest.raises(SnapshotActivationError):
        _validate_snapshot(file)


def test_create_only_activation_installs_new_target(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "installed.sqlite"
    source.write_bytes(b"verified snapshot")

    monkeypatch.setattr(
        activation, "_validate_snapshot", lambda _: None
    )

    result = activate_snapshot(
        source,
        target,
        replace_existing=False,
    )

    assert result == target
    assert source.read_bytes() == b"verified snapshot"
    assert target.read_bytes() == b"verified snapshot"

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "installed.sqlite",
        "source.sqlite",
    ]


def test_create_only_activation_refuses_existing_target(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "installed.sqlite"
    source.write_bytes(b"new")
    target.write_bytes(b"existing")

    monkeypatch.setattr(
        activation, "_validate_snapshot", lambda _: None
    )

    with pytest.raises(FileExistsError):
        activate_snapshot(
            source,
            target,
            replace_existing=False,
        )

    assert target.read_bytes() == b"existing"
    assert source.read_bytes() == b"new"


def test_create_only_activation_rejects_racing_target(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "installed.sqlite"
    source.write_bytes(b"verified")

    def validate(_staged):
        # Another process creates the target after our initial
        # existence check but before the installation.
        target.write_bytes(b"other process")

    monkeypatch.setattr(
        activation, "_validate_snapshot", validate
    )

    with pytest.raises(FileExistsError):
        activate_snapshot(
            source,
            target,
            replace_existing=False,
        )

    assert target.read_bytes() == b"other process"
    assert source.read_bytes() == b"verified"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "installed.sqlite",
        "source.sqlite",
    ]


def test_guarded_replacement_allows_unchanged_database(
    tmp_path, monkeypatch
):
    old = tmp_path / "old.sqlite"
    candidate = tmp_path / "new.sqlite"

    old.write_bytes(b"original")
    candidate.write_bytes(b"candidate")

    state = activation.capture_target_state(old)

    monkeypatch.setattr(
        activation,
        "_validate_snapshot",
        lambda _: None,
    )

    result = activate_snapshot(
        candidate,
        old,
        expected_target_state=state,
    )

    assert result == old
    assert old.read_bytes() == b"candidate"


def test_guard_detects_in_place_write_during_validation(
    tmp_path, monkeypatch
):
    old = tmp_path / "old.sqlite"
    candidate = tmp_path / "new.sqlite"

    old.write_bytes(b"original")
    candidate.write_bytes(b"candidate")

    state = activation.capture_target_state(old)
    original_stat = old.stat()

    def mutate(_):
        old.write_bytes(b"modified")  # Same length

        import os
        os.utime(
            old,
            ns=(
                original_stat.st_atime_ns,
                original_stat.st_mtime_ns,
            ),
        )

    monkeypatch.setattr(
        activation,
        "_validate_snapshot",
        mutate,
    )

    with pytest.raises(
        SnapshotActivationError,
        match="changed during update",
    ):
        activate_snapshot(
            candidate,
            old,
            expected_target_state=state,
        )

    assert old.read_bytes() == b"modified"
    assert candidate.read_bytes() == b"candidate"
    assert not (tmp_path / "old.sqlite.lock").exists()


def test_guard_detects_replaced_file_during_validation(
    tmp_path, monkeypatch
):
    import os

    old = tmp_path / "old.sqlite"
    candidate = tmp_path / "new.sqlite"
    replacement = tmp_path / "external.sqlite"

    old.write_bytes(b"original")
    candidate.write_bytes(b"candidate")
    replacement.write_bytes(b"original")

    state = activation.capture_target_state(old)

    def mutate(_):
        os.replace(replacement, old)

    monkeypatch.setattr(
        activation,
        "_validate_snapshot",
        mutate,
    )

    with pytest.raises(
        SnapshotActivationError,
        match="changed during update",
    ):
        activate_snapshot(
            candidate,
            old,
            expected_target_state=state,
        )

    assert old.read_bytes() == b"original"
    assert candidate.read_bytes() == b"candidate"


def test_activation_refuses_sidecar_created_during_validation(
    tmp_path, monkeypatch
):
    old = tmp_path / "old.sqlite"
    candidate = tmp_path / "new.sqlite"

    old.write_bytes(b"original")
    candidate.write_bytes(b"candidate")

    def mutate(_):
        (tmp_path / "old.sqlite-wal").write_bytes(
            b"pending"
        )

    monkeypatch.setattr(
        activation,
        "_validate_snapshot",
        mutate,
    )

    with pytest.raises(
        SnapshotActivationError,
        match="SQLite sidecar",
    ):
        activate_snapshot(candidate, old)

    assert old.read_bytes() == b"original"
    assert candidate.read_bytes() == b"candidate"


def test_failed_staging_cleanup_does_not_hide_original_error(
    tmp_path, monkeypatch, caplog
):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "active.sqlite"

    source.write_bytes(b"candidate")
    target.write_bytes(b"old")

    def invalid(_):
        raise SnapshotActivationError(
            "primary validation failure"
        )

    monkeypatch.setattr(
        activation,
        "_validate_snapshot",
        invalid,
    )

    original_unlink = type(target).unlink

    def fail_staged_unlink(self, *args, **kwargs):
        if self.name.startswith(".active.sqlite."):
            raise PermissionError(
                "cannot remove stage"
            )

        return original_unlink(
            self, *args, **kwargs
        )

    monkeypatch.setattr(
        type(target),
        "unlink",
        fail_staged_unlink,
    )

    with pytest.raises(
        SnapshotActivationError,
        match="primary validation failure",
    ):
        activate_snapshot(source, target)

    assert target.read_bytes() == b"old"
    assert (
        "Could not clean up staged CardDB file"
        in caplog.text
    )
    assert not (
        tmp_path / "active.sqlite.lock"
    ).exists()


def test_activation_lock_collision_reports_owner_without_touching_target(
    tmp_path,
):
    from mtg_carddb.lockfiles import acquire_lock

    source = tmp_path / "source.sqlite"
    target = tmp_path / "active.sqlite"
    lock = tmp_path / "active.sqlite.lock"

    source.write_bytes(b"new")
    target.write_bytes(b"old")

    owner = acquire_lock(
        lock,
        purpose="activation",
        error_type=RuntimeError,
        collision_message="locked",
    )

    try:
        with pytest.raises(
            SnapshotActivationError,
            match="PID=.*mtg-carddb locks",
        ):
            activate_snapshot(source, target)

        assert target.read_bytes() == b"old"
        assert lock.exists()

    finally:
        owner.release()
