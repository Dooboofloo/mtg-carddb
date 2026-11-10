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
