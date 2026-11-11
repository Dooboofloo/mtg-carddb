# Getting started with MTG CardDB

This guide covers installation, database management, and common API usage. The [API reference](api.md) defines the exact semantics of every public query; this guide focuses on the usual workflow.

## Install from PyPI

Requires Python 3.11 or newer.

```bash
python -m pip install mtg-carddb
```

The distribution is named `mtg-carddb`, the importable module is `mtg_carddb`, and the installed command is `mtg-carddb`.

For a source checkout and development dependencies instead:

```bash
python -m pip install -e ".[dev]"
```

## Initialize the database

```bash
mtg-carddb init
```

Initialization explicitly retrieves the Scryfall Default Cards and Oracle Tags bulk exports, the Sets API catalog, and relevant reference catalogs. It constructs a SQLite snapshot, validates it, and activates it only after construction succeeds. It requires network access and may take several minutes.

The default path is:

| Platform | Path |
| --- | --- |
| Windows | `%LOCALAPPDATA%\mtg-carddb\carddb.sqlite` |
| Linux/macOS | `${XDG_DATA_HOME}/mtg-carddb/carddb.sqlite`, or `~/.local/share/mtg-carddb/carddb.sqlite` when unset |

For a custom location:

```bash
mtg-carddb init --database path/to/carddb.sqlite
```

Create the parent directory before using a custom destination. The CLI automatically creates the parent directory for the default location when needed.

## Query the snapshot

```python
from mtg_carddb import CardDatabase

with CardDatabase.open() as db:
    creatures = db.by_type("Creature")
    in_golgari = db.within_identity("BG")
    legal = db.by_legality("commander", "legal")

    ids = creatures & in_golgari & legal
    for card in db.resolve(ids)[:10]:
        print(card.name, card.oracle_id)
```

Query results are immutable sets of Oracle UUIDs. Set expressions can be passed between functions and manipulated without keeping SQL query objects alive. Resolved cards are immutable records and are returned in deterministic name/Oracle-ID order.

### Exact, subset, and containing color identity

```python
with CardDatabase.open() as db:
    exact = db.exact_identity("WU")       # Identity contains W and U, no other colors
    within = db.within_identity("WU")     # Only colors drawn from W and U, including colorless
    containing = db.containing_identity("WU")  # At least W and U, perhaps more
    green_cards = db.by_color("G")         # Printed/card colors, not Commander identity
```

### Fast individual-name lookup

A single named card does not require hydrating the database's full set of
Oracle IDs or its entire in-memory name index:

```python
with CardDatabase.open() as db:
    cards = db.resolve(db.by_name("Sol Ring"))
    for card in cards:
        print(card.name, card.oracle_id)
```

Name matches are exact and Unicode case-insensitive across whole-card and face
names. `by_name()` still returns immutable Oracle-ID sets, including an empty
set for an unknown name. The SQLite path is used for a new name; a per-handle
bounded LRU cache keeps the **256 most recently used names**, including
unsuccessful searches. Repeating a name with different casing hits the same
cache entry.

If you expect **many distinct names** within one opened database, explicitly
load the complete name index first:

```python
with CardDatabase.open() as db:
    db.preload_names()
    for name in ["Sol Ring", "Command Tower", "Arcane Signet"]:
        print(name, db.resolve(db.by_name(name)))
```

`preload_names()` trades a one-time load and memory use for very fast indexed
lookups, and does not alter matching semantics. Neither approach needs a
rebuild of an existing schema-v1 database. The name cache and its capacity are
implementation optimizations, not public 1.x compatibility guarantees.

### Name lookup and exclusions

```python
with CardDatabase.open() as db:
    ring_ids = db.by_name("Sol Ring")
    cards = (db.by_type("Artifact") & db.within_identity("BG")) - ring_ids
    for card in db.resolve(cards)[:10]:
        print(card.name)
```

`by_name()` is case-insensitive but is not substring or fuzzy search. Unknown Oracle IDs raise `KeyError` with `db[id]`; use `db.get(id)` for an optional lookup.

### Oracle Tags

```python
with CardDatabase.open() as db:
    available = sorted(db.tag_slugs)
    if available:
        slug = available[0]
        with_descendants = db.by_tag(slug)
        direct_only = db.by_tag(slug, include_descendants=False)
```

An unknown tag raises `UnknownTagError`. Tag slugs and hierarchy come from the installed Oracle Tags snapshot and may change with Scryfall source updates.

### Printing-history observations

```python
with CardDatabase.open() as db:
    ids = db.by_set_rarity("MKM", "rare", game="paper")
```

This method requires a **single matching observation** containing all three dimensions; it does not independently intersect all sets and rarities ever associated with a card. CardDB stores this relationship at the Oracle-ID level, not as a complete inventory of individual printings.

## Inspect and update

```bash
mtg-carddb info
mtg-carddb update
mtg-carddb locks
```

| Command | Action |
| --- | --- |
| `init` | Create a new database at the chosen path |
| `info` | Show snapshot metadata and freshness |
| `update` | Build a replacement for an existing, compatible database and activate it |
| `locks` | Read-only inspection of workflow locks and matching temporary artifacts |

All commands accept `--database PATH` (or `-d PATH`). `init` and `update` accept `--no-progress`. `info` accepts `--no-stale-warning` and `--stale-after-days DAYS`.

The CLI can also be run with `python -m mtg_carddb --help`.

**Updates are explicit.** Opening an existing `CardDatabase` neither downloads data nor rebuilds its snapshot. An already-open handle keeps using its original data after another process installs an update; reopen it to see the new snapshot.

## Python lifecycle API

```python
from mtg_carddb import (
    default_database_path,
    initialize_database,
    update_database,
)

path = default_database_path()
path.parent.mkdir(parents=True, exist_ok=True)

# First install:
# initialize_database(path)
# Future refresh:
# update_database(path)
```

Unlike the default CLI initialization path, direct library calls expect the parent directory to exist. The lifecycle functions optionally accept an `on_progress` callback receiving `WorkflowProgress` events.

## Snapshot freshness

An installed database is a fixed snapshot assembled from independently retrieved Scryfall sources; it is not necessarily an atomic snapshot of every upstream endpoint at one instant.

By default, `CardDatabase.open()` logs a nonfatal warning if a required source export is more than seven days old or its age is unknown. You can suppress the warning without updating the database:

```python
with CardDatabase.open(warn_if_stale=False) as db:
    print(len(db))
```

CardDB remains usable offline with stale source data. To inspect the recorded source provenance, call `db.info()`; see [the API reference](api.md) for `DatabaseInfo` and source metadata.

## Troubleshooting

**No database yet:** If `CardDatabase.open()` raises `DatabaseNotInitializedError`, run `mtg-carddb init`. If using a custom path, pass the same path to the CLI and Python.

**Update refuses a target:** `update` requires an existing compatible database and will not initialize a missing one or overwrite an unrelated file. Use `init` for a new location.

**Lock detected:** Run `mtg-carddb locks` and verify whether another import/update is still running. The recorded PID, hostname, and timestamp do not conclusively prove whether a process is live. Do not delete a lock or a temporary workspace until the operation is known to have stopped.

**Download or build failure:** Check the CLI error. A new snapshot is constructed separately and activated only after validation; an update failure before activation should leave the existing database available. Repeated failures may reflect upstream changes, connection problems, disk space, permissions, or stale locks.

For reproducible bug reports, include the library/Python versions, operating system, relevant CLI output, and a minimal example. Report issues in the [GitHub issue tracker](https://github.com/Dooboofloo/mtg-carddb/issues).
