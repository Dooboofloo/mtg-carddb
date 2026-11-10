"""CLI behavior without network downloads or SQLite builds."""

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from mtg_carddb import cli


def build_result(path, *, operation):
    return SimpleNamespace(
        operation=operation,
        path=path,
        build=SimpleNamespace(
            cards=42,
            sets=2,
            tags=3,
            printings=50,
            provenance_sources=10,
        ),
    )


class FakeDatabase:
    def __init__(self, age_days):
        timestamp = (
            datetime.now(timezone.utc)
            - timedelta(days=age_days)
        ).isoformat() if age_days is not None else None

        self.metadata = SimpleNamespace(
            schema_version=1,
            builder_version="snapshot-builder-v1",
            created_at="2026-11-10T12:00:00Z",
            sources=tuple(
                SimpleNamespace(
                    source_type=kind,
                    source_updated_at=timestamp,
                )
                for kind in ("default_cards", "oracle_tags")
            ) if timestamp else (),
        )
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True

    def __len__(self):
        return 42

    def info(self):
        return self.metadata


def fake_database(monkeypatch, age_days):
    instance = FakeDatabase(age_days)

    def open_fake(path, *, warn_if_stale):
        assert isinstance(path, Path)
        assert warn_if_stale is False
        return instance

    monkeypatch.setattr(
        cli.CardDatabase,
        "open",
        open_fake,
    )

    return instance


def test_init_creates_default_parent(
    tmp_path, monkeypatch, capsys
):
    target = (
        tmp_path / "not-yet-created" / "carddb.sqlite"
    )

    monkeypatch.setattr(
        cli, "_default_database_path",
        lambda: target,
    )

    called = []

    def initialize(path, *, on_progress=None):
        called.append(path)
        assert path.parent.is_dir()
        return build_result(path, operation="init")

    monkeypatch.setattr(
        cli, "initialize_database", initialize
    )

    assert cli.main(["init"]) == 0
    assert called == [target]
    assert "Cards: 42" in capsys.readouterr().out


def test_explicit_init_does_not_create_parent(
    tmp_path, monkeypatch
):
    target = (
        tmp_path / "nonexistent" / "carddb.sqlite"
    )

    def initialize(path, *, on_progress=None):
        assert path == target
        assert not path.parent.exists()
        raise FileNotFoundError(
            "target parent does not exist"
        )

    monkeypatch.setattr(
        cli, "initialize_database", initialize
    )

    assert cli.main([
        "init", "-d", str(target)
    ]) == 1

    assert not target.parent.exists()


def test_init_error_does_not_overwrite(
    tmp_path, monkeypatch, capsys
):
    target = tmp_path / "existing.sqlite"
    target.write_bytes(b"keep")

    def fail(_, *, on_progress=None):
        raise FileExistsError(
            "database already exists"
        )

    monkeypatch.setattr(
        cli, "initialize_database", fail
    )

    assert cli.main([
        "init", "-d", str(target)
    ]) == 1

    assert target.read_bytes() == b"keep"
    assert (
        "already exists"
        in capsys.readouterr().err
    )


def test_update_calls_workflow(
    tmp_path, monkeypatch, capsys
):
    target = tmp_path / "installed.sqlite"
    seen = []

    def update(path, *, on_progress=None):
        seen.append(path)
        return build_result(path, operation="update")

    monkeypatch.setattr(
        cli, "update_database", update
    )

    assert cli.main([
        "update", "--database", str(target)
    ]) == 0

    assert seen == [target]
    assert (
        "Database update:"
        in capsys.readouterr().out
    )


def test_update_missing_suggests_init(
    tmp_path, monkeypatch, capsys
):
    def update(_, *, on_progress=None):
        raise FileNotFoundError(
            "cannot update missing database"
        )

    monkeypatch.setattr(
        cli, "update_database", update
    )

    assert cli.main([
        "update",
        "-d",
        str(tmp_path / "absent.sqlite"),
    ]) == 1

    assert (
        "mtg-carddb init"
        in capsys.readouterr().err
    )


def test_info_current_is_silent(
    tmp_path, monkeypatch, capsys, caplog
):
    database = fake_database(
        monkeypatch, age_days=1
    )

    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        assert cli.main([
            "info",
            "-d",
            str(tmp_path / "cards.sqlite"),
        ]) == 0

    assert (
        "Freshness: current"
        in capsys.readouterr().out
    )
    assert caplog.records == []
    assert database.closed


def test_info_stale_warns_and_succeeds(
    tmp_path, monkeypatch, capsys, caplog
):
    fake_database(
        monkeypatch, age_days=10
    )

    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        assert cli.main([
            "info",
            "-d",
            str(tmp_path / "cards.sqlite"),
        ]) == 0

    assert (
        "Freshness: stale"
        in capsys.readouterr().out
    )
    assert "stale" in caplog.text


def test_info_stale_warning_suppressed(
    tmp_path, monkeypatch, capsys, caplog
):
    fake_database(
        monkeypatch, age_days=10
    )

    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        assert cli.main([
            "info", "--no-stale-warning"
        ]) == 0

    assert (
        "Freshness: stale"
        in capsys.readouterr().out
    )
    assert caplog.records == []


def test_info_custom_threshold(
    tmp_path, monkeypatch, capsys, caplog
):
    fake_database(
        monkeypatch, age_days=10
    )

    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        assert cli.main([
            "info",
            "--stale-after-days",
            "14",
        ]) == 0

    assert (
        "Freshness: current"
        in capsys.readouterr().out
    )
    assert caplog.records == []


def test_info_unknown_freshness_is_nonfatal(
    tmp_path, monkeypatch, capsys, caplog
):
    fake_database(
        monkeypatch, age_days=None
    )

    with caplog.at_level(
        logging.WARNING,
        logger="mtg_carddb",
    ):
        assert cli.main(["info"]) == 0

    assert (
        "Freshness: unknown"
        in capsys.readouterr().out
    )
    assert "unknown" in caplog.text


def test_info_missing_file_suggests_init(
    tmp_path, capsys
):
    target = (
        tmp_path / "does-not-exist.sqlite"
    )

    assert cli.main([
        "info", "-d", str(target)
    ]) == 1

    assert "init" in capsys.readouterr().err
    assert not target.exists()


def test_invalid_threshold_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "info",
            "--stale-after-days",
            "0",
        ])

    assert exc.value.code == 2
    assert (
        "positive"
        in capsys.readouterr().err
    )


def test_help_is_available(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "update" in capsys.readouterr().out


def test_progress_display_prints_stage_transitions(capsys):
    from mtg_carddb.workflow import WorkflowProgress

    with cli._ProgressDisplay(
        interval_seconds=60,
    ) as progress:
        progress(
            WorkflowProgress("bulk", "start")
        )
        progress(
            WorkflowProgress("bulk", "complete")
        )
        progress(
            WorkflowProgress(
                "build",
                "start",
            )
        )
        progress(
            WorkflowProgress(
                "build",
                "complete",
                "42 Oracle cards",
            )
        )

    captured = capsys.readouterr()

    assert "Downloading Scryfall bulk exports" in captured.err
    assert "Completed downloading" in captured.err
    assert "Building and validating" in captured.err
    assert "42 Oracle cards" in captured.err
    assert captured.out == ""


def test_progress_heartbeat_during_long_stage(monkeypatch):
    from threading import Event

    from mtg_carddb.workflow import WorkflowProgress

    heartbeat_seen = Event()

    def record_message(self, message):
        if "Still building and validating" in message:
            heartbeat_seen.set()

    monkeypatch.setattr(
        cli._ProgressDisplay,
        "_write",
        record_message,
    )

    with cli._ProgressDisplay(
        interval_seconds=0.01,
    ) as progress:
        progress(
            WorkflowProgress("build", "start")
        )

        assert heartbeat_seen.wait(timeout=1.0)


def test_init_no_progress_flag(
    tmp_path, monkeypatch, capsys
):
    target = tmp_path / "carddb.sqlite"

    def initialize(path, *, on_progress):
        assert path == target
        assert on_progress is None
        return build_result(
            path,
            operation="init",
        )

    monkeypatch.setattr(
        cli,
        "initialize_database",
        initialize,
    )

    assert cli.main([
        "init",
        "--database",
        str(target),
        "--no-progress",
    ]) == 0

    captured = capsys.readouterr()

    assert captured.err == ""
    assert "Cards: 42" in captured.out


def test_info_reports_oldest_bulk_source(
    tmp_path, monkeypatch, capsys
):
    database = fake_database(
        monkeypatch,
        age_days=1,
    )

    older = (
        datetime.now(timezone.utc)
        - timedelta(days=10)
    ).isoformat()

    database.metadata.sources[1].source_updated_at = older

    assert cli.main([
        "info",
        "--no-stale-warning",
    ]) == 0

    output = capsys.readouterr().out

    assert "Freshness: stale" in output
    assert "Oldest bulk source: oracle_tags" in output


def test_locks_cli_reports_owner_and_orphan_workspaces(
    tmp_path, capsys
):
    from mtg_carddb.lockfiles import acquire_lock

    target = tmp_path / "carddb.sqlite"

    workspace = tmp_path / ".carddb-build-abandoned"
    workspace.mkdir()

    staged = tmp_path / ".carddb.sqlite.abandoned.sqlite"
    staged.write_bytes(b"partial snapshot")

    lock = tmp_path / "carddb.sqlite.workflow.lock"

    owner = acquire_lock(
        lock,
        purpose="workflow",
        error_type=RuntimeError,
        collision_message="locked",
    )

    try:
        assert cli.main([
            "locks",
            "-d",
            str(target),
        ]) == 0

        output = capsys.readouterr().out

        assert "Workflow lock:" in output
        assert "PID=" in output
        assert "Activation lock:" in output
        assert "absent" in output
        assert str(workspace) in output
        assert str(staged) in output
        assert "manual cleanup" in output

        assert lock.exists()
        assert workspace.exists()
        assert staged.exists()

    finally:
        owner.release()


def test_locks_cli_describes_legacy_lock_without_removing_it(
    tmp_path, capsys
):
    target = tmp_path / "carddb.sqlite"
    lock = tmp_path / "carddb.sqlite.lock"

    lock.write_text("legacy lock")

    assert cli.main([
        "locks",
        "--database",
        str(target),
    ]) == 0

    assert (
        "owner unknown"
        in capsys.readouterr().out
    )
    assert lock.read_text() == "legacy lock"
