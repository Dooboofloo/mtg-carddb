# Releasing MTG CardDB

This document describes the checks and publication procedure for an MTG CardDB release.

The initial planned release is 0.1.0. The Python package version and SQLite schema version are independent; 0.1.0 currently writes and reads SQLite schema version 1.

## Release requirements

Before creating a tag:

- Confirm the intended API and schema documentation matches the code.
- Confirm all offline tests pass.
- Confirm GitHub Actions is green on the release commit.
- Confirm wheel and source-distribution builds succeed.
- Confirm clean wheel installation and both CLI entry points work.
- Confirm the release has had a recent successful real-Scryfall import when acquisition or ingestion has changed.
- Review release notes, known limitations, license, attribution, and distribution contents.
- Check that no credentials, downloaded bulk files, or unintended local data are included.
- Decide whether the release is a preview, prerelease, or stable publication.

Do not tag an unverified commit or publish a distribution from a different source revision.

## 1. Check the working tree

From the repository root:

```powershell
git status
git diff --check
git log -1 --oneline
```

The release should come from a clean, committed working tree.

Verify that the version in `pyproject.toml` is the intended release version.

Before changing the SQLite schema, check the matching schema version in `src/mtg_carddb/schema.py` and the compatibility policy in `src/mtg_carddb/storage.py`.

## 2. Run offline tests

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q --disable-socket
```

These tests must not reach Scryfall.

A change to the normalized database schema must retain the exact correspondence between `SCHEMA_SQL` and the first SQL code block in `docs/schema.md`.

Do not use the number of passing tests alone as a compatibility guarantee.

## 3. Build clean distributions

Remove older local distributions so that old versions cannot accidentally be included:

```powershell
Remove-Item .\dist -Recurse -Force -ErrorAction SilentlyContinue
python -m build
```

Select the newly built wheel and source distribution:

```powershell
$wheel = Get-ChildItem .\dist\*.whl | Select-Object -First 1
$sdist = Get-ChildItem .\dist\*.tar.gz | Select-Object -First 1

if (-not $wheel -or -not $sdist) {
    throw "Expected both a wheel and source distribution"
}

python -m twine check --strict $wheel.FullName $sdist.FullName
```

Confirm that the artifacts have the intended version and that exactly the expected archives are present.

Inspect the contents if packaging files or dependencies changed:

```powershell
python -m zipfile -l $wheel.FullName
tar -tzf $sdist.FullName
```

The wheel should contain the installed library and metadata, not downloaded Scryfall datasets, caches, test fixtures, or secrets.

## 4. Test installation independently

Create a new virtual environment outside the repository:

```powershell
$smoke = Join-Path $env:TEMP (
    "mtg-carddb-release-" + [guid]::NewGuid().ToString("N")
)

python -m venv $smoke

$python = Join-Path $smoke "Scripts\python.exe"
$command = Join-Path $smoke "Scripts\mtg-carddb.exe"

& $python -m pip install --no-deps $wheel.FullName

Push-Location $env:TEMP

& $python -I -c "import mtg_carddb as m; print(m.default_database_path()); print(m.CardDatabase); print(m.initialize_database)"

& $python -m mtg_carddb --help
& $command --help
& $command locks --database (
    Join-Path $env:TEMP "carddb-release-check.sqlite"
)

Pop-Location
```

Use another clean virtual environment for a source-distribution installation when packaging configuration has changed. The automated CI distribution job performs both installation types.

These smoke tests should not download Scryfall data.

## 5. Verify live-data compatibility

This is separate from ordinary CI.

Use `mtg-carddb init --database PATH` with a fresh temporary destination when a new full build is warranted. Check successful acquisition, reference capture, import, snapshot validation, and final activation.

Record the Scryfall source timestamps and import counts used for the validation in the release notes or associated validation log.

The normal workflow records source provenance in SQLite but deletes temporary source files. A successful build is not sufficient evidence that the original source bytes have been archived for reproducibility.

When auditing upstream changes, compare selected normalized Oracle identities and historical observations against independent source records. Record which checks were performed rather than implying every possible equivalence property was independently verified.

## 6. Confirm CI

Push the release-preparation commit and inspect GitHub Actions.

The release commit must pass:

- Ubuntu, Windows, and macOS on supported Python versions.
- Offline tests with sockets disabled.
- Wheel and source-distribution builds.
- Strict distribution metadata validation.
- Clean installation smoke tests.

Wait for the final result before creating the release tag.

## 7. Create the release tag

For version 0.1.0, once the intended commit is verified:

```powershell
git tag -a v0.1.0 -m "MTG CardDB 0.1.0"
git push origin v0.1.0
```

Do not reuse or move a published version tag. If a release needs corrections after publication, create a new version.

## 8. Publish a GitHub release

In the repository's GitHub Releases interface:

1. Draft a new release using the verified version tag.
2. Summarize the features, installation requirements, and known limitations from `CHANGELOG.md`.
3. Mark it as a prerelease if the release is intended as an early preview.
4. Attach the validated wheel and source distribution if providing downloadable archives.
5. Review the page and publish.

Building and attaching artifacts is different from publishing them to a package index.

## 9. Optional PyPI publication

PyPI publication is a separate decision and is not configured by the current CI workflow.

Before publishing:

- Verify that the intended PyPI project name is available or owned by the maintainer.
- Review the package metadata and the exact artifacts to upload.
- Prefer PyPI Trusted Publishing with GitHub Actions OIDC over storing long-lived package-index credentials.
- Ensure publication is limited to an explicitly authorized release event.
- Do not publish a version number that has already been uploaded to PyPI.

See the official PyPI publishing documentation:

https://docs.pypi.org/publishing/publishing-to-pypi/

The project's standard CI must remain free of publication credentials and permissions.

## 10. Post-release verification

After publication:

- Confirm the GitHub release and its source commit are correct.
- Confirm the displayed release notes and README are accurate.
- If published to PyPI, install that exact released version in a fresh environment and exercise the CLI.
- Record reported regressions and compatibility issues in the issue tracker.
- Prepare a new patch version for any necessary corrections.

Do not assume a released package can recreate identical Scryfall data at a later date without archived source inputs.
