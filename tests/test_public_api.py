"""Public import surface and development packaging configuration."""

from __future__ import annotations

import os
from pathlib import Path
import tomllib

import pytest

import mtg_carddb as api
from mtg_carddb import (
    acquisition,
    activation,
    builder,
    database,
    freshness,
    reference_sources,
    storage,
    workflow,
)


def test_public_query_and_record_exports_match_implementations():
    assert api.CardDatabase is database.CardDatabase
    assert api.Card is database.Card
    assert api.DatabaseInfo is database.DatabaseInfo
    assert api.SourceInfo is storage.SourceInfo


def test_public_lifecycle_exports_match_implementations():
    assert api.initialize_database is workflow.initialize_database
    assert api.update_database is workflow.update_database
    assert (
        api.DatabaseWorkflowResult
        is workflow.DatabaseWorkflowResult
    )
    assert api.WorkflowProgress is workflow.WorkflowProgress
    assert api.ProgressCallback is workflow.ProgressCallback
    assert api.SnapshotBuildResult is builder.SnapshotBuildResult


def test_public_freshness_exports_match_implementations():
    assert api.database_freshness is freshness.database_freshness
    assert api.DatabaseFreshness is freshness.DatabaseFreshness
    assert api.DEFAULT_STALE_AFTER_DAYS == 7.0


def test_public_errors_match_implementations():
    assert (
        api.DatabaseNotInitializedError
        is freshness.DatabaseNotInitializedError
    )
    assert api.DatabaseWorkflowError is workflow.DatabaseWorkflowError
    assert api.DatabaseOpenError is storage.DatabaseOpenError
    assert (
        api.DatabaseCompatibilityError
        is storage.DatabaseCompatibilityError
    )
    assert api.SnapshotBuildError is builder.SnapshotBuildError
    assert (
        api.BulkAcquisitionError
        is acquisition.BulkAcquisitionError
    )
    assert (
        api.SnapshotActivationError
        is activation.SnapshotActivationError
    )
    assert (
        api.ReferenceArchiveError
        is reference_sources.ReferenceArchiveError
    )


def test_all_exports_exist_without_duplicates():
    assert len(api.__all__) == len(set(api.__all__))
    assert all(hasattr(api, name) for name in api.__all__)
    assert not any(name.startswith("_") for name in api.__all__)


def test_default_database_path_uses_current_environment(
    tmp_path, monkeypatch,
):
    base = tmp_path / "new-data-root"

    variable = (
        "LOCALAPPDATA"
        if os.name == "nt"
        else "XDG_DATA_HOME"
    )

    monkeypatch.setenv(variable, str(base))

    expected = base / "mtg-carddb" / "carddb.sqlite"

    assert api.default_database_path() == expected
    assert database._default_database_path() == expected

    # Path discovery never initializes data.
    assert not base.exists()


def test_missing_default_database_error_through_public_api(
    tmp_path, monkeypatch,
):
    base = tmp_path / "not-initialized"

    variable = (
        "LOCALAPPDATA"
        if os.name == "nt"
        else "XDG_DATA_HOME"
    )

    monkeypatch.setenv(variable, str(base))

    with pytest.raises(
        api.DatabaseNotInitializedError,
        match="init",
    ):
        api.CardDatabase.open(warn_if_stale=False)

    assert not base.exists()


def test_pyproject_declares_installed_command_and_src_package():
    project_root = Path(__file__).resolve().parents[1]

    config = tomllib.loads(
        (project_root / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert config["project"]["name"] == "mtg-carddb"
    assert config["project"]["requires-python"] == ">=3.11"

    assert (
        config["project"]["scripts"]["mtg-carddb"]
        == "mtg_carddb.cli:main"
    )

    assert "src/mtg_carddb" in (
        config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    )

    assert "build>=1.2" in (
        config["project"]["optional-dependencies"]["dev"]
    )
