# Changelog

Notable user-facing changes to MTG CardDB are recorded here.

The project follows a `MAJOR.MINOR.PATCH` version scheme. Releases below 1.0.0 may refine the public API, while SQLite schema compatibility is tracked separately using `PRAGMA user_version`.

## 1.3.0 — Representative images and resilient ingestion

### Added

- `CardDatabase.images_for(oracle_id)` and immutable `CardImages(front, back)`
  return a chosen Scryfall normal-resolution printing image (two URLs for
  physical double-faced cards). No image downloads or printing inventory.
- Recover missing type/subtype delimiters using the type and subtype catalogs
  **only when one unambiguous interpretation exists**. Recognize alternate
  separators and preserve unrecognized, explicitly delimited subtype labels
  without inventing corrected spellings; retain the original printed text.
- Report structured type-line recovery counts and bounded source examples
  in `IngestionStats`, `SnapshotBuildResult`, and successful CLI build output.

### Improved

- Prefer ordinary English paper artwork over promotional, Secret Lair,
  digital-only, and alternate-treatment printings; choose the newest eligible
  image with deterministic tie-breaking and allow special-print fallback.
  The preference is approximate, not a claim of matching Scryfall's private
  display-ranking rules exactly.
- Keep the selected Oracle mechanics and legality independent of image source.
  Reversible faces with the same Oracle ID can have different images without
  creating artificial mechanical conflicts.
- Prefer catalog-verified mechanics over recovered source anomalies when
  selecting canonical Oracle records; continue to reject actual conflicting
  rules text and other non-type mechanical data.

### Compatibility

- No additional runtime dependencies; SQLite `user_version` remains 1.
  The `card_images` table is optional, so older snapshots remain readable and
  older CardDB readers ignore the extra table on rebuilt snapshots.
- Existing snapshots return `CardImages(None, None)` for known cards until
  rebuilt; no in-place migration is required. A fresh `init` or `update` is
  necessary to populate representative images.
- Existing CardDB query APIs and result types remain unchanged.

## 1.2.1 — Scryfall source compatibility and cleanup

### Fixed

- Normalize the known `UNK` source typo `Alien Perfomer` to the `Performer` subtype for indexed classification while retaining the exact printed type line.
- Accept Scryfall type lines using the spaced ASCII ` -- ` type/subtype divider (e.g. `Legendary Creature -- Aetherborn Vampire`) while retaining the original printed text and validating types against the catalogs.
- Recognize the source-verified `UNK` novelty subtypes `Booster`, `Critter`, and `Scotsman` without relaxing validation of unknown subtypes.
- Interpret two source-verified missing type/subtype separators in `Legendary Creature Human Advisor` (`PUNK`) and `Legendary Creature Human Noble` (`SLD`) for indexing while preserving the exact source type lines.
- Audit the current default-cards export for remaining normalization errors before releasing this importer compatibility fix.
- Close lazy Scryfall bulk readers explicitly when ingestion fails, preventing leaked gzip handles from interfering with temporary-workspace cleanup on Windows.

### Compatibility

- No public API, runtime dependency, or SQLite schema changes; existing snapshots do not require rebuilding solely due to the package update.

## 1.2.0 — Fuzzy suggestions and single-card tag checks

### Added

- `CardDatabase.suggest_names(name, *, limit=5, cutoff=0.6)` for opt-in,
  dependency-free fuzzy suggestions of canonical whole-card and face names.
  Similarity compares Unicode case-folded, punctuation-normalized names and
  returns a deterministically ordered tuple, not Oracle IDs.
- `CardDatabase.has_tag(oracle_id, slug, *, include_ancestors=True)` for
  direct or hierarchical membership checks of one card. Canonical tag slugs
  and aliases follow the same error semantics as `by_tag()`.
- Focused tests for name suggestions, optional lazy catalog loading,
  punctuation and Unicode normalization, multi-parent/cyclic tags, and
  direct-versus-ancestor-inherited membership.

### Compatibility

- Existing `by_name()` and `by_tag()` results and their semantics are unchanged.
- No new runtime dependencies, SQLite schema changes, data migrations, or
  snapshot rebuilds required; all new lookups use existing schema-v1 data.

## 1.1.0 — Fast individual-name lookups

### Added

- `CardDatabase.preload_names()` for explicitly hydrating the complete name
  index when making many distinct name queries.
- A bounded per-database-handle, least-recently-used cache for the 256 most
  recent case-insensitive name lookups, including unsuccessful searches.

### Improved

- Database opening no longer eagerly materializes the complete Oracle-ID
  universe; one-off exact-name searches query SQLite directly without loading
  unrelated indexes.
- Direct Oracle-ID retrieval, resolution, membership, and database length can
  operate without constructing the complete in-memory ID universe.
- Repeat queries of a cached name avoid the previous per-query SQLite scan;
  explicit preloading retains indexed performance for batches of distinct names.
- README, getting-started guide, API reference, roadmap, and release guidance
  updated for v1.1.0.

### Compatibility

- No breaking public API changes, no new runtime dependencies, and no SQLite
  schema migration or database rebuild required (schema v1).

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
