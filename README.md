# MTG CardDB

[![PyPI version](https://img.shields.io/pypi/v/mtg-carddb)](https://pypi.org/project/mtg-carddb/)
[![Python requirement](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FDooboofloo%2Fmtg-carddb%2Fmain%2Fpyproject.toml)](https://pypi.org/project/mtg-carddb/)
[![CI](https://github.com/Dooboofloo/mtg-carddb/actions/workflows/ci.yml/badge.svg)](https://github.com/Dooboofloo/mtg-carddb/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Dooboofloo/mtg-carddb/blob/main/LICENSE)

**MTG CardDB** is a local-first Python library for searching *Magic: The Gathering* card data with ordinary Python set operations.

It builds an indexed SQLite snapshot from Scryfall data. Queries return immutable sets of **Oracle IDs**, so you can intersect, union, and subtract card pools without writing SQL or contacting a remote service. The database is downloaded and updated explicitly; importing the package and querying an existing database work offline.

**Requirements:** Python 3.11+ · No third-party runtime dependencies · MIT-licensed code

## Install

```bash
python -m pip install mtg-carddb
```

Initialize a local database once (internet access required):

```bash
mtg-carddb init
```

This downloads Scryfall's Default Cards and Oracle Tags exports and supporting reference catalogs, then builds and validates a SQLite snapshot. The initial build can take several minutes. Subsequent queries use the local database.

To refresh it later, run:

```bash
mtg-carddb update
```

`update` requires an existing compatible database, builds a replacement before activation, and does not silently change the snapshot used by an already-open `CardDatabase` handle.

See the [getting started guide](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/getting-started.md) for database paths, custom locations, CLI options, and troubleshooting.

## Look up a card by name

For a one-off lookup, CardDB opens the local SQLite snapshot without loading
its full card universe or any inverted indexes:

```python
from mtg_carddb import CardDatabase

with CardDatabase.open() as db:
    for card in db.resolve(db.by_name("Sol Ring")):
        print(card.name, card.oracle_id)
```

`by_name()` returns a `frozenset[str]` of Oracle IDs, matching a whole-card
name or an individual face name without regard to case. Unknown names return
an empty set. The first lookup of a distinct name queries SQLite; up to 256
recent names, **including misses**, are cached per open database handle for
fast repeats. The cache uses least-recently-used eviction. For many **distinct**
names in one session, call `db.preload_names()` once to load the full in-memory
name index and speed up subsequent queries. This is optional and does not
change returned results.

Existing schema-v1 databases work unchanged; no `init` or `update` is required
to use the faster lookup path.

## Query cards

```python
from mtg_carddb import CardDatabase

with CardDatabase.open() as db:
    candidates = (
        db.within_identity("BG")
        & db.by_type("Creature")
        & db.by_legality("commander", "legal")
        & db.mana_value_between(2, 5)
    )

    print(f"Found {len(candidates)} cards")

    for card in db.resolve(candidates)[:10]:
        print(card.name, card.mana_value, card.type_line)
```

Every card search returns a `frozenset[str]` of Scryfall Oracle UUIDs. Use Python's `&`, `|`, `-`, and `^` operators to combine results; call `db.resolve(...)` when you need immutable card records. Results are Oracle identities, **not individual printings**.

### Other queries

```python
with CardDatabase.open() as db:
    # Exactly two colors, versus any subset of those colors
    exactly_golgari = db.exact_identity("BG")
    golgari_eligible = db.within_identity("BG")

    # Names, supertypes, and exclusions compose with other searches
    legendary_creatures = db.by_supertype("Legendary") & db.by_type("Creature")
    not_banned = legendary_creatures - db.by_legality("commander", "banned")

    # Historical observations preserve set, rarity, and game together
    paper_rares = db.by_set_rarity("MKM", "rare", game="paper")

    # Discover available Oracle Tag slugs before querying them
    if db.tag_slugs:
        slug = sorted(db.tag_slugs)[0]
        tagged = db.by_tag(slug)  # Includes descendants by default
        direct = db.by_tag(slug, include_descendants=False)
```

Tags and their hierarchy depend on the installed Scryfall Oracle Tags snapshot. The library reports source legality and card properties; it does not implement deck-construction rules or user-specific bans.

## What it supports

- **Fast exact-name lookup:** low-overhead first access, a bounded per-handle cache for repeated names, and optional full name-index preloading.
- **Composable inverted-index searches:** color, color identity, card types and subtypes, supertypes, keywords, mana value, produced mana, layouts, and other documented predicates.
- **Oracle Tags:** direct classification assignments and optional descendant expansion.
- **Legality and printing history:** current Scryfall format statuses and historical set/rarity/game observations, without conflating unrelated printings.
- **Multifaced cards:** normalized, immutable card and face records, including reversible layouts.
- **SQLite snapshots:** read-only access, explicit initialization/updates, source provenance, and freshness diagnostics.
- **CLI and Python lifecycle APIs:** `init`, `update`, `info`, and `locks`, with optional custom database paths.

CardDB is a mechanical card-data library, **not** a rules engine, full collection/printing database, or deckbuilder. It does not reconstruct historical Oracle text or dated ban lists. Source exports are not automatically archived for later byte-identical reconstruction.

## Documentation

- [Getting started and CLI guide](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/getting-started.md) — paths, options, example queries, and troubleshooting
- [Python API reference](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/api.md) — public objects, query semantics, lifecycle, and errors
- [SQLite schema and import specification](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/schema.md) — normalization, relationships, and source guarantees
- [Roadmap](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/roadmap.md) — current milestones and possible follow-on work
- [Contributing](https://github.com/Dooboofloo/mtg-carddb/blob/main/CONTRIBUTING.md) · [Changelog](https://github.com/Dooboofloo/mtg-carddb/blob/main/CHANGELOG.md) · [Release guide](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/releasing.md)

**Version 1.1.0** adds a fast path for exact-name retrieval and a bounded name cache while preserving the stable 1.x API over SQLite schema v1. Public names, return types, and documented query semantics follow semantic versioning: compatible changes can ship in 1.x, while intentionally incompatible API changes normally require a new major version. Database files have their own schema version and compatibility checks. See the [roadmap](https://github.com/Dooboofloo/mtg-carddb/blob/main/docs/roadmap.md) for future directions.

## Development

For a source checkout:

```bash
git clone https://github.com/Dooboofloo/mtg-carddb.git
cd mtg-carddb
python -m pip install -e ".[dev]"
python -m pytest -q --disable-socket
```

CI runs offline tests on Windows, Linux, and macOS with Python 3.11–3.13, and validates wheel and source-distribution installations. Code and releases remain maintainer-reviewed and human-published.

## License and attribution

The original library code is released under the [MIT License](https://github.com/Dooboofloo/mtg-carddb/blob/main/LICENSE).

Card data comes from [Scryfall](https://scryfall.com/) and remains subject to applicable third-party terms. *Magic: The Gathering* and related marks belong to Wizards of the Coast. MTG CardDB is not affiliated with or endorsed by Scryfall or Wizards of the Coast.
