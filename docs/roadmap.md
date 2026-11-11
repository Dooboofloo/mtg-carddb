# Roadmap

MTG CardDB is a personal open-source Python library maintained by a single developer. The goal is a dependable, useful API with proportionate testing and maintenance overhead.

## v1.0.0: stable core

The initial `0.1.0` preview introduced the implemented engine. **Version 1.0.0 establishes the documented public Python API as stable** without introducing a new query engine, storage format, or feature family.

The 1.0 baseline includes:

- Oracle-ID queries returning immutable native `frozenset[str]` results.
- A read-only local SQLite snapshot, with independently versioned schema v1.
- Typed immutable card and face records; color, type, keyword, mana, legality, tag, and set/rarity/game queries.
- Scryfall bulk ingestion, explicit `init` and `update`, provenance and freshness metadata, and guarded snapshot activation.
- Offline tests, cross-platform CI, built distribution checks, and maintainer-controlled publication.

**Compatibility promise:** Public names, method signatures, record fields, return types, errors, and documented query semantics remain backward-compatible throughout the 1.x line, except where correcting a bug necessarily changes erroneous behavior. Additive features normally warrant a minor release; fixes and documentation improvements can ship in patch releases. Deliberately incompatible changes normally require 2.0.

The **SQLite schema version** is separate from the package version. The reader detects unsupported schemas and reports compatibility failures; automatic migration between schema versions is not a 1.0 guarantee. As with v0.1.0, updates are explicit rather than automatic.

Version 1.0 is not a claim of bug-free software or exhaustive validation. It does not imply a printing inventory, historical Oracle-text or ban-list archive, or deckbuilding rules engine.

## v1.1.0: fast individual-name retrieval

Version 1.1.0 makes `CardDatabase.open()` and single-card name lookups practical
without building the complete in-memory name index or Oracle-ID universe.
Exact names, including individual face names, are matched case-insensitively;
result sets and all existing query semantics are unchanged. A bounded
per-handle cache accelerates repeated searches of the same name, while the
additive `preload_names()` method preserves the full indexed path for batches
of distinct names. Existing schema-v1 snapshots remain compatible.

This is a backward-compatible minor release, with no changes to the data
source pipeline, storage schema, or required runtime dependencies.

## Possible work after 1.1

These are candidates, not prerequisites or a fixed release schedule.

| Area | Potential direction |
| --- | --- |
| Downstream adoption | Use the published package in the original Commander generator; improve integration pain points through backward-compatible APIs |
| Query correctness and performance | Additional targeted regression fixtures, performance benchmarks, and (if needed) persisted name-index optimization for many uncached distinct names |
| Reproducible datasets | Optional source-export retention and deterministic rebuild manifests |
| Additional data | Printing-specific records, related-card links, and narrowly scoped query predicates |
| Extensions | Historical-format tooling, custom classifications, and other applications outside the core |
| Developer experience | Improved examples, typing, diagnostics, and documentation based on feedback |

The defining interface stays the same: **local indexed searches returning ordinary Python sets**, with application-specific policies left to consumers.

## Release and documentation practice

- Use existing offline tests and CI as the normal release gate; add regression tests for meaningful behavioral changes.
- Reserve expensive live Scryfall imports for acquisition, normalization, storage changes, or a concrete compatibility concern.
- **Before publishing, review and update affected documentation** (README, API, schema, changelog, and release notes as appropriate). Documentation does **not** need edits for every intermediate technical commit.
- Keep GitHub and PyPI publishing under the maintainer's explicit control; [the release guide](releasing.md) documents the workflow.

The roadmap describes intent, not a promise of dates or features.
