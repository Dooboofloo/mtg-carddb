# Roadmap

MTG CardDB is a personal open-source library maintained by a single developer. The goal is a dependable, useful Python API without adding infrastructure or release requirements out of proportion to the project's size.

The first public package, **v0.1.0**, is published on [PyPI](https://pypi.org/project/mtg-carddb/). Its current API and SQLite schema already cover the intended core use cases.

## Next target: v1.0.0

**The plan is to make 1.0 the next feature release, without requiring intermediate 0.2–0.9 milestones.** No broad redesign, new backend, benchmark program, or additional feature family is required first.

The short release gate:

1. **Choose the 1.0 contract.** Review the public interfaces and query semantics in [api.md](api.md). Resolve only clearly important gaps or inconsistencies; leave optional features for later. Keep the implemented SQLite schema v1 unless a specific blocker is found.
2. **Update the public-facing documentation.** Make README, API reference, schema notes, changelog, package metadata, and release status consistent with 1.0. State supported behavior and known limitations accurately.
3. **Run the existing checks.** Run the offline tests and ensure GitHub CI passes. Build distributions and perform the established clean-install smoke test. Repeat a full Scryfall import only if acquisition, normalization, or storage has materially changed since the last successful real-data run, or if a specific compatibility concern appears.
4. **Publish deliberately.** Bump `pyproject.toml` to `1.0.0`, tag the verified commit `v1.0.0`, create the GitHub release, then manually authorize PyPI publication with the existing workflow. Verify the published package installs.

The [release guide](releasing.md) contains the operational commands. The checklist is intentionally modest and may evolve with experience.

## What 1.0 means

Version 1.0 is a commitment to **documented Python API compatibility**, not a promise that the software is bug-free, feature-complete, or suitable for every production workload.

- Public names, signatures, return types, error behavior, and query semantics are considered stable within the 1.x line. Intentional incompatible changes normally require a new major version; compatible additions and fixes can ship in 1.x releases.
- The underlying SQLite schema has its **own version number** and is an implementation/storage contract distinct from the Python package version. A newer schema need not be backward-compatible at the SQL level; readers must detect unsupported versions and provide a clear path to rebuild or upgrade. Automated migrations are not promised by 1.0.
- CardDB continues to operate on Oracle identities with immutable set results. It does not become a deck-construction rules engine or a complete printing inventory.
- Known limitations may remain. Bug reports and upstream Scryfall changes will guide iterative maintenance.

## After 1.0: candidate work, not prerequisites

These are opportunities rather than a fixed release schedule:

| Area | Potential direction |
| --- | --- |
| Real-world integration | Use the published package in the original Commander generator and improve awkward public interfaces based on that experience |
| Correctness and performance | Focused regression fixtures, query-equivalence checks, and benchmarks when needed to investigate results or bottlenecks |
| Data reproducibility | Optional source-export retention or reproducible build manifests |
| Additional data | Printing-specific details, related-card relationships, and carefully scoped new predicates |
| Consumer extensions | External historical-format tools, user classifications, and other applications built on CardDB rather than embedded into its core |
| Developer experience | Better examples, typing, diagnostics, and documentation based on user feedback |

Feature work should preserve the package's central idea: **local indexed queries that return ordinary Python sets**, while leaving application rules to the applications using it.

## Maintenance approach

- Keep CI and the existing offline tests as the ordinary baseline. Add regression tests for bugs and material behavioral changes rather than imposing arbitrary coverage targets.
- Run expensive live-data audits when they have a concrete purpose. Do not require new full imports for documentation-only changes.
- Release patches for fixes, minors for backward-compatible features, and new majors for deliberately incompatible API revisions after 1.0.
- Maintain human review and control over merges, GitHub releases, and PyPI publication.

This roadmap describes intent, not a guarantee of dates or future features.
