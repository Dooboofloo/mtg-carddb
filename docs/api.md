# MTG CardDB — Python API Contract

**Status:** v1 design contract (no implementation yet)  
**Package/import:** `mtg_carddb`  
**Primary interface:** `CardDatabase`  
**Companion specification:** [`schema.md`](schema.md) (agreed SQLite schema v1, including the approved nullable `cards.game_changer` column).

## 1. Purpose and design boundaries

MTG CardDB is a local-first Python library for querying Scryfall-derived *Magic: The Gathering* mechanical card data. SQLite is a read-only data source for the running application; **the product is a Python interface whose query results are ordinary, composable sets**.

The public contract is designed for applications that need to select, compare, combine, and resolve cards without knowing SQL, the import pipeline, internal bitmasks, or Scryfall's individual printing structure.

### Stable principles

1. **Oracle identity is the query unit.** Every card-search method returns `frozenset[str]` containing Scryfall Oracle UUID strings; no search returns individual printings or faces.
2. **Public results are deeply immutable.** Record objects are frozen value objects with immutable nested collections. Returned sets are native Python `frozenset` instances, not a proprietary query class.
3. **Queries are read-only and composable.** Use Python `&`, `|`, `-`, and `^` directly. Applications may construct mutable copies with `set(...)` if desired.
4. **No application policy is built in.** House bans, preferred formats, deck construction, Commander eligibility, and user-defined classifications belong to consumers. User-defined persistent indexes are not part of v1.
5. **The API describes a fixed dataset per opened handle.** Importing or opening the library never downloads data, and an existing handle does not silently switch versions during a database update.
6. **Scryfall is canonical.** Returned values are normalized from the persisted facts specified in `schema.md`. The API does not infer historical Oracle revisions, historical legality, or contextual Magic rules beyond explicitly documented predicates.
7. **Internal representation is not a public requirement.** WUBRG bitmasks, SQLite table layout, index keys, cache state, and tag UUIDs need not be known by consumers.

## 2. Public object model

All record classes are **deeply immutable**, suitable for holding after the database has closed. The intended implementation technique is frozen, slotted dataclasses, but the semantic contract is their fields, values, and immutability—not the decorator used.

### 2.1 `Card`

A `Card` represents **one Oracle identity**, independent of printings.

| Field | Public type | Meaning |
| --- | --- | --- |
| `oracle_id` | `str` | Scryfall Oracle UUID string |
| `name` | `str` | Canonical whole-card name |
| `layout` | `str` | Layout of the selected canonical mechanical representative, **not** a list of layouts found among its printings |
| `type_line` | `str` | Whole-card type line, as normalized from Scryfall |
| `mana_value` | `float` | Scryfall card-level `cmc` |
| `colors` | `frozenset[str] \| None` | Whole-card WUBRG colors if defined; `None` is not colorless |
| `color_identity` | `frozenset[str]` | Whole-card WUBRG color identity |
| `produced_mana` | `frozenset[str] \| None` | Scryfall-produced mana symbols from `WUBRGC`, if defined |
| `reserved` | `bool` | Scryfall Reserved List flag |
| `game_changer` | `bool \| None` | Scryfall's current Game Changer classification, when explicitly available; `None` means unknown or absent, not `False`; persisted in nullable `cards.game_changer`. |
| `keywords` | `frozenset[str]` | Scryfall card-level keyword classifications |
| `faces` | `tuple[CardFace, ...]` | At least one face; original normalized face order |
| `supertypes` | `frozenset[str]` | Convenience **union across faces** |
| `types` | `frozenset[str]` | Convenience **union across faces** |
| `subtypes` | `frozenset[str]` | Convenience **union across faces** |

The final three properties are derived from the face records; they do not imply that all properties occur together on one face. `Card.colors` preserves the source-level whole-card value; the `by_color()` query may additionally derive an aggregate from face colors when that value is absent, as specified in §4.3. `Card` is self-contained: accessing any field does not issue SQL, and it has no `.save()`, `.update()`, or implicit database reference. A representative's layout can be printing-dependent (for example, a normal card reprinted reversibly); `Card.layout` names the **selected normalized representation**, not every historical physical layout. See §9.

### 2.2 `CardFace`

| Field | Public type | Meaning |
| --- | --- | --- |
| `face_index` | `int` | Zero-based position within `Card.faces` |
| `name` | `str` | Face name |
| `type_line` | `str` | Face-specific type line |
| `mana_cost` | `str \| None` | Original Scryfall mana-cost notation |
| `oracle_text` | `str \| None` | Current face rules text |
| `colors` | `frozenset[str] \| None` | Known face colors, if defined |
| `color_indicator` | `frozenset[str] \| None` | Scryfall face color indicator, if provided |
| `supertypes` | `frozenset[str]` | Parsed face supertypes |
| `types` | `frozenset[str]` | Parsed face card types |
| `subtypes` | `frozenset[str]` | Parsed face subtypes, including multiword names |
| `power` / `toughness` | `str \| None` | Original textual characteristics; may be symbolic |
| `loyalty` / `defense` | `str \| None` | Original textual characteristics |
| `hand_modifier` / `life_modifier` | `str \| None` | Vanguard-related characteristics, when applicable |

Every `Card` contains one or more faces, including a normalized face 0 for ordinarily single-faced cards. An empty mana-cost string `""` is distinct from `{0}` and from `None`. Color sets use `frozenset()` for a **known** empty set and `None` for a missing/not-applicable field. No face is an independently queryable Oracle identity unless Scryfall assigns it its own Oracle identity (such as an independently normalized reversible side).

### 2.3 Associated immutable records

These records are retrieved **separately**, rather than included in every `Card`:

| Record | Exposed fields | Meaning |
| --- | --- | --- |
| `Legality` | `format: str`, `status: str` | Current Scryfall legality classification for this database build |
| `SetRarity` | `set_code: str`, `rarity: str`, `game: str` | One distinct historical observation, with all three dimensions coupled; the relationship lookup can expose **all** platforms independently of search defaults |
| `Tagging` | `tag_slug: str`, `weight: str`, `annotation: str \| None` | **Direct** tag assignment; no fabricated ancestor tagging |
| `Tag` | `slug: str`, `label: str`, `description: str \| None`, `aliases: frozenset[str]`, `parents: frozenset[str]`, `children: frozenset[str]` | Tag descriptions and **direct** graph neighbors; parent/child values are public slugs |
| `SetInfo` | `code: str`, `name: str`, `set_type: str`, `released_at: str \| None`, `digital: bool` | Set-level Scryfall metadata; dates are `YYYY-MM-DD` |
| `DatabaseInfo` | `schema_version: int`, `created_at: str`, `builder_version: str`, `sources: tuple[SourceInfo, ...]` | Read-only database build information |
| `SourceInfo` | `source_type: str`, `source_updated_at: str \| None`, `downloaded_at: str`, `source_url: str`, `sha256: str`, `record_count: int` | Provenance for a particular Scryfall input |

The listed field names are the proposed v1 Python contract. Internal database UUIDs for tags and sets may be used by the engine but are not required to operate the public API. The metadata structures contain immutable values and immutable nested collections.

**Ordering:** Direct `Card.faces` preserves face order. Every returned tuple of records has deterministic ordering; related legality records sort by `(format, status)`, set-rarity records by `(set_code, rarity, game)`, taggings by `tag_slug`, and source metadata by `source_type`. No meaningful iteration order is promised for `frozenset` results.

## 3. Database lifecycle and direct access

| Interface | Public contract |
| --- | --- |
| `CardDatabase.open(path: str \| Path \| None = None) -> CardDatabase` | Open an existing compatible SQLite database, read-only. `None` resolves the package-managed default database path. No network access or automatic rebuild. |
| `db.close() -> None` | Close the handle; repeated closure is harmless. |
| `with CardDatabase.open(...) as db:` | Context manager closes the handle on exit, including exceptional exit. |
| `db[oracle_id: str] -> Card` | Strict lookup by Oracle UUID; missing key raises `KeyError`. |
| `db.get(oracle_id: str) -> Card \| None` | Optional lookup by Oracle UUID; missing key returns `None`. |
| `db.resolve(oracle_ids: Iterable[str]) -> tuple[Card, ...]` | Resolve unique Oracle IDs in deterministic order. Unknown IDs raise `KeyError`; no silent dropping. |
| `oracle_id in db` | Membership test for an Oracle ID string. |
| `len(db)` | Number of Oracle identities. |
| `db.info() -> DatabaseInfo` | Immutable database and source metadata. |

**Bulk resolution:** Input order and duplicates are discarded. Results sort by `(card.name.casefold(), card.oracle_id)`, making output deterministic even when the input is a set. Empty input returns `()`. Resolution may use batched SQLite reads and an internal object cache; object **value** is guaranteed, object **identity** and SQL query count are not.

**Closed handles:** All further database methods, query methods, catalogs, and record lookups raise `DatabaseClosedError`. Previously returned `Card`, `CardFace`, metadata, tuples, and frozensets remain valid and independently usable.

**Dataset stability:** An opened `CardDatabase` uses one fixed dataset. A separately invoked update builds and validates a temporary SQLite database before replacing `carddb.sqlite` if safe; readers must close and reopen to obtain new data. On platforms where open handles prevent replacement, the updater fails safely rather than corrupting the file. No live refresh of an existing object.

**Concurrency:** Thread-sharing guarantees are **not** part of v1; separate handles should be used when necessary. Caches and SQL connections are private implementation details.

## 4. Common card-query contract

All methods in this section return an actual `frozenset[str]` of **Oracle IDs** and never return `Card` objects, mutable collections, lazy SQL cursors, or custom query objects. `db.all_cards()` is the universe `U` for that database.

- `&` performs intersection; `|` performs union; `-` performs difference; `^` performs symmetric difference.
- `db.all_cards() - result` is complement relative to the current database universe.
- Reassignment with `&=`, `|=`, or `-=` does not mutate an existing frozenset.
- A valid search with no matching cards returns `frozenset()`.
- Input normalization is documented by query family; ordinary strings should not be interpreted as regular expressions, fuzzy searches, or a Scryfall search-language expression.

### 4.1 Universe, names, and layouts

| Method | Semantics |
| --- | --- |
| `all_cards() -> frozenset[str]` | Every stored Oracle identity |
| `by_name(name: str) -> frozenset[str]` | Exact, case-insensitive match against the whole-card name **or any face name**; not substring/fuzzy matching |
| `by_layout(layout: str) -> frozenset[str]` | Matching Scryfall card layout; case-insensitive canonical-value comparison |
| `game_changers() -> frozenset[str]` | Oracle IDs explicitly classified `game_changer=True` by Scryfall in this build; missing and `False` are excluded. |

A complete multifaced card and each of its individual face names can match `by_name()`, always returning the **parent Oracle ID**. Distinct Oracle identities with a matching name may all be returned. Unknown ordinary name or layout values return empty results, not guessed matches.

### 4.2 Color identity

| Method | Semantics |
| --- | --- |
| `exact_identity(colors: str) -> frozenset[str]` | Card identity equals the supplied colors |
| `within_identity(colors: str) -> frozenset[str]` | Card identity is a subset of the supplied colors |
| `containing_identity(colors: str) -> frozenset[str]` | Card identity is a superset of the supplied colors |

The input is a sequence of `WUBRGC` letters, case-insensitive and order-independent; duplicates are ignored. **`C` is an accepted colorless alias and is discarded during color-set normalization.** Consequently `"C"`, `""`, and `"CCC"` all normalize to the empty color set, while `"WUC"` means exactly `"WU"` for matching purposes. This is specific to queries over *colors*, where colorless is the absence of the five colors—not a sixth color. Examples:

- `exact_identity("WU")`: precisely white-blue identity.
- `within_identity("WU")`: colorless, white, blue, or white-blue identity.
- `containing_identity("WU")`: includes both white and blue, possibly additional colors.
- `exact_identity("")`: exactly colorless.
- `within_identity("")`: exactly colorless.
- `containing_identity("")`: the entire universe.
- `exact_identity("C")`: exactly colorless, identical to `exact_identity("")`.
- `within_identity("WUC")`: identical to `within_identity("WU")`.

Invalid symbols other than `WUBRGC`, or a wrong input type, raise `ValueError` or `TypeError` respectively. Numeric masks never appear in public signatures. The `C` alias is **not** applied to `produces_mana()`, where `"C"` denotes an actual colorless mana type.

### 4.3 Card colors (aggregate search)

`by_color(colors: str, *, match: Literal["contains", "exact", "within"] | None = None) -> frozenset[str]`

This is a **whole-Oracle-card discovery query**, not a requirement that all queried colors appear on a single face. Its searched color set is determined as follows:

1. When the stored whole-card `Card.colors` is known, use that value.
2. Otherwise, when **all** normalized faces have known color sets, use the union of their colors.
3. Otherwise, the aggregate color set is **unknown** and the card matches no `by_color()` predicate, including colorless searches. Missing values must never be treated as colorless.

For ordinary colored input, the **default match is `"contains"`**: `by_color("RG")` finds cards whose aggregate colors include both red and green, potentially alongside other colors. This also matches a card with a red half and a green half (for example, a split/fuse card), even if no individual face is both colors. For transforming or modal double-faced layouts, aggregating both faces is a CardDB *search convenience*, not a claim that the card possesses all those colors simultaneously in every game zone.

| Match | Predicate over known aggregate colors `A` and requested colors `Q` |
| --- | --- |
| `"contains"` | `Q ⊆ A` |
| `"exact"` | `A = Q` |
| `"within"` | `A ⊆ Q` |

**Special default for colorless:** The input uses the same `WUBRGC` notation as identity queries: `"C"`, `""`, and `"CCC"` normalize to an empty set; `"CRG"` normalizes to `"RG"`. If `match` is **omitted** (`None`), an empty normalized query defaults to `"exact"` instead of `"contains"`. Thus `by_color("C")` and `by_color("")` return *known colorless cards*; `by_color("RG")` defaults to `"contains"`. Explicit `match` arguments always take precedence. In particular, `by_color("C", match="contains")` follows ordinary set algebra and returns **all cards with a known aggregate color set** because the empty set is contained in every set. Explicit `match="within"` with an empty request selects known colorless cards.

Examples:

```python
by_color("RG")                        # aggregate contains red and green
by_color("RG", match="exact")         # aggregate exactly red-green
by_color("RG", match="within")        # aggregate is a subset of red-green
by_color("C")                         # defaults to exact colorless
by_color("", match="contains")        # explicitly matches all known aggregates
```

The `None` default in the signature is **only a marker for omitted `match`**, not an additional matching mode. Invalid explicit match values raise `ValueError`; incorrect types raise `TypeError`. `Card.colors` and each `CardFace.colors` retain their original normalized values; the aggregate used by `by_color()` does not rewrite those records or alter color-identity queries. A separate same-face color predicate is deferred from v1.

### 4.4 Types and keywords

| Method | Semantics |
| --- | --- |
| `by_type(value: str) -> frozenset[str]` | Any face has the card type |
| `by_supertype(value: str) -> frozenset[str]` | Any face has the supertype |
| `by_subtype(value: str) -> frozenset[str]` | Any face has the subtype (including multiword names) |
| `by_keyword(value: str) -> frozenset[str]` | Scryfall's **whole-card** keyword classification includes the keyword |

Names compare case-insensitively to canonical stored names. Multiple independent type queries intersect at the **Oracle-card level**, not necessarily the **same-face** level: a land face and a creature face make their card appear in both whole-card result sets. No same-face compound predicate is promised for v1.

### 4.5 Mana value and produced mana

| Method | Semantics |
| --- | --- |
| `by_mana_value(value: int \| float) -> frozenset[str]` | Exact card-level Scryfall mana value (`cmc`) |
| `mana_value_between(min_value: int \| float, max_value: int \| float) -> frozenset[str]` | Inclusive interval, both endpoints included |
| `produces_mana(symbol: str) -> frozenset[str]` | Scryfall card-level produced-mana array contains **one** requested symbol from `WUBRGC` |

Mana-value arguments must be finite real numbers (excluding `bool`, NaN, and infinities); a reversed interval raises `ValueError`. No interpretation of printed mana-cost symbols is implied. `produces_mana` accepts **exactly one** symbol (case-insensitive). `"C"` here means colorless mana, unlike color identity. A card with missing `produced_mana` does not match any symbol; the API does not assert unconditional access to the requested mana in every game state. For “either” or “both” production, callers compose the resulting sets with `|` or `&`.

### 4.6 Oracle tags

`by_tag(slug: str, *, include_descendants: bool = True) -> frozenset[str]`

Tag lookups accept canonical slugs or Scryfall aliases, case-insensitively. Exact canonical slugs take precedence over aliases. An alias resolving to multiple tags raises `AmbiguousTagError`; an unknown slug/alias raises `UnknownTagError`.

- By default, include the direct tag members **plus** all descendant tags' direct members.
- With `include_descendants=False`, include only directly assigned members.
- Descendants follow the imported **directed** tag graph, with deduplication of multiple paths.
- Query expansion does not create direct parent taggings or inherited tagging weights.
- The initial `by_tag()` API does not filter by tagging weight. `taggings_for()` exposes original direct weights and annotations.

### 4.7 Format legalities

`by_legality(format: str, status: str) -> frozenset[str]`

Return cards having exactly the requested Scryfall status for a recognized format in the imported dataset. Supported statuses are `"legal"`, `"restricted"`, `"banned"`, and `"not_legal"`; names are normalized case-insensitively. Format names are discovered from the dataset, **not** hardcoded as preferred formats. Unknown formats raise `UnknownFormatError`; unknown status values raise `ValueError`; a supported format/status pair without cards returns an empty set.

No convenience `legal_in()` is part of v1, since whether `restricted` belongs in an application pool depends on the application. Missing legality records are **not** reinterpreted as `not_legal`. These are statuses *at the imported dataset's build time*, not historical legality claims.

### 4.8 Sets, rarities, and game platforms

| Method | Semantics |
| --- | --- |
| `by_set(code: str, *, game: str \| None = "paper") -> frozenset[str]` | Cards observed in the set on paper by default, or in the explicitly selected platform(s) |
| `by_rarity(rarity: str, *, game: str \| None = "paper") -> frozenset[str]` | Cards observed at this rarity in **any** set, on paper by default |
| `by_set_rarity(code: str, rarity: str, *, game: str \| None = "paper") -> frozenset[str]` | Cards observed with that rarity **in that same set and selected game**; paper by default |

Set codes, rarities, and game names use canonical source strings with case-insensitive input. **The default `game="paper"` applies to all three set/rarity search methods.** An explicit `game=None` means **all imported platforms** (an existential match in any recorded game); `game="arena"` and `game="mtgo"` search those platforms alone. A card appearing only in a digital set will therefore not match the unqualified paper search. `set_rarities_for(oracle_id)` remains an unfiltered account of **all** stored game observations. Unknown set codes raise `UnknownSetError`; unsupported rarity or game identifiers raise `ValueError` (recognized identifiers with no qualifying members produce an empty set).

Examples:

```python
db.by_set("m21")                                 # paper only
db.by_rarity("common")                           # ever common on paper
db.by_set_rarity("m21", "common")                # common in M21 on paper
db.by_set_rarity("m21", "common", game=None)     # same set + rarity on any game
db.by_rarity("common", game="arena")             # Arena only
```

**Critical relational distinction:** `by_set("m21") & by_rarity("common")` does **not** mean “common in M21.” A card could be common elsewhere and rare in M21. `by_set_rarity("m21", "common")` retains the coupled `(set, rarity, game)` observation, as required by the frozen schema. Similarly, intersecting separate platform and rarity sets is not a substitute for a coupled platform query.

## 5. Record relationships and discovery

### 5.1 Associated data lookups

| Method | Result | Behavior |
| --- | --- | --- |
| `taggings_for(oracle_id: str)` | `tuple[Tagging, ...]` | **Direct** card taggings only; includes source weight and annotation |
| `legalities_for(oracle_id: str)` | `tuple[Legality, ...]` | All stored format/status observations for the card |
| `set_rarities_for(oracle_id: str)` | `tuple[SetRarity, ...]` | All distinct historical `(set, rarity, game)` observations |
| `get_tag(slug: str)` | `Tag \| None` | Canonical slug or unambiguous alias lookup; `None` if unknown, `AmbiguousTagError` if ambiguous |
| `get_set(code: str)` | `SetInfo \| None` | Set metadata by code; `None` if unknown |

The first three methods require a known Oracle ID; an unknown ID raises `KeyError`, not an empty tuple. A known card with no relationships returns `()`.

### 5.2 Immutable catalogs

| Property | Return type | Contents |
| --- | --- | --- |
| `db.types` | `frozenset[str]` | Known card types |
| `db.supertypes` | `frozenset[str]` | Known supertypes |
| `db.subtypes` | `frozenset[str]` | Known subtypes |
| `db.keywords` | `frozenset[str]` | Known keywords |
| `db.formats` | `frozenset[str]` | Known format identifiers |
| `db.set_codes` | `frozenset[str]` | Known set codes |
| `db.layouts` | `frozenset[str]` | Known layouts |
| `db.tag_slugs` | `frozenset[str]` | Canonical known tag slugs |
| `db.rarities` | `frozenset[str]` | Rarities represented in the dataset |
| `db.games` | `frozenset[str]` | Game-platform identifiers represented in the dataset |

Catalog values are useful for autocomplete, validation, and discoverability. They are read-only views of the opened dataset; the library does not publish its internal index dictionaries or invite callers to mutate canonical classifications.

## 6. Errors and argument validation

| Situation | Result |
| --- | --- |
| Valid search value with no matching cards | Empty `frozenset()` |
| Exact name not found | Empty `frozenset()` |
| Unknown card ID in `db[...]`, `resolve()`, or a relationship method | `KeyError` |
| Unknown card ID in `db.get()` | `None` |
| Unknown tag slug/alias in `by_tag()` | `UnknownTagError` |
| Ambiguous tag alias in `by_tag()` / `get_tag()` | `AmbiguousTagError` |
| Unknown format in `by_legality()` | `UnknownFormatError` |
| Unknown set code in set queries | `UnknownSetError` |
| Invalid WUBRGC color-set notation, match mode, status, rarity, game, or numeric range | `ValueError` |
| Wrong Python argument type | `TypeError` |
| Attempt to use a closed database | `DatabaseClosedError` |
| Missing, malformed, or incompatible SQLite file on open | Informative open/compatibility error; never silently rebuild or download |

The package exposes a small, documented exception hierarchy for domain-specific failures; familiar Python errors retain their standard roles. In particular, unknown ordinary type/keyword/name/layout values yield empty sets, while names identifying required catalog entities (a tag, format, or set in a specialized query) fail loudly when unknown.

## 7. Internal engine constraints (not public compatibility promises)

The public semantics above remain stable even if the implementation later changes its indexing technique.

- **Baseline representation:** Canonical Oracle UUID strings, with memberships constructed as temporary `set[str]` and published as `frozenset[str]`. No custom public result type.
- **Baseline hydration:** Lazily load an entire attribute category upon first use. Loading is atomic to the index store: no partial category is exposed after failure. Large categories may use more selective hydration if benchmarks justify it.
- **Base indexes:** Map individual or **compound** attribute keys to immutable memberships. Base memberships represent persisted facts, never independently invented classifications.
- **Derived results:** Color-identity subsets/supersets, aggregate-color memberships and comparisons, numeric ranges, tag descendants, and historical projections may be computed and cached. Their cache entries are separate from direct base memberships.
- **Tag specialization:** Preserve direct taggings and directed edges; cache descendants and expanded members independently; do not duplicate ancestor taggings in SQLite.
- **Historical specialization:** Preserve `(set, rarity, game)` coupling in the underlying membership key before producing projections.
- **Record resolution:** Use batched retrieval and bounded caches if useful, while preserving immutable values and deterministic order.
- **Memory optimization:** Shared string instances, integer IDs, bitmap indexes, eviction policies, and eager/value-lazy alternatives remain replaceable implementation choices; none should leak into public return types.
- **SQLite responsibility:** Provide durable normalized facts, membership loading, record resolution, and build provenance. Python handles repeated search and set algebra. There is no required user-facing SQL interface or custom search DSL.

A cached set handed to a caller remains stable even if the internal cache later evicts its entry. An opened handle is never rebuilt in place; to use updated source data, open a new handle.

## 8. Contract acceptance tests and benchmarks

The API must be verified independently of network ingestion using synthetic, normalized fixtures, and then against representative real Scryfall records after the importer exists.

### Behavioral tests

1. **Set semantics:** Every query returns `frozenset[str]`, and union/intersection/difference/complement produce the expected Oracle IDs without mutating inputs.
2. **Immutability:** Frozen card and metadata objects contain no mutable public collections; returned records remain usable after `db.close()`.
3. **Face and color aggregation:** A multifaced card occurs once in matching whole-card indexes; intersecting independent face-derived type queries can match across different faces. `by_color()` uses the known whole-card color set, or the union of all known face colors if the whole-card field is unavailable. Verify that a split/fuse card with red and green halves matches `by_color("RG")`, without claiming both colors coexist on each face.
4. **Missing data:** `None` for unknown color or mana properties never behaves like an empty color set or zero mana.
5. **Color identity and aggregated colors:** Check all 32 identities, `"" == "C" == "cC"`, mixed input such as `"WUC" == "WU"`, and duplicate/reordered lowercase inputs. Verify `by_color("RG")` defaults to contains, `by_color("C")` defaults to exact known-colorless, explicitly supplied `match` overrides the contextual default, `by_color("C", match="contains")` includes every **known** aggregate, and incomplete face colors never become colorless. Confirm `produces_mana("C")` means actual colorless mana. Public results contain UUID strings, not internal masks.
6. **Tags:** Check direct-only versus descendant-expanded results, multi-parent graphs, duplicate ancestry, canonical slug precedence, unknown aliases, and alias ambiguity.
7. **Legality:** Distinguish all four statuses and missing records; support formats appearing dynamically in data.
8. **Historical coupling and defaults:** Verify a card common in Set A but rare in Set B does not appear in `by_set_rarity("B", "common")`; enforce same-platform qualifications. All set/rarity searches default to paper, explicitly passing `game=None` includes every recorded game, and `set_rarities_for()` remains all-platform.
9. **Lookup:** Strict/optional lookup behavior, duplicate input IDs, deterministic resolution order, and failure on unknown IDs.
10. **Lifecycle:** Queries fail after closure but already returned values survive; no automatic network or implicit rebuild; an explicit safe-update workflow does not alter an existing handle's dataset.
11. **Equivalence:** For each supported predicate, indexed results equal an independently evaluated reference predicate over normalized fixture records.
12. **Representative selection:** Permute source printing order; canonical `Card` records, fields, faces, layout, and legalities must be stable. Cover normal vs reversible Blood Crypt, identical-name art-series faces (which must **not** be globally renamed), reversible-only identities, printing-dependent layouts, and nonstandard gold-bordered legality differences.
13. **Field coverage:** For each current source gameplay field, assert that it is either mapped to the agreed schema or explicitly classified as deferred, omitted, or printing-dependent. Verify `Card.game_changer` preserves `True`, `False`, and unavailable (`None`) distinctly, while `game_changers()` includes only `True`, in agreement with the amended `cards.game_changer` column. Reject unexpected newly required mechanics without silently discarding them.

### Benchmarks (not yet performance guarantees)

Measure open time, first-category query latency, repeated-query latency, index hydration time, expanded tag queries, color subset queries, set/rarity coupling, bulk card resolution, peak memory, and on-disk database size. Compare the native-string baseline with naive scans and indexed SQLite, then compare eager, category-lazy, and value-lazy hydration. Only optimize if evidence supports the tradeoff.

No hard latency or memory promises are part of the v1 public compatibility contract before measurements exist.

## 9. Default Cards representative selection and field coverage

This section makes the **read-only public `Card` contract** precise when ingesting `default_cards`. It does not add printing records or promise fields that the agreed SQLite schema cannot store. The companion `schema.md` defines the persistence model and the original deterministic selection policy; any enhancement requiring new columns or tables must be approved as a separate schema revision.

### 9.1 Identity and source-shape normalization

A record in `default_cards` describes a *printing*, not necessarily the canonical shape of its Oracle identity. It supplies both Oracle characteristics and set/rarity/game observations. The importer groups normalized candidates by **Oracle ID**, not name or printing ID:

1. For an ordinary record with a root `oracle_id`, produce one candidate from its root card-level fields, plus one or more face rows from `card_faces` or a normalized single face from root-level face properties.
2. For a `reversible_card` record, produce **one candidate for each face's own `oracle_id`**, using that face's own name, layout, type line, mana value, Oracle text, and other side-local facts. The root's `name` is the physical combination of the two sides; it is **not** the canonical name for either Oracle identity. Do **not** distribute the combined root `color_identity`, `keywords`, or `reserved` flag to both identities.
3. Independently of canonical candidate selection, extract `(oracle_id, set_id, rarity, game)` observations from **every** relevant printing and from both independent reversible sides. No representative's set, rarity, or games can stand in for the aggregate history.
4. Normalize missing versus known-empty fields, preserve face order and original Oracle text, and validate that the candidate can be represented by `cards`, `faces`, `face_types`, `card_keywords`, and `card_legalities` without inventing values.

The public `Card` has **one selected normalized face arrangement**, not an inventory of printing layouts. A card printed in both `normal` and `reversible_card` form may expose `Card.layout == "normal"` even though a reversible printing also exists. This is intentional and means that `by_layout()` queries the representative *card model*, not historical print formats.

### 9.2 Representative selection: complete, deterministic, and order-independent

Select **one coherent candidate** per Oracle ID, not an arbitrary first record and not a mixture of fields taken opportunistically from incompatible printings.

- **Eligibility:** A candidate missing required Oracle-level characteristics is incomplete. It cannot supersede a complete candidate merely because it is newer. Validate names, type line, mana value, color identity, keywords, reserved status, and any face-specific required fields against the selected source layout.
- **Preference:** Among complete, semantically compatible candidates, follow the companion schema's deterministic priority: **non-reversible first**, then English (`lang == "en"`), then newest `released_at`, then lexically ordered Scryfall printing `id` for ties. An optional preference for paper or non-gold-bordered printings is *not* silently added to this frozen ordering; see legality treatment below.
- **Cross-check:** Compare normalized non-print-specific mechanical facts across *reliable candidates* for the same Oracle ID. Detect true disagreements rather than treating every missing optional field as a contradiction. A change in **physical `layout` alone** is expected and is not an Oracle-characteristic conflict.
- **No accidental duplication:** If a reversible printing is named `Blood Crypt // Blood Crypt` but a complete ordinary printing of the same Oracle identity exists, the ordinary `Blood Crypt` representation wins. Do **not** globally rewrite names of the form `X // X`: that is also a valid Scryfall name form for certain art-series and double-faced-token records.
- **Unresolvable identities:** If a reversible-only Oracle identity lacks required independent characteristics, fail strict normalization with diagnostics; do not assign both sides the root's combined characteristics, silently omit the identity, or silently fill it using rules-text guesses.
- **Stable tie-breaking:** The result must be identical regardless of the order of printing objects in the bulk file. A candidate's Scryfall printing ID is an **import-only** tie-breaker, never a persistent `Card` field or public query ID.

**Legality is not an invariant across every printing.** Scryfall legality values may differ for tournament-ineligible editions (for example, gold-bordered cards), even when the underlying Oracle mechanics agree. Consequently, normalizing a `Card` and selecting its **current legality** must be treated as related but **separate projections** from the same Oracle-ID candidate group:

1. Choose the coherent mechanical representative by the fixed ordering above. This determines `Card.name`, `layout`, faces, and other intrinsic fields.
2. For legality, first prefer candidates without printing-specific tournament-ineligibility markers, particularly `border_color == "gold"` or `set_type == "memorabilia"`, when ordinary candidates exist. These markers are **only selection signals**; CardDB does not infer legality rules from them. Among otherwise comparable candidates, use the same deterministic language/date/printing-ID tie-breakers.
3. Compare the legality maps reported by the **highest-eligibility candidate group**. If they agree, persist that exact Scryfall status per format. Log differences found in lower-priority (e.g. gold-bordered) candidates without treating them as disagreements in canonical Oracle text. If equally eligible ordinary candidates disagree, **strict import fails with diagnostics** rather than silently choosing whichever printing happened to be last.
4. If no ordinary candidate exists, preserve the status supplied by the best available source candidate and report that its source is nonstandard; do not fabricate a format legality from rules text or from the legality of a different Oracle identity.

This requires **no change to the 13-table schema**, since `card_legalities` already stands apart from `cards`. However, the frozen `schema.md` §2.1 currently describes the normalized gameplay projection as including legalities; before implementing ingestion, its explanatory validation wording should be clarified to distinguish these two projections. This is a documentation-alignment task, **not** permission to alter its SQL tables or the user-facing set queries.

**Scope of validation:** The above is the required normalization *contract*, supported by Scryfall's layout model and independent audits of `default_cards`, **not** a claim that the current complete bulk export was downloaded and exhaustively checked for this document. A full-export integration run remains a release gate.

### 9.3 Audit of mechanically relevant Scryfall fields

The core `Card`/`CardFace` definitions cover the following source-backed **Oracle-level properties**: identity, selected layout and name, type lines and parsed types, rules text, mana costs and mana value, card/face colors and color indicators, color identity, produced mana, keywords, power/toughness, loyalty, defense, Vanguard hand/life modifiers, and Reserved List status. Current legalities, Oracle tags, and coupled set/rarity/game history are deliberately accessible as **separate immutable relationship records**.

Three additional Scryfall fields deserve deliberate treatment rather than silent loss:

| Scryfall field | Why it matters | v1 contract under the agreed schema |
| --- | --- | --- |
| `all_parts` | Links closely related card objects, including generated tokens and meld parts/results. Related objects identify **Scryfall printing IDs**, not necessarily Oracle IDs; mapping into the Oracle model would require validated cross-record resolution. | **Not exposed.** Retaining this correctly would require a relationship model and a reviewed schema change. It is a substantial mechanical extension candidate. |
| `attraction_lights` | Which numbers are lit on an Unfinity Attraction matters to gameplay. It is documented as **printing-level** information and patterns may differ among printings of the same Oracle identity. | **Not exposed.** A single `Card.attraction_lights` value would be misleading. A future design could aggregate observed patterns without retaining printing records, but requires new relational facts and a schema change. |
| `game_changer` | A current Scryfall classification relevant to Commander Brackets; it is not an intrinsic rules-text characteristic. Verify its presence and missing/false behavior against the imported `default_cards` data. | **Included in v1:** nullable `cards.game_changer`, public `Card.game_changer: bool | None`, and set query `game_changers()`. CardDB does not enforce Commander Bracket rules. |

Other relevant omissions have a clear scope rationale: `edhrec_rank` and `penny_rank` are third-party popularity metrics rather than mechanics; illustration, artist, printing rarity (except the agreed historical aggregates), border, foil, flavor, pricing, and localization are printing metadata; rulings require a separate Scryfall endpoint and are not a property of the `default_cards` Card object. New mechanics should be audited by source-field name when Scryfall extends its export, not silently ignored.

**Coverage conclusion:** The existing `Card` and `CardFace` models cover the principal Oracle-level gameplay fields and Vanguard characteristics, but not every mechanically meaningful fact available in Scryfall. The approved `game_changer` extension is reflected in `schema.md` as one nullable `cards` column, consistent with this API contract. Attraction light patterns remain deferred because they're printing-dependent and would require additional persistent relationships. `all_parts` is deliberately omitted from scope rather than planned as a v1 extension.

**Research references:** [Scryfall Card field definitions](https://github.com/scryfall/api-types/blob/main/src/objects/Card/CardFields.ts), [Scryfall card layouts](https://github.com/scryfall/api-types/blob/main/src/objects/Card/Card.ts), [Scryfall face definitions](https://github.com/scryfall/api-types/blob/main/src/objects/Card/CardFace.ts), [Related-card model](https://github.com/scryfall/api-types/blob/main/src/objects/Card/RelatedCard.ts), [current Rust Scryfall client fields](https://docs.rs/scryfall/latest/scryfall/card/struct.Card.html), and an [independent Default Cards consistency audit](https://tangled.org/eth0net.io/manaweb/blob/main/docs/scryfall.md). The latter documents reversible duplicate-name cases and cross-printing legality differences; its findings are useful corroboration, not a replacement for our own integration tests.

## 10. Explicitly deferred functionality

These are **not** part of the initial public contract:

- User-authored indexes, persistent user classifications, or writing/modifying the canonical database through `CardDatabase`.
- Deck construction, Commander eligibility, house-banned cards, restricted-copy enforcement, or format-specific policy layers.
- Scryfall query-language parsing, fuzzy/substring name matching, general-purpose Boolean query-builder objects, or arbitrary Oracle-text semantic inference.
- Same-face compound predicates, numeric interpretation of symbolic power/toughness, and parsed alternate casting characteristics.
- Printing records, artwork, pricing, card collections, historical Oracle text, and dated format legality timelines.
- Print-dependent Attraction light patterns and Scryfall `all_parts` related-card links. Attractions are deferred; `all_parts` is omitted. `game_changer` **is** in scope and stored in the nullable `cards.game_changer` column as described in §9.3.
- Third-party data providers, web services, or database backends other than the agreed SQLite source.

Applications remain free to create their own sets and combine them with CardDB's immutable query results; doing so does not require an application-defined-index subsystem.

## 11. Compatibility boundary

This document defines the intended v1 **library-facing** behavior. Before public `1.0.0`, names/signatures may be corrected through deliberate design review; once published as stable, changes to method names, return types, record fields, search meaning, error categories, or the meaning of absence are compatibility-affecting changes.

The following are **not** public compatibility commitments: SQLite query plans, internal bit allocations, memory/index layout, hydration timing, cache hit behavior, caching object identity, and module boundaries of the internal implementation. Database schema compatibility is governed separately by the frozen `schema.md` and SQLite's `user_version`.

**Implementation gate:** Implement and test the pure-Python set/index engine against synthetic fixtures first. Integrate SQLite and Scryfall ingestion later, without changing this contract merely to fit a particular storage query shape. Before freezing the importer, reconcile the legality anomaly described in §9.2 with `schema.md` validation wording, and verify that the implemented nullable `cards.game_changer` column and API behave consistently. Do not add Attraction-pattern or `all_parts` persistence without a new scope decision.
