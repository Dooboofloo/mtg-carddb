# Contributing to MTG CardDB

Contributions, bug reports, regression tests, and documentation corrections are welcome.

MTG CardDB is a local-first Python library. Its central guarantees are immutable Oracle-ID query results, deterministic normalization, read-only database access, and explicit, safe database updates.

## Development setup

Python 3.11 or newer is required.

Clone the repository, create a virtual environment if desired, and install the development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Run the offline test suite:

```powershell
python -m pytest -q --disable-socket
```

The ordinary test suite must not depend on the availability of Scryfall or any other external service. Use temporary files, synthetic fixtures, and mocked HTTP responses when testing network or import behavior.

## Before submitting changes

- Add regression tests for corrected bugs and new public behavior.
- Run the complete offline test suite.
- Keep imports and database queries free of implicit network requests.
- Check Windows, Linux, and macOS considerations, especially filesystem replacement, locks, path handling, and timestamps.
- Before a release, review and update documentation affected by public behavior changes; not every intermediate technical commit needs a docs change.
- Do not commit generated SQLite databases, downloaded Scryfall exports, local credentials, virtual environments, or build artifacts.

GitHub Actions tests the supported Python versions on all three operating systems and verifies wheel and source-distribution builds.

## Public API and schema

The public API is documented in [docs/api.md](docs/api.md). Prefer exposing stable library interfaces through `mtg_carddb.__init__`.

Queries should continue to return native `frozenset[str]` Oracle-ID memberships. Do not substitute printing IDs, mutable result collections, or custom query objects without an explicit API review.

The persisted schema is documented in [docs/schema.md](docs/schema.md).

**The SQL block in `docs/schema.md` must match `SCHEMA_SQL` in `src/mtg_carddb/schema.py`.** This is enforced by `tests/test_schema.py`.

Do not change the SQL schema silently. A schema change requires coordinated changes to its documentation, builder, reader compatibility checks, tests, and SQLite schema version policy.

Version 1.0 establishes the documented public Python API as stable. Prefer backward-compatible additions in minor releases and reserve intentionally incompatible public API changes for a major release; the SQLite schema retains its own compatibility/versioning policy.

## Scryfall import changes

The standard workflow consumes Default Cards, Oracle Tags, the Sets API, and type catalogs.

Preserve the distinction between known-empty and missing source fields. Keep historical set, rarity, and game observations coupled. Preserve deterministic Oracle representative selection and the separate legality-selection policy.

An unsupported required source shape should produce a diagnostic failure, not fabricated values or a partially activated database.

Real Scryfall integration runs are separate from the offline test suite. Run them when normalization, acquisition, or storage changes materially, or when an upstream-compatibility issue requires one; they are not required for documentation-only changes. Avoid unnecessary API requests; use bulk exports rather than per-card downloads.

## Reporting issues

When reporting a bug, include the CardDB version, Python version, operating system, the operation attempted, and a minimal reproducible example or traceback.

For database build issues, source timestamps, source types, relevant hashes, and sanitized error messages are useful. Do not upload personal files or unrelated database contents without checking what they contain.

For security-sensitive reports, avoid posting exploit details publicly before there is an opportunity to investigate.

## Releases

See the [roadmap](docs/roadmap.md) for post-1.0 directions and [release guide](docs/releasing.md) for the maintainer-controlled GitHub and PyPI workflow. CI and targeted regression checks are the normal quality baseline; exhaustive audits and benchmark gates are not prerequisites for routine releases.

The project is licensed under the [MIT License](LICENSE). Card data supplied by Scryfall is subject to its own terms and attribution requirements.
