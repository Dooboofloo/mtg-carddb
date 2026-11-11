# Changelog

Notable user-facing changes to MTG CardDB are recorded here.

The project follows a `MAJOR.MINOR.PATCH` version scheme. Releases below 1.0.0 may refine the public API, while SQLite schema compatibility is tracked separately using `PRAGMA user_version`.

## Unreleased

No changes recorded yet.

## 0.1.0 — Initial release candidate

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
