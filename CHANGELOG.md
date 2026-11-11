# Changelog

Notable user-facing changes to MTG CardDB are recorded here.

The project follows a `MAJOR.MINOR.PATCH` version scheme. Releases below 1.0.0 may refine the public API, while SQLite schema compatibility is tracked separately using `PRAGMA user_version`.

## Unreleased

No changes recorded yet.

## 1.0.0 — Stable public API

### Changed

- Declare the existing documented Python API stable under a 1.x compatibility policy.
- Add Python-version Trove classifiers for the tested 3.11, 3.12, and 3.13 versions, and make the README Python badge read the requirement directly from `pyproject.toml`.
- Update the README, API/schema references, roadmap, and release guidance for the stable release.

### Unchanged

- No changes to the core query behavior, SQLite schema v1, source ingestion, or database lifecycle.
- Existing scope and source-provenance limitations remain; see the README and API/schema documentation.

## 0.1.0 — First public release

### Added

- Immutable, composable Oracle-ID search API.
- Read-only SQLite-backed card records and associated metadata.
- Color identity, color, type, keyword, mana value, produced-mana, legality, Oracle Tag, and historical set/rarity/game queries.
- Normalization of multiface and reversible-card records.
- Deterministic Oracle representative and legality selection.
- Scryfall Default Cards, Oracle Tags, Sets API, and type-catalog acquisition.
- Verified provenance and source-freshness reporting.
- Explicit database initialization, update, information, and lock-inspection CLI commands.
- Progress reporting during database builds.
- Validated snapshot activation, guarded updates, and lock diagnostics.
- Network, download-size, and decompression safeguards.
- Cross-platform CI and clean distribution-installation checks.

### Known limitations

- No individual printing inventory, price history, or historical Oracle text revisions.
- No automatically retained source archive for exact offline reconstruction.
- No guaranteed atomic compare-and-replace against unrelated external filesystem writers.
- Public Python API remains pre-1.0.
