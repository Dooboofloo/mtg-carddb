# MTG CardDB — Database Schema

**Status:** Design specification (proposed schema v1; not yet implemented)  
**Database file:** `carddb.sqlite`  
**Canonical data source:** Scryfall  
**Primary consumer:** Python applications using composable set operations

## 1. Purpose and boundaries

MTG CardDB is a local-first Python library for accessing *Magic: The Gathering* cards through intuitive, composable set operations. SQLite is an **intermediary layer**, not the primary query engine. The main querying interface is Python; applications should not need to write SQL.

**Principles**

- The unit of query identity is a **Scryfall Oracle ID**. Queries return immutable sets of Oracle IDs (`frozenset[str]`).
- The database describes the card universe without specific assumptions, house bans, favored formats, or other application policies.
- **Scryfall is the only canonical data source.**
- No individual printing records are retained. `default_cards` printings are a **transient import representation** used both to select current Oracle-level characteristics and to aggregate historical set/rarity/platform observations. CardDB never persists printing IDs or printing objects.
- Cards, faces, keywords, colors, tags, legalities, and set/rarity occurrences are **stored facts**. Python indexes and descendant-expanded tag memberships are **derived views**, not duplicated SQL facts.
- The database represents the current Oracle characteristics and current Scryfall legality statuses **at build time**. It is not an archive of historical Oracle text or historical ban lists.
- A completed database is treated as **read-only** by consuming applications. Refreshing builds a new database and replaces the existing file only after validation.

Out of scope: printing IDs, collector numbers, art, finishes, prices, languages, per-printing chronology, collection ownership, deck-construction policies, historic Oracle revisions, historic legality timelines, and arbitrary user-authored database attributes.

## 2. Scryfall sources and import boundaries

The **sole card-record bulk source** is Scryfall's `default_cards` export; `oracle_cards` is **not** downloaded or required. `default_cards` contains Scryfall Card objects for individual printings (English preferred, or a printed language where English is unavailable) and includes current gameplay fields. Reading all of these records is already necessary to derive set/rarity history, so a second card bulk export would be redundant.

| Scryfall source | Used for | Stored output |
| --- | --- | --- |
| `default_cards` bulk export (**required**) | Normalize one current mechanical representative **per Oracle ID**; aggregate all observed card–set–rarity–game relationships | `cards`, `faces`, `face_types`, `card_keywords`, `card_legalities`, `card_set_rarities`; **never printing rows** |
| `oracle_tags` bulk export (**required**) | Oracle-tag definitions, direct taggings, aliases, directed hierarchy | `tags`, `tag_aliases`, `tag_relationships`, `card_tags` |
| Scryfall Sets API (`GET /sets`, **required**) | Set UUID, canonical code, name, type, release date, and **set-level** `digital` | `sets` |
| Scryfall type catalogs (**import-time reference**) | Validate known supertypes and subtype vocabulary, including multiword types; detect vocabulary changes | No dedicated catalog table; provenance recorded if fetched |

**Bulk transport.** Discover download URLs from Scryfall's bulk-data API, rather than hardcoding CDN filenames. Current exports expose `jsonl_download_uri` pointing to gzip-compressed JSON Lines. Stream and decompress line by line; validate the final gzip stream and record count. For possible future format changes, reject unsupported formats explicitly. Do not load the entire export in memory or issue per-card API requests for normal imports.

**Source consistency.** `default_cards`, `oracle_tags`, and the Sets API/catalogs have independent update times. The resulting DB is a documented build from specific Scryfall source versions, not a claim of a globally simultaneous Scryfall snapshot. Record each actual source in `source_metadata`.

### 2.1 Deterministic Oracle representative selection

The importer must **not** use the first printing encountered: row order is not a semantic selection policy. Instead:

1. Stream `default_cards` and extract distinct `(oracle_id, set_id, rarity, game)` observations from **every** printing, including reversible face Oracle IDs. Deduplicate those observations in the staging database.
2. For every ordinary Scryfall Card with a top-level Oracle ID, form a **current-mechanics projection** containing only persisted gameplay fields (including normalized face properties, card keywords, and legalities). Ignore printing-specific fields such as printing `id`, `set`, `rarity`, collector number, art, and price.
3. Normalize known layout-dependent field locations and compare projected values for the same Oracle ID. Compare actual values when present; distinguish genuinely absent data from conflicting provided data. Resolve layout-dependent absences only using a complete, supported source record, never arbitrary merging of unrelated cards.
4. Select a reproducible representative among compatible candidates: prefer **non-reversible** objects, then `lang == 'en'`, then newest `released_at`, then lexical Scryfall printing `id` as a final tie-breaker. Reversible objects are treated as **separate face-level candidates** for each face's Oracle ID, not as combined card records. This policy does not claim to replicate Scryfall's subjective `oracle_cards` representative choice.
5. A non-reversible candidate with a complete and valid gameplay projection wins over reversible face candidates. If the only source is a reversible face, accept it only when **all required Oracle-level fields can be resolved unambiguously** from Scryfall-provided information. Otherwise fail the strict build with the affected Oracle ID and missing fields; do not borrow combined root color identity, keywords, or reserved status from a different identity.
6. Any **real disagreement in non-missing canonical mechanical values** across candidates is an import error requiring an explicit normalization rule or upstream investigation; it is not resolved by choosing the newest printing. Reconcile and validate cross-record completeness before finalizing.

This handles known reversible anomalies such as an overall printing named `Blood Crypt // Blood Crypt`, where the underlying Oracle identity's name remains `Blood Crypt`. It also prevents accidentally treating two unrelated reversible sides as a single multifaced gameplay card.

**Important limitation:** this is a fully specified *decision policy*, not a claim that the complete November 2026 Scryfall bulk files have been audited. The implementation must execute the fixture and full-export validation defined in §8. A new upstream shape that cannot be represented causes a clear build failure, leaving the existing `carddb.sqlite` intact.

## 3. Identity and normalization

### 3.1 Cards

One `cards` row represents one Oracle identity. Its primary key is Scryfall's Oracle UUID, exposed to Python callers as a string. An Oracle identity is **not** an individual printing and not necessarily a physical piece of cardboard.

### 3.2 Faces

Every `cards` row has **at least one** `faces` row. Single-faced source cards become one normalized face (`face_index = 0`); multifaced source cards contribute one face per Scryfall face entry in source order. Whole-card queries can union face memberships without duplicating Oracle IDs.

A card with a creature face and a land face belongs to both whole-card type sets. That does **not** imply any individual face has both types. Face-specific query semantics can be added later without changing the persisted facts.

### 3.3 Reversible cards and other unusual layouts

Scryfall's `reversible_card` layout differs from transform or modal DFCs: the root object has **no top-level `oracle_id`**, and its component faces have **independent** Oracle IDs and `cmc`/`type_line`/`layout` properties. A reversible printing contributes **one candidate Oracle identity per face**, each with **one normalized face**, and its `(set_id, rarity, games)` observation applies to both constituent Oracle IDs. Never store the original root name (`A // B`) as the canonical name of either unrelated card.

Reversible face data alone does not establish every card-level property (notably `color_identity`, `keywords`, and `reserved`); source root properties describing the overall reversible object are not automatically assignable to both face identities. Prefer ordinary, independently identified printings of those cards. A reversible-only identity is accepted only when complete source-backed Oracle properties can be established; otherwise **strict validation fails** with actionable diagnostics instead of fabricating data, silently omitting the card, or contaminating it with the other face's properties.

For ordinary split, Adventure, transform, and modal DFC layouts, multiple face rows belong to **one** Oracle identity. Meld components and results remain distinct Oracle identities; modeling meld links is outside v1.

## 4. Color and scalar conventions

- Public APIs accept familiar Magic notation (`"WUG"`, `"B"`, etc.); **numeric masks are internal only**.
- Card colors, color identity, and face color indicators use a **five-bit internal mask**: `W=1, U=2, B=4, R=8, G=16`. Valid range: `0..31`.
- Produced mana uses a **six-bit internal mask**, adding colorless mana `C=32`. Valid range: `0..63`.
- `0` means a *known empty color set*; `NULL` means the source did not provide an applicable value. These are never interchangeable.
- `mana_value` is `REAL` to preserve Scryfall's `cmc`, including unusual fractional values.
- Mana cost and Oracle text are preserved as source strings. Missing mana cost (`''`) is not the same as `{0}`; `NULL` signifies absent or unavailable source data.
- Power, toughness, loyalty, defense, and Vanguard modifiers are strings because some values are symbolic (`*`, `X`, etc.). No automatic numeric interpretation is promised in v1.
- Dates are ISO `YYYY-MM-DD` and timestamps are UTC ISO 8601 text. Booleans are SQLite integers restricted to `0` or `1`.

## 5. Proposed SQLite schema (v1)

The following DDL defines the **logical persisted model**. The canonical card-field selection algorithm is specified in §2.1 and does not create additional printing tables. Secondary performance indexes are deliberately minimal until index-hydration benchmarks identify the actual bottlenecks. SQLite foreign-key enforcement must be enabled on every writing connection (`PRAGMA foreign_keys = ON`). The importer is responsible for higher-order invariants that simple foreign keys cannot express.

```sql
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

-- One row per Oracle identity.
CREATE TABLE cards (
    oracle_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    layout          TEXT NOT NULL,
    type_line       TEXT NOT NULL,
    mana_value      REAL NOT NULL,
    color_identity  INTEGER NOT NULL CHECK (color_identity BETWEEN 0 AND 31),
    colors          INTEGER CHECK (colors BETWEEN 0 AND 31),
    produced_mana   INTEGER CHECK (produced_mana BETWEEN 0 AND 63),
    reserved        INTEGER NOT NULL CHECK (reserved IN (0, 1))
);

-- Normalized face rows, including one face for ordinarily single-faced cards.
CREATE TABLE faces (
    oracle_id       TEXT NOT NULL,
    face_index      INTEGER NOT NULL CHECK (face_index >= 0),
    name            TEXT NOT NULL,
    type_line       TEXT NOT NULL,
    mana_cost       TEXT,
    oracle_text     TEXT,
    colors          INTEGER CHECK (colors BETWEEN 0 AND 31),
    color_indicator INTEGER CHECK (color_indicator BETWEEN 0 AND 31),
    power           TEXT,
    toughness       TEXT,
    loyalty         TEXT,
    defense         TEXT,
    hand_modifier   TEXT,
    life_modifier   TEXT,
    PRIMARY KEY (oracle_id, face_index),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- Parsed from face type lines; the original text is preserved above.
CREATE TABLE face_types (
    oracle_id   TEXT NOT NULL,
    face_index  INTEGER NOT NULL,
    category    TEXT NOT NULL
                CHECK (category IN ('supertype', 'type', 'subtype')),
    value       TEXT NOT NULL,
    PRIMARY KEY (oracle_id, face_index, category, value),
    FOREIGN KEY (oracle_id, face_index)
        REFERENCES faces(oracle_id, face_index) ON DELETE CASCADE
);

-- Scryfall's Oracle-level keyword classifications.
CREATE TABLE card_keywords (
    oracle_id  TEXT NOT NULL,
    keyword    TEXT NOT NULL,
    PRIMARY KEY (oracle_id, keyword),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- One current Scryfall status per card and format.
CREATE TABLE card_legalities (
    oracle_id  TEXT NOT NULL,
    format     TEXT NOT NULL,
    status     TEXT NOT NULL
               CHECK (status IN ('legal', 'restricted', 'banned', 'not_legal')),
    PRIMARY KEY (oracle_id, format),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- Oracle tag identities, independent of tag/card assignments.
CREATE TABLE tags (
    tag_id       TEXT PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL,
    description  TEXT
);

CREATE TABLE tag_aliases (
    tag_id  TEXT NOT NULL,
    alias   TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (tag_id, alias),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
);

-- Directed parent -> child edges. Multiple parents are allowed.
CREATE TABLE tag_relationships (
    parent_id  TEXT NOT NULL,
    child_id   TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id),
    CHECK (parent_id <> child_id),
    FOREIGN KEY (parent_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (child_id) REFERENCES tags(tag_id) ON DELETE CASCADE
);

-- Direct Scryfall taggings only; no duplicated ancestor assignments.
CREATE TABLE card_tags (
    tag_id      TEXT NOT NULL,
    oracle_id   TEXT NOT NULL,
    weight      TEXT NOT NULL,
    annotation  TEXT,
    PRIMARY KEY (tag_id, oracle_id),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE
);

-- Set identity and limited descriptive metadata (no printings).
CREATE TABLE sets (
    set_id       TEXT PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    set_type     TEXT NOT NULL,
    released_at  TEXT,
    digital      INTEGER NOT NULL CHECK (digital IN (0, 1))
);

-- Distinct observed historical relationships, not individual editions.
CREATE TABLE card_set_rarities (
    oracle_id  TEXT NOT NULL,
    set_id     TEXT NOT NULL,
    rarity     TEXT NOT NULL,
    game       TEXT NOT NULL,
    PRIMARY KEY (oracle_id, set_id, rarity, game),
    FOREIGN KEY (oracle_id) REFERENCES cards(oracle_id) ON DELETE CASCADE,
    FOREIGN KEY (set_id) REFERENCES sets(set_id)
);

-- One database-build record, not a history of old database files.
CREATE TABLE database_metadata (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    created_at       TEXT NOT NULL,
    builder_version  TEXT NOT NULL
);

-- Provenance for each source actually used in this build.
CREATE TABLE source_metadata (
    source_type       TEXT PRIMARY KEY,
    source_updated_at TEXT,
    downloaded_at     TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    sha256            TEXT NOT NULL CHECK (length(sha256) = 64),
    record_count      INTEGER NOT NULL CHECK (record_count >= 0)
);
```

### 5.1 Relational integrity beyond the DDL

The importer must enforce (strict builds fail if required guarantees cannot be met):

1. Every card has at least one face, with unique contiguous indices starting at 0.
2. Every source printing is either accounted for by a normalized Oracle identity (ordinary or reversible face) or produces an explicit actionable error. No Oracle ID has conflicting supplied canonical mechanical characteristics; fields absent only because of a reversible layout are not interpreted as disagreeing values.
3. `face_types` accurately describes parsed face `type_line` values. Parse the `—` type/subtype separator and match subtype names by **catalog-aware longest match** (for example, **Time Lord**, **Assembly-Worker**), not by whitespace splitting. Supertypes are identified via the Scryfall supertype catalog; other type tokens use a validated Magic card-type vocabulary. Unknown or ambiguous tokens are a validation error, not silently misclassified.
4. Every legality status belongs to the supported status vocabulary; format identifiers come from Scryfall data, **not** a hardcoded list of favorite formats.
5. All tag slugs are unique and parent/child edges form an **acyclic** directed graph. Read `parent_ids` and `child_ids` (which may be missing/null/empty) as the two views of the same directed relationships; when both describe a relationship, verify their consistency. Alias collisions are **permitted in SQL**, recorded as ambiguous in Python, and never resolved by guessing. Canonical exact slugs take precedence over aliases.
6. `oracle_tags` input is a collection of tag objects (`id`, `slug`, `label`, optional `description`, `aliases`, `parent_ids`, `child_ids`, and nested `taggings`). Oracle taggings reference `oracle_id` and contain `weight` with optional `annotation`. Preserve source-supplied weights as text; no hardcoded ordering or SQL vocabulary constraint is introduced. Validate object type and field types at import time, and reject unexpected required-field omissions with clear diagnostics.
7. Tagging Oracle IDs and all set/rarity Oracle IDs resolve to imported cards. Dangling references fail a strict build (after a diagnostic report listing affected IDs); no silent deletion or invented replacement identities.
8. `default_cards` supplies one language-preferred object per printing and is the canonical source for the intended `(oracle_id, set_id, rarity, game)` relation. `all_cards` is **not used**: its additional language records do not represent an additional game-mechanical set/rarity category. Repeated printings, multi-game memberships, and reversible sides are deduplicated into distinct relationship rows.
9. The new database passes SQLite integrity and foreign-key checks, plus semantic validation, **before** activation.

### 5.2 Performance indexes

SQLite's declared primary keys and unique constraints already create internal indexes. Additional covering/reverse indexes should be selected against observed access paths: index hydration, lookups by Oracle ID, and bulk resolution. Potential examples are an index starting with `face_types(category, value)`, reverse lookups on `card_tags(oracle_id)`, and `card_set_rarities(set_id, rarity, game)`. Do **not** introduce dozens of permanent SQL indexes merely to imitate the Python inverted indexes.

## 6. Python query-index mapping

SQL persists normalized facts; Python builds an inverted index mapping attribute values or compound attribute keys to immutable Oracle-ID sets. The public API must never require callers to know internal bitmask integers, tag UUIDs, or SQL table names.

| Python operation (proposed) | SQL source | Result semantics |
| --- | --- | --- |
| `by_type("Creature")` | `face_types`, category `type` | Oracle IDs with **any** matching face |
| `by_supertype("Legendary")` / `by_subtype("Elf")` | `face_types` | Any matching face |
| `by_keyword("Flying")` | `card_keywords` | Scryfall card-level keyword membership |
| `exact_identity("WU")` | `cards.color_identity` | Exact known identity |
| `within_identity("WUG")` | `cards.color_identity` | Identity is a subset of WUG; colorless included |
| `by_legality("vintage", "restricted")` | `card_legalities` | Exact Scryfall format/status pair |
| `by_tag("ramp")` | `tags`, `tag_relationships`, `card_tags` | Direct tags **plus descendants** by default |
| `by_tag("ramp", include_descendants=False)` | Same | Directly tagged cards only |
| `by_set("m21")` | `sets`, `card_set_rarities` | Cards observed anywhere in that set |
| `by_rarity("common", game="paper")` | `card_set_rarities` | Ever observed at that rarity/platform |
| `by_set_rarity("m21", "common", game="paper")` | `card_set_rarities` | Rarity **in that set and game** |
| `resolve(oracle_ids)` | `cards`, `faces` and related tables | Full card records, retrieved by ID |

These names illustrate intended semantics, not a frozen Python API. The engine may eagerly or lazily hydrate index categories; benchmark the alternatives. A repeated lookup of an already-loaded category should not require SQL. Direct and expanded tag memberships should be cached separately as needed, with duplicate paths deduplicated by set union.

### 6.1 Relational pairs must remain coupled

A card's presence in a set and its having **ever** been printed at a rarity are two independent facts. Their intersection does **not** prove the card had that rarity *in that set*:

```python
# Not equivalent to "common in m21":
db.by_set("m21") & db.by_rarity("common")

# Correct relationship-preserving query:
db.by_set_rarity("m21", "common")
```

The same rule applies to game platform. Compound relationships must be indexed using their **full relevant key**, rather than reconstructed by intersecting unrelated projections.

### 6.2 Tag expansion

Let `D(t)` be the direct Oracle-ID membership of tag `t` and `Desc(t)` the set of graph descendants. The default tag query is:

`T(t) = D(t) ∪ ⋃{ D(d) : d ∈ Desc(t) }`

Store only direct taggings and direct parent/child edges in SQL. Graph traversal and expanded membership are derived and cached in Python, bound to the currently opened database. Multiple parents are allowed; cycles are invalid. A broad tag does not receive fabricated direct taggings or inherited tagging weights.

### 6.3 Errors and absence

A valid category/value query with no matching cards returns an empty `frozenset`. Unknown tag names or format identifiers should produce informative errors rather than silently imply that the tag/format exists and has no members. Missing Scryfall facts are never silently converted into `not_legal`, colorless, or zero-value facts.

## 7. File lifecycle and refresh guarantees

The public file has **one stable name**: `carddb.sqlite`. No retained snapshot directories or history of previous database versions are required. A completed database is read-only in normal use. Package version, SQLite schema version, and upstream source timestamps are independent.

**Explicit initialization/update procedure:**

1. Discover and download `default_cards` and `oracle_tags` bulk files, fetch the complete Scryfall `/sets` catalog (following pagination if provided), and fetch relevant type catalogs where used. Save the exact responses temporarily; record source URLs, available timestamps, record counts, and SHA-256 hashes.
2. Build a **new** SQLite file in a temporary location on the **same filesystem/directory** as `carddb.sqlite`.
3. Normalize one canonical Oracle identity per card using §2.1; aggregate historical set/rarity/game observations from the **same** `default_cards` export; join against the Sets API records for set metadata; import tags and graph relationships. Record `database_metadata` and one `source_metadata` row for **every** source actually fetched (including Sets API and catalogs).
4. Run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`, plus logical/integrity checks listed above; verify all imported Oracle identities have complete required fields, no card or tag reference was silently dropped, and the final number of set/rarity tuples equals the count of distinct source tuples.
5. Commit and close the temporary database; ensure it is a self-contained SQLite file (including completing/checkpointing any WAL state and closing handles).
6. Atomically replace `carddb.sqlite` **where the operating system supports doing so safely**. On Windows, an open database can prevent replacement; report a clear error and leave the original file intact. Existing CardDB objects must be reopened to see updated data.
7. Remove temporary files as appropriate. If any earlier step fails, leave the valid existing database untouched.

No automatic network access occurs merely because an application imports the package or opens a database. The updater should also prevent concurrent rebuilds targeting the same path.

### Database metadata and compatibility

- SQLite `PRAGMA user_version` identifies the schema (initial proposal: `1`).
- `database_metadata` records one build timestamp and the package version that built it.
- `source_metadata` records each Scryfall export/catalog's provenance; `source_updated_at` is nullable for sources without an upstream update timestamp.
- A database is a **point-in-time build** of several Scryfall sources; their timestamps need not be identical.
- For an unsupported schema version, the initial policy is **rebuild**, not in-place migration. The runtime must refuse to open an incompatible file with a helpful diagnostic.

## 8. Validation and tests

The implementation should include small synthetic fixtures and **captured real Scryfall fixtures** for normal cards, Adventures, split cards, transforming/modal double-faced cards, meld, reversible cards (including repeated names such as `Blood Crypt // Blood Crypt`), empty-versus-missing color data, fractional mana values, multiword subtypes (including `Time Lord`), Vanguard characteristics, tag graph branches, alias collisions, tagging-weight serialization, and multiple rarities in one set. Tests must cover a reversible-only identity with incomplete required fields (strict failure), non-reversible preference, same-ID field conflicts, and stable tie-breaking independent of source record order.

**Required equivalence property:** for any supported query, the indexed Python result equals the set of Oracle IDs obtained by evaluating the corresponding predicate over normalized source records. Test union, intersection, difference, descendant expansion, and deduplication of multifaced cards. Test that failed imports and failed file replacement leave the original usable database unchanged.

Benchmark initial index-building time, repeated-query latency, memory use, SQLite file size, and card resolution. Compare eager, category-lazy, and value-lazy index hydration before committing to one caching strategy. A **full current-export integration test** is a release gate: fetch actual Scryfall bulk files, record their source timestamps, run all importer invariants, and compare a small sample of normalized Oracle identities and set/rarity relationships against Scryfall Card/Set API responses. This validation is mandatory before describing a release as complete.

## 9. Resolution of design questions

These are now specified as **importer policies and acceptance tests**, rather than unresolved alternatives. Assertions about the *actual contents of a future live export* are checked during import; they are not assumed merely because an API type definition permits a value.

| Previous question | Resolution |
| --- | --- |
| 1. Reversible identities | Split a `reversible_card` object into separate candidate identities keyed by each face's `oracle_id`; attach each printing's set/rarity/game observations to both. Prefer complete non-reversible candidates; do **not** apply root-level color identity, keywords, or reserved status to both sides. Fail strict import if a reversible-only identity lacks required unambiguous facts. See §§2.1, 3.3. |
| 2. Set metadata | **Always fetch Scryfall's `/sets` catalog**. `default_cards` provides printing set identifiers, but set-level `digital` and `set_type` belong to the Scryfall Set object. Verify the full set UUID/code join and record source provenance. See §2. |
| 3. Historical completeness | Use **`default_cards` alone** for all observed Oracle/set/rarity/game memberships. It includes the relevant printings without duplicating every language variation; `all_cards` adds localization outside the project scope. Validate distinct tuple counts and the reversible-face expansion. |
| 4. Tag source encoding | Use the documented Tag/Tagging structure (`id`, `slug`, hierarchy/aliases, nested Oracle-ID taggings with weight/optional annotation). Accept null or absent optional graph/alias arrays. Preserve raw textual weight labels, do not infer ordering, and report unexpected live-export shapes. See §5 and §8. |
| 5. Type parsing | Keep original type lines and parse face-level fields with a tested **catalog-aware parser**, including multiword names and unusual layouts. Reject unknown/ambiguous classifications; never split all subtype names on spaces. See §5.1. |
| 6. Public API semantics | Keep the *schema* independent of final API method naming. Public identities use WUBRG strings and `frozenset[str]`; whole-card type membership means **any face**. `cards.colors` vs `faces.colors` remain separate source facts; precise `by_color()` semantics are deferred to API design, not implemented by guessing. See §§4, 6. |

### 9.1 Verification evidence and limitations

Relevant upstream definitions and implementations consulted during design:

- [Scryfall bulk-data documentation](https://scryfall.com/docs/api/bulk-data) and the [Scryfall Card field definitions](https://github.com/scryfall/api-types/blob/main/src/objects/Card/CardFields.ts), [Card layout types](https://github.com/scryfall/api-types/blob/main/src/objects/Card/Card.ts), and [Card Face types](https://github.com/scryfall/api-types/blob/main/src/objects/Card/CardFace.ts) establish the Oracle-ID/face distinction and record field locations.
- [Scryfall Set types](https://github.com/scryfall/api-types/blob/main/src/objects/Set/Set.ts) establish that `digital`, `set_type`, `released_at`, `code`, and `id` belong to set metadata.
- [Scryfall Tag documentation](https://scryfall.com/docs/api/tags), corroborated by [Scruffy's Tag/Tagging model](https://scruffy.hexdocs.pm/scruffy/tag.html) and [Scrython's Oracle-tag support](https://github.com/NandaScott/Scrython), describes nested taggings, hierarchy, aliases, and tagging strengths.
- [Scryfall's catalog endpoints](https://scryfall.com/docs/api/catalogs) expose type vocabularies for validation, including creature, land, artifact, and other subtypes.
- A [third-party Default Cards import analysis](https://tangled.org/eth0net.io/manaweb/blob/main/docs/scryfall.md) reports that reversible printings can disagree with normal printings on field *presence* and printable name (`Blood Crypt // Blood Crypt`). This is corroborating evidence, **not** a substitute for a new complete-export audit.

**Limit of this design review:** upstream APIs and independent importer analyses were researched, but the current full Scryfall bulk payloads were **not downloaded and exhaustively inspected** as part of writing this specification. Exact live tag field encodings, newly introduced layouts, rare reversible-only identities, and complete import counts remain subject to the mandatory full-export acceptance test in §8. This is a *verification task*, not an undecided schema design question. In all cases, new unsupported input causes diagnostic failure while preserving the last valid `carddb.sqlite`.
