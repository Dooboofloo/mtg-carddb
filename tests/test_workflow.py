"""Offline init/update tests. No Scryfall requests or real imports."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from mtg_carddb import activation, workflow
from mtg_carddb.builder import SnapshotBuildResult
from mtg_carddb.workflow import (
    DatabaseWorkflowError,
    initialize_database,
    update_database,
)


def fake_pipeline(monkeypatch):
    steps = []

    def acquire(directory):
        steps.append("bulk")
        directory.mkdir()

        cards = directory / "cards.gz"
        tags = directory / "tags.gz"

        cards.write_bytes(b"cards")
        tags.write_bytes(b"tags")

        return SimpleNamespace(
            cards_path=cards,
            tags_path=tags,
            bulk_sources=("verified-manifest",),
        )

    def capture(directory):
        steps.append("references")
        directory.mkdir()

        return SimpleNamespace(directory=directory)

    def build(
        *,
        cards_path,
        tags_path,
        output_path,
        bulk_sources,
        reference_archive,
    ):
        steps.append("build")

        assert cards_path.read_bytes() == b"cards"
        assert tags_path.read_bytes() == b"tags"
        assert bulk_sources == ("verified-manifest",)
        assert reference_archive.directory.is_dir()

        output_path.write_bytes(b"new database")

        return SnapshotBuildResult(
            path=output_path,
            sets=1,
            printings=2,
            excluded_printings=0,
            cards=1,
            historical_observations=1,
            tags=1,
            taggings=1,
            art_only_oracle_ids=0,
            excluded_art_taggings=0,
            provenance_sources=10,
        )

    monkeypatch.setattr(
        workflow, "acquire_bulk_sources", acquire
    )
    monkeypatch.setattr(
        workflow, "capture_reference_sources", capture
    )
    monkeypatch.setattr(
        workflow, "build_snapshot", build
    )

    monkeypatch.setattr(
        activation, "_validate_snapshot", lambda _: None
    )

    # These test snapshots contain synthetic bytes, not SQLite.
    # The real update-target validator is tested separately.
    monkeypatch.setattr(
        workflow, "_validate_update_target", lambda _: None
    )

    return steps


def assert_workspace_clean(directory, target):
    assert not list(
        directory.glob(f".{target.stem}-build-*")
    )
    assert not Path(
        str(target) + ".workflow.lock"
    ).exists()


def test_init_creates_database_and_cleans_workspace(
    tmp_path, monkeypatch
):
    steps = fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"

    result = initialize_database(target)

    assert result.operation == "init"
    assert result.path == target
    assert result.build.path == target
    assert result.build.provenance_sources == 10
    assert target.read_bytes() == b"new database"

    assert steps == [
        "bulk", "references", "build"
    ]
    assert_workspace_clean(tmp_path, target)


def test_update_replaces_existing_database(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"old database")

    result = update_database(target)

    assert result.operation == "update"
    assert result.build.cards == 1
    assert target.read_bytes() == b"new database"
    assert_workspace_clean(tmp_path, target)


def test_init_refuses_existing_database_before_fetch(
    tmp_path, monkeypatch
):
    steps = fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"keep")

    with pytest.raises(
        FileExistsError, match="already exists"
    ):
        initialize_database(target)

    assert target.read_bytes() == b"keep"
    assert steps == []
    assert_workspace_clean(tmp_path, target)


def test_update_refuses_missing_database_before_fetch(
    tmp_path, monkeypatch
):
    steps = fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"

    with pytest.raises(
        FileNotFoundError, match="cannot update"
    ):
        update_database(target)

    assert steps == []
    assert_workspace_clean(tmp_path, target)


def test_failed_build_preserves_installed_database(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"old database")

    def fail_build(**_kwargs):
        raise ValueError("failed import")

    monkeypatch.setattr(
        workflow, "build_snapshot", fail_build
    )

    with pytest.raises(
        ValueError, match="failed import"
    ):
        update_database(target)

    assert target.read_bytes() == b"old database"
    assert_workspace_clean(tmp_path, target)


def test_failed_activation_preserves_installed_database(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"old database")

    def fail_activate(*_args, **_kwargs):
        raise RuntimeError("candidate rejected")

    monkeypatch.setattr(
        workflow, "activate_snapshot", fail_activate
    )

    with pytest.raises(
        RuntimeError, match="candidate rejected"
    ):
        update_database(target)

    assert target.read_bytes() == b"old database"
    assert_workspace_clean(tmp_path, target)


def test_workflow_lock_blocks_other_operations(
    tmp_path, monkeypatch
):
    steps = fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    lock = Path(str(target) + ".workflow.lock")
    lock.write_text("another process")

    with pytest.raises(
        DatabaseWorkflowError, match="may be running"
    ):
        initialize_database(target)

    assert steps == []
    assert lock.read_text() == "another process"


def test_init_does_not_replace_racing_target(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    actual_activate = workflow.activate_snapshot

    def race(*args, **kwargs):
        target.write_bytes(
            b"another process created this"
        )
        return actual_activate(*args, **kwargs)

    monkeypatch.setattr(
        workflow, "activate_snapshot", race
    )

    with pytest.raises(FileExistsError):
        initialize_database(target)

    assert target.read_bytes() == (
        b"another process created this"
    )
    assert_workspace_clean(tmp_path, target)


def test_init_emits_ordered_progress_events(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    events = []

    initialize_database(
        target,
        on_progress=events.append,
    )

    assert [
        (event.stage, event.status)
        for event in events
    ] == [
        ("bulk", "start"),
        ("bulk", "complete"),
        ("references", "start"),
        ("references", "complete"),
        ("build", "start"),
        ("build", "complete"),
        ("activation", "start"),
        ("activation", "complete"),
    ]

    assert "Oracle cards" in events[5].detail


def test_failed_build_does_not_report_activation(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"existing database")

    def fail_build(**_kwargs):
        raise ValueError("import failed")

    monkeypatch.setattr(
        workflow,
        "build_snapshot",
        fail_build,
    )

    events = []

    with pytest.raises(
        ValueError,
        match="import failed",
    ):
        update_database(
            target,
            on_progress=events.append,
        )

    assert events[-1].stage == "build"
    assert events[-1].status == "start"
    assert all(
        event.stage != "activation"
        for event in events
    )

    assert target.read_bytes() == b"existing database"


def test_update_refuses_unrelated_file_without_download(
    tmp_path, monkeypatch
):
    target = tmp_path / "notes.sqlite"
    original = b"important non-CardDB file"
    target.write_bytes(original)

    def no_download(_directory):
        pytest.fail(
            "must reject the target before any download"
        )

    monkeypatch.setattr(
        workflow,
        "acquire_bulk_sources",
        no_download,
    )

    with pytest.raises(
        DatabaseWorkflowError,
        match="compatible CardDB",
    ):
        update_database(target)

    assert target.read_bytes() == original
    assert_workspace_clean(tmp_path, target)


def test_update_accepts_existing_carddb_schema(tmp_path):
    from mtg_carddb.schema import staging_database

    target = tmp_path / "valid.sqlite"

    with staging_database(target) as connection:
        connection.execute(
            "INSERT INTO database_metadata "
            "(id, created_at, builder_version) VALUES (1, ?, ?)",
            (
                "2026-11-10T12:00:00+00:00",
                "snapshot-builder-v1",
            ),
        )

    workflow._check_target(target, update=True)


def test_failed_completion_callback_does_not_change_result(
    tmp_path, monkeypatch, caplog
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"old database")

    def callback(event):
        if (
            event.stage == "activation"
            and event.status == "complete"
        ):
            raise RuntimeError("display crashed")

    result = update_database(
        target,
        on_progress=callback,
    )

    assert result.operation == "update"
    assert target.read_bytes() == b"new database"
    assert "progress callback failed" in caplog.text
    assert_workspace_clean(tmp_path, target)


def test_failed_initial_callback_is_nonfatal_and_disabled(
    tmp_path, monkeypatch, caplog
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"
    received = []

    def callback(event):
        received.append(event)
        raise RuntimeError("display crashed")

    result = initialize_database(
        target,
        on_progress=callback,
    )

    assert result.operation == "init"
    assert target.read_bytes() == b"new database"
    assert len(received) == 1
    assert "progress callback failed" in caplog.text


def test_update_rejects_external_change_before_activation(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)

    target = tmp_path / "carddb.sqlite"
    target.write_bytes(b"old database")

    original_activate = workflow.activate_snapshot

    def external_edit_then_activate(*args, **kwargs):
        # After the final compatibility check, but before
        # activation's own fingerprint guard.
        target.write_bytes(b"external edit")
        return original_activate(*args, **kwargs)

    monkeypatch.setattr(
        workflow,
        "activate_snapshot",
        external_edit_then_activate,
    )

    with pytest.raises(
        activation.SnapshotActivationError,
        match="changed during update",
    ):
        update_database(target)

    assert target.read_bytes() == b"external edit"
    assert_workspace_clean(tmp_path, target)


def test_init_has_no_target_fingerprint(
    tmp_path, monkeypatch
):
    fake_pipeline(monkeypatch)

    target = tmp_path / "carddb.sqlite"
    original = workflow.activate_snapshot
    fingerprints = []

    def check(*args, **kwargs):
        fingerprints.append(
            kwargs["expected_target_state"]
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        workflow,
        "activate_snapshot",
        check,
    )

    initialize_database(target)

    assert fingerprints == [None]


def test_cleanup_error_after_success_is_warning_not_failure(
    tmp_path, monkeypatch, caplog
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"

    original_cleanup = workflow.TemporaryDirectory.cleanup

    def failing_cleanup(self):
        original_cleanup(self)
        raise PermissionError("simulated cleanup failure")

    monkeypatch.setattr(
        workflow.TemporaryDirectory,
        "cleanup",
        failing_cleanup,
    )

    result = initialize_database(target)

    assert result.operation == "init"
    assert target.read_bytes() == b"new database"
    assert (
        "Could not clean up CardDB build workspace"
        in caplog.text
    )


def test_cleanup_error_does_not_mask_original_failure(
    tmp_path, monkeypatch, caplog
):
    fake_pipeline(monkeypatch)
    target = tmp_path / "carddb.sqlite"

    original_cleanup = workflow.TemporaryDirectory.cleanup

    def failing_cleanup(self):
        original_cleanup(self)
        raise PermissionError("simulated cleanup failure")

    def failing_build(**_):
        raise ValueError("primary build failure")

    monkeypatch.setattr(
        workflow.TemporaryDirectory,
        "cleanup",
        failing_cleanup,
    )
    monkeypatch.setattr(
        workflow,
        "build_snapshot",
        failing_build,
    )

    with pytest.raises(
        ValueError,
        match="primary build failure",
    ):
        initialize_database(target)

    assert not target.exists()
    assert (
        "Could not clean up CardDB build workspace"
        in caplog.text
    )


def test_workflow_lock_collision_reports_owner_without_removing_lock(
    tmp_path,
):
    from mtg_carddb.lockfiles import acquire_lock

    target = tmp_path / "carddb.sqlite"
    lock = tmp_path / "carddb.sqlite.workflow.lock"

    owner = acquire_lock(
        lock,
        purpose="workflow",
        error_type=RuntimeError,
        collision_message="locked",
    )

    try:
        with pytest.raises(
            DatabaseWorkflowError,
            match="PID=.*mtg-carddb locks",
        ):
            with workflow._workflow_lock(target):
                pytest.fail(
                    "must not enter locked workflow"
                )

        assert lock.exists()

    finally:
        owner.release()
