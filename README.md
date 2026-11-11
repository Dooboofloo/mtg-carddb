# MTG CardDB

**MTG CardDB** is a local-first Python library for querying *Magic: The Gathering* card data using immutable, composable sets of Oracle IDs.

CardDB builds a normalized SQLite snapshot from Scryfall data, then exposes a read-only Python API for searching cards without requiring SQL or a network connection.

**Status:** v0.1.0 — pre-release  
**Python:** 3.11+  
**Runtime dependencies:** Python standard library only  
**License:** MIT

## Features

- **Composable queries:** Combine card searches using Python set intersection (`&`), union (`|`), difference (`-`), and symmetric difference (`^`).
- **Oracle identity:** Search results identify cards by Scryfall Oracle UUID rather than individual printings.
- **Mechanical attributes:** Query color, color identity, card types, subtypes, keywords, mana value, and produced mana.
- **Format legality:** Filter by Scryfall legality status.
- **Oracle Tags:** Search direct card classifications or include descendant tags.
- **Printing history:** Query set membership, rarity, and supported games while preserving the relationship between those attributes.
- **Immutable records:** Resolve Oracle IDs to frozen card objects containing normalized face information.
- **Offline queries:** Open and search an installed database without contacting Scryfall.
- **Explicit updates:** Download, validate, build, and activate a new database only when requested.
- **Safe activation:** Validate replacement snapshots, protect existing databases, and detect many concurrent modification scenarios.
- **Source provenance:** Record information about the Scryfall sources used to build a snapshot.

## Installation

CardDB uses Python 3.11 or newer.

To install the current source from a local checkout:

```powershell
git clone https://github.com/Dooboofloo/mtg-carddb.git
cd mtg-carddb
python -m pip install .
```

For development, use an editable installation with the optional development tools:

```powershell
python -m pip install -e ".[dev]"
```

Once installed, the library can be imported as `mtg_carddb`, and the `mtg-carddb` command is available.

## Quick start

### 1. Initialize the database

```powershell
mtg-carddb init
```

Initialization downloads Scryfall's Default Cards and Oracle Tags exports, retrieves reference catalogs, constructs a SQLite snapshot, validates it, and installs it.

The CLI displays progress messages during the longer stages. Initialization requires internet access and may take several minutes.

By default, CardDB stores its database at:

| Platform | Default path |
| --- | --- |
| Windows | `%LOCALAPPDATA%\mtg-carddb\carddb.sqlite` |
| Linux/macOS | `${XDG_DATA_HOME}/mtg-carddb/carddb.sqlite`, or `~/.local/share/mtg-carddb/carddb.sqlite` when unset |

To use a custom location:

```powershell
mtg-carddb init --database path/to/carddb.sqlite
```

When specifying a custom path, create its parent directory first. The default initialization path is created automatically when needed.

### 2. Query cards in Python

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

Every search returns a `frozenset[str]` of Oracle UUIDs. Because these are ordinary immutable Python sets, queries can be combined directly.

`db.resolve()` loads card records for the requested IDs and returns them sorted by name and Oracle ID.

### 3. Update the database

```powershell
mtg-carddb update
```

Updating builds and validates a complete replacement database before activating it. The existing database is not modified during acquisition or construction.

The update command requires an existing compatible CardDB database. It will not initialize a missing database or intentionally replace an unrelated file.

An already-open `CardDatabase` handle continues using its existing snapshot. Reopen the database to see the newly activated version.

## Python API examples

### Compose filters

```python
from mtg_carddb import CardDatabase

with CardDatabase.open() as db:
    # Legendary creatures within a two-color identity
    pool = (
        db.within_identity("WU")
        & db.by_supertype("Legendary")
        & db.by_type("Creature")
    )

    # Exclude cards that are banned in Commander
    banned = db.by_legality("commander", "banned")
    pool = pool - banned

    for card in db.resolve(pool)[:20]:
        print(card.name)
```

### Color and color identity

CardDB distinguishes a card's colors from its Commander-style color identity.

```python
with CardDatabase.open() as db:
    # Cards whose color identity contains both B and G
    black_green_identity = db.containing_identity("BG")

    # Cards whose color identity uses only B and/or G
    golgari_eligible = db.within_identity("BG")

    # Cards whose color identity is exactly B and G
    exactly_golgari = db.exact_identity("BG")

    # Cards whose aggregate printed colors contain green
    green_cards = db.by_color("G")
```

Color identity queries operate on Oracle-level identity. `by_color()` operates on card colors aggregated from the normalized card data.

Colorless is represented by the absence of WUBRG colors, not a sixth color identity.

### Find a specific card

```python
with CardDatabase.open() as db:
    matches = db.by_name("Sol Ring")

    for oracle_id in matches:
        card = db[oracle_id]
        print(card.name, card.oracle_id)
```

`by_name()` performs a case-insensitive name lookup, not a substring or fuzzy search.

Use `db.get(oracle_id)` to retrieve a card or `None`, or `db[oracle_id]` to raise `KeyError` when it is absent.

### Oracle Tags

```python
with CardDatabase.open() as db:
    # Discover available tag slugs
    print(sorted(db.tag_slugs)[:20])

    # Once a desired slug has been selected:
    if db.tag_slugs:
        slug = sorted(db.tag_slugs)[0]

        # Include cards tagged with descendant classifications
        expanded = db.by_tag(slug)

        # Only direct assignments to this tag
        direct = db.by_tag(
            slug,
            include_descendants=False,
        )

        print(len(expanded), len(direct))
```

Tag names, aliases, and hierarchy come from the installed Oracle Tags snapshot. An unknown tag raises `UnknownTagError`.

### Printing history

```python
with CardDatabase.open() as db:
    # Oracle identities observed at rare rarity in a given set
    cards = db.by_set_rarity(
        "MKM",
        "rare",
        game="paper",
    )

    print(len(cards))
```

`by_set_rarity()` requires set, rarity, and game to belong to a matching historical observation. It does not independently intersect a card's entire set history with its entire rarity history.

CardDB stores these observations at the Oracle-identity level; it is not a complete printing or collectible-card inventory database.

### Metadata and provenance

```python
from mtg_carddb import (
    CardDatabase,
    database_freshness,
)

with CardDatabase.open(warn_if_stale=False) as db:
    metadata = db.info()
    freshness = database_freshness(metadata)

    print("Cards:", len(db))
    print("Schema:", metadata.schema_version)
    print("Freshness:", freshness.status)

    for source in metadata.sources:
        print(source.source_type, source.source_url)
```

An installed database is a fixed snapshot of independently retrieved Scryfall sources, not a promise that all upstream datasets existed at one simultaneous point in time.

## CLI reference

| Command | Purpose |
| --- | --- |
| `mtg-carddb init` | Create a new database |
| `mtg-carddb update` | Rebuild and replace an existing database |
| `mtg-carddb info` | Display database metadata and freshness |
| `mtg-carddb locks` | Inspect workflow locks and interrupted-build artifacts |

All commands accept `--database PATH` (or `-d PATH`) to select a database.

`init` and `update` also accept `--no-progress` to suppress stage messages and periodic heartbeats.

`info` accepts:

- `--no-stale-warning` — suppress warning messages while still calculating and displaying freshness.
- `--stale-after-days DAYS` — change the freshness threshold from its default of seven days.

`locks` is read-only and never removes lock files or build artifacts.

The CLI can also be invoked with:

```powershell
python -m mtg_carddb --help
```

## Library lifecycle

Applications may initialize and update databases through Python:

```python
from mtg_carddb import (
    default_database_path,
    initialize_database,
    update_database,
)

path = default_database_path()

# The parent directory must exist for a direct library call.
path.parent.mkdir(parents=True, exist_ok=True)

# First installation:
# initialize_database(path)

# Later updates:
# update_database(path)
```

Initialization and updates are explicit operations involving Scryfall network access. Opening and querying an existing database do not perform either operation.

The lifecycle functions also support an optional `on_progress` callback receiving `WorkflowProgress` events.

## Freshness and offline use

CardDB uses the source timestamps recorded for the Default Cards and Oracle Tags exports when assessing freshness.

By default, opening a database logs a nonfatal warning when either required source is older than seven days, or when freshness cannot be established.

```python
with CardDatabase.open(
    warn_if_stale=False,
) as db:
    print(len(db))
```

Suppressing the warning does not update the data. It only disables warning emission for that open operation.

CardDB remains usable offline even when its sources are stale.

## Troubleshooting

### The database has not been initialized

If `CardDatabase.open()` raises `DatabaseNotInitializedError`, run:

```powershell
mtg-carddb init
```

Ensure your library and CLI use the same database path if you have selected a custom destination.

### Update refuses the target

`update` requires an existing, compatible CardDB database. It will reject missing, invalid, or unsupported database files.

Use `init` to create a database at a new path. Do not replace an unrelated file to satisfy the update command.

### An operation reports an existing lock

Inspect the lock state:

```powershell
mtg-carddb locks
```

The command reports workflow and activation locks, and any matching temporary build artifacts.

Lock metadata can identify the recorded PID, hostname, and creation time. It cannot conclusively establish whether the process is still running.

Do not delete a lock or temporary workspace until you have independently confirmed that no operation is using it.

### A download or build fails

CardDB builds in an isolated workspace and activates a replacement only after validating it. If an update fails before activation, the previously installed database should remain available.

The command's error output should be inspected before retrying. Persistent failures may indicate unavailable Scryfall endpoints, unsupported upstream data, disk or permission problems, or a leftover lock.

## Development

Install development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Run the offline test suite:

```powershell
python -m pytest -q --disable-socket
```

Build and validate distributions:

```powershell
python -m build
python -m twine check --strict dist/*
```

The Twine command above uses shell glob expansion. On PowerShell, pass the distribution filenames explicitly if necessary.

CI runs automated tests on Windows, Linux, and macOS with Python 3.11, 3.12, and 3.13. A separate job builds the wheel and source distribution and tests clean installations.

## Design documents

- [Python API contract](docs/api.md)
- [SQLite schema and import specification](docs/schema.md)

These documents describe the intended data model and detailed query semantics. Implementation and documentation changes are reviewed together as the project approaches a stable release.

For development and releases, see [Contributing](CONTRIBUTING.md), the [Changelog](CHANGELOG.md), and the [Release checklist](docs/releasing.md).

## Scope and limitations

CardDB provides a local mechanical card database, not a full rules engine or deck-building application.

It does not automatically implement house bans, deck construction rules, historical format rotation, complete printing inventories, or user-defined persistent classifications.

The underlying Scryfall sources may change independently. CardDB does not attempt to reconstruct historical Oracle text revisions or historical ban-list states from the current exports.

## License and attribution

The original CardDB source code is licensed under the MIT License. See [LICENSE](LICENSE).

Card data is obtained from [Scryfall](https://scryfall.com/). Scryfall data and other third-party materials remain subject to their respective terms.

*Magic: The Gathering* and associated marks are the property of Wizards of the Coast.

This project is not affiliated with or endorsed by Wizards of the Coast or Scryfall.
