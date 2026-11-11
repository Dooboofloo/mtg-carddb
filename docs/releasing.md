# Releasing MTG CardDB

MTG CardDB is published on [PyPI](https://pypi.org/project/mtg-carddb/). The first public release was `0.1.0`; **`1.0.0` is the next intended feature release**. Releases are prepared and published by the maintainer, not automatically on every push.

The Python package version and SQLite schema version are independent. The initial release uses SQLite schema version 1. Consult [the schema specification](schema.md) and the storage reader before changing that version.

## Proportionate release checks

For an ordinary release:

1. Review the changes, user-facing documentation, changelog, and version in `pyproject.toml`.
2. Run the existing offline tests. Confirm the release commit passes CI.
3. Build and validate the wheel and source distribution, and verify a clean installation. CI already exercises both distribution formats.
4. Ensure the tag points to the verified release commit, then publish the GitHub release and authorize the separate PyPI workflow.

**Extra live-data checks are change-driven.** Repeat an end-to-end Scryfall import when acquisition, normalization, or the persisted schema changes materially, or when there is a specific upstream-compatibility concern. Documentation-only changes do not require another bulk download.

This is a lightweight workflow for a solo-maintained library, not a guarantee that every possible bug has been caught. Avoid weakening the existing offline tests or bypassing a failing release check merely to ship faster.

## 1. Prepare and test

From the repository root (PowerShell):

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q --disable-socket

git status
git diff --check
git log -1 --oneline
```

Verify the working tree is clean and that `pyproject.toml`, `CHANGELOG.md`, and public docs describe the intended version. For a schema change, update `src/mtg_carddb/schema.py`, `docs/schema.md`, the builder and reader compatibility logic, and the corresponding tests together. `tests/test_schema.py` checks that the first SQL block in `docs/schema.md` matches `SCHEMA_SQL` exactly.

Build clean distributions:

```powershell
Remove-Item .\dist -Recurse -Force -ErrorAction SilentlyContinue
python -m build

$wheel = Get-ChildItem .\dist\*.whl | Select-Object -First 1
$sdist = Get-ChildItem .\dist\*.tar.gz | Select-Object -First 1
if (-not $wheel -or -not $sdist) { throw "Expected wheel and sdist" }
python -m twine check --strict $wheel.FullName $sdist.FullName
```

When package metadata or build configuration changes, inspect archive contents to avoid shipping caches, credentials, Scryfall exports, or generated SQLite databases:

```powershell
python -m zipfile -l $wheel.FullName
tar -tzf $sdist.FullName
```

CI also performs isolated wheel and source-distribution installation smoke tests. For a release, verify that CI is green on the actual release commit. You can run additional local install checks if a packaging change warrants them.

## 2. Create the release tag

For the upcoming 1.0 release, **after** committing and pushing the verified changes:

```powershell
git tag -a v1.0.0 -m "MTG CardDB 1.0.0"
git push origin v1.0.0
```

Substitute the actual version for later releases. Do not move or reuse a tag after publication; release a new version for corrections.

## 3. Publish the GitHub release

At [GitHub Releases](https://github.com/Dooboofloo/mtg-carddb/releases):

1. Draft a release from the verified tag.
2. Use the changelog to describe important changes and known limitations.
3. Use **pre-release** only when you intend that release to be a preview; the planned v1.0.0 release is intended as a normal stable release.
4. Attach the wheel and source distribution if desired, review the notes, and publish.

GitHub release assets are separate from the PyPI publication workflow, which rebuilds from the selected Git tag.

## 4. Publish to PyPI (manual approval)

The repository's [Publish to PyPI workflow](https://github.com/Dooboofloo/mtg-carddb/actions/workflows/publish.yml) is manually triggered; normal CI cannot publish. It uses a configured GitHub `pypi` environment and PyPI Trusted Publishing, without a stored long-lived PyPI token.

1. Open **Actions → Publish to PyPI → Run workflow**.
2. Run it from `main` and enter the **existing release tag**, e.g. `v1.0.0`.
3. The workflow checks out that tag, confirms the version matches `pyproject.toml`, runs offline tests, and builds and validates one wheel and one source distribution.
4. Approve the protected `pypi` environment when prompted. Only the publishing job receives the OIDC permission to upload.
5. Confirm the publication job succeeds.

Publishing a new version updates the PyPI project description from the README embedded in that release. **Changes to GitHub's README do not automatically update an already-uploaded PyPI release.** A version already uploaded to PyPI cannot be replaced with another build of the same version.

## 5. Verify the released package

In a clean environment outside the repository, install the **published** version and exercise the CLI:

```powershell
python -m pip install --upgrade "mtg-carddb==1.0.0"
mtg-carddb --help
python -c "from mtg_carddb import CardDatabase; print(CardDatabase)"
```

For later releases, substitute the released version. Check the PyPI project page and GitHub release notes. A documentation-only GitHub update does not require another release unless the PyPI description needs to change.

If the release introduces a bug, record it and publish a new patch version after testing the fix. CardDB source provenance does not imply that Scryfall exports are archived for byte-identical future rebuilds.
