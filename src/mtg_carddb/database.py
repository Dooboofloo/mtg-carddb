"""Public, composable Oracle-ID search interface for a read-only CardDB v1."""

from __future__ import annotations

import os
from collections.abc import Iterable
from math import isfinite
from pathlib import Path
from typing import Literal

from .models import Card, DatabaseInfo, Legality, SetInfo, SetRarity, Tag, Tagging
from .storage import SQLiteStore
from .freshness import (
    DEFAULT_STALE_AFTER_DAYS,
    require_database_file,
    report_database_freshness,
)

_COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
_MANA_BITS = {**_COLOR_BITS, "C": 32}
_STATUSES = frozenset({"legal", "restricted", "banned", "not_legal"})


class UnknownTagError(ValueError):
    """A requested tag slug or alias is not in this database."""


class UnknownFormatError(ValueError):
    """A requested legality format is not in this database."""


class UnknownSetError(ValueError):
    """A requested set code is not in this database."""


def _text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value.casefold()


def _color_mask(colors: str) -> int:
    if not isinstance(colors, str):
        raise TypeError("colors must be a string")
    mask = 0
    for symbol in colors.upper():
        if symbol == "C":
            continue  # Colorless is the absence of WUBRG, not a sixth color.
        if symbol not in _COLOR_BITS:
            raise ValueError(f"invalid color symbol: {symbol!r}")
        mask |= _COLOR_BITS[symbol]
    return mask


def _number(value: int | float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return number


def _default_database_path() -> Path:
    """Default per-user data path; shared with the future init/update CLI."""
    if os.name == "nt":
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or (Path.home() / "AppData" / "Local")
        )
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME")
            or (Path.home() / ".local" / "share")
        )
    return base / "mtg-carddb" / "carddb.sqlite"


def default_database_path() -> Path:
    """Return the per-user database path without creating any files.

    CardDatabase.open(None) and the CLI use the same resolver.
    Environment variables are read when the function is called.
    """
    return _default_database_path()


# Only constant, internal identifiers are used in these SQL statements.
_CATALOG_SQL = {
    "types": "SELECT DISTINCT value FROM face_types WHERE category = 'type'",
    "supertypes": "SELECT DISTINCT value FROM face_types WHERE category = 'supertype'",
    "subtypes": "SELECT DISTINCT value FROM face_types WHERE category = 'subtype'",
    "keywords": "SELECT DISTINCT keyword FROM card_keywords",
    "formats": "SELECT DISTINCT format FROM card_legalities",
    "set_codes": "SELECT code FROM sets",
    "layouts": "SELECT DISTINCT layout FROM cards",
    "tag_slugs": "SELECT slug FROM tags",
    "rarities": "SELECT DISTINCT rarity FROM card_set_rarities",
    "games": "SELECT DISTINCT game FROM card_set_rarities",
}


class CardDatabase:
    """Search a fixed, read-only CardDB snapshot using sets of Oracle IDs.

    Open with CardDatabase.open(path). No importing, downloading, or rebuilding
    happens as part of opening or querying.
    """

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store
        self._indexes = store.index_store()
        self._ids = store.all_ids()
        self._catalog_cache: dict[str, frozenset[str]] = {}
        self._closed = False

    @classmethod
    def open(
        cls,
        path: str | Path | None = None,
        *,
        warn_if_stale: bool = True,
        stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
    ) -> CardDatabase:
        """Open an existing read-only CardDB database.

        Raises DatabaseNotInitializedError when the database is missing.

        By default, logs a nonfatal warning if the Scryfall source data
        is stale or its freshness cannot be established. Warnings can
        be disabled or the age threshold customized.

        Opening never downloads, rebuilds, or modifies the database.
        """
        if type(warn_if_stale) is not bool:
            raise TypeError("warn_if_stale must be a bool")

        resolved_path = (
            _default_database_path()
            if path is None
            else Path(path)
        )

        # Fail with an actionable initialization message instead
        # of allowing SQLite to report a generic missing-file error.
        require_database_file(resolved_path)

        store = SQLiteStore.open(resolved_path)

        try:
            database = cls(store)

            if warn_if_stale:
                report_database_freshness(
                    database.info(),
                    resolved_path,
                    stale_after_days=stale_after_days,
                )

            return database

        except BaseException:
            # Includes failures during object construction or
            # freshness inspection. Never leak an opened connection.
            store.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            from .storage import DatabaseClosedError
            raise DatabaseClosedError("CardDB database handle is closed")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._store.close()

    def __enter__(self) -> CardDatabase:
        self._ensure_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __len__(self) -> int:
        self._ensure_open()
        return len(self._ids)

    def __contains__(self, oracle_id: object) -> bool:
        self._ensure_open()
        return isinstance(oracle_id, str) and oracle_id in self._ids

    def __getitem__(self, oracle_id: str) -> Card:
        card = self.get(oracle_id)
        if card is None:
            raise KeyError(oracle_id)
        return card

    def get(self, oracle_id: str) -> Card | None:
        self._ensure_open()
        if not isinstance(oracle_id, str):
            raise TypeError("oracle_id must be a string")
        if oracle_id not in self._ids:
            return None
        return self._store.load_card(oracle_id)

    def resolve(self, oracle_ids: Iterable[str]) -> tuple[Card, ...]:
        self._ensure_open()
        if isinstance(oracle_ids, (str, bytes)):
            raise TypeError(
                "oracle_ids must be an iterable of IDs, not a string"
            )
        ids: set[str] = set()
        for oracle_id in oracle_ids:
            if not isinstance(oracle_id, str):
                raise TypeError("each oracle_id must be a string")
            if oracle_id not in self._ids:
                raise KeyError(oracle_id)
            ids.add(oracle_id)

        cards = [self[oracle_id] for oracle_id in ids]
        return tuple(sorted(
            cards,
            key=lambda card: (card.name.casefold(), card.oracle_id),
        ))

    def info(self) -> DatabaseInfo:
        self._ensure_open()
        return self._store.info()

    def all_cards(self) -> frozenset[str]:
        self._ensure_open()
        return self._ids

    def by_name(self, name: str) -> frozenset[str]:
        self._ensure_open()
        return self._indexes.get("name", _text(name, "name"))

    def _casefold_members(
        self, category: str, value: str
    ) -> frozenset[str]:
        """Case-insensitive lookup without changing canonical index keys."""
        return self._indexes.derive(
            f"casefold:{category}",
            value,
            lambda: set().union(*(
                self._indexes.get(category, key)
                for key in self._indexes.values(category)
                if key.casefold() == value
            )),
        )

    def by_layout(self, layout: str) -> frozenset[str]:
        self._ensure_open()
        return self._casefold_members("layout", _text(layout, "layout"))

    def game_changers(self) -> frozenset[str]:
        self._ensure_open()
        return self._indexes.get("game_changer", 1)

    def exact_identity(self, colors: str) -> frozenset[str]:
        self._ensure_open()
        return self._indexes.get("identity", _color_mask(colors))

    def _mask_members(
        self, category: str, mask: int, mode: str
    ) -> frozenset[str]:
        def build() -> set[str]:
            result: set[str] = set()
            for existing in self._indexes.values(category):
                if (
                    (mode == "contains" and existing & mask == mask)
                    or (mode == "within" and existing & mask == existing)
                    or (mode == "exact" and existing == mask)
                ):
                    result.update(self._indexes.get(category, existing))
            return result

        return self._indexes.derive(
            f"mask:{category}:{mode}", mask, build
        )

    def within_identity(self, colors: str) -> frozenset[str]:
        self._ensure_open()
        return self._mask_members(
            "identity", _color_mask(colors), "within"
        )

    def containing_identity(self, colors: str) -> frozenset[str]:
        self._ensure_open()
        return self._mask_members(
            "identity", _color_mask(colors), "contains"
        )

    def by_color(
        self,
        colors: str,
        *,
        match: Literal["contains", "exact", "within"] | None = None,
    ) -> frozenset[str]:
        self._ensure_open()
        mask = _color_mask(colors)

        if match is None:
            match = "exact" if mask == 0 else "contains"
        elif not isinstance(match, str):
            raise TypeError("match must be a string or None")
        elif match not in ("contains", "exact", "within"):
            raise ValueError(f"invalid color match mode: {match!r}")

        return self._mask_members("aggregate_color", mask, match)

    def by_type(self, value: str) -> frozenset[str]:
        self._ensure_open()
        return self._casefold_members("type", _text(value, "type"))

    def by_supertype(self, value: str) -> frozenset[str]:
        self._ensure_open()
        return self._casefold_members(
            "supertype", _text(value, "supertype")
        )

    def by_subtype(self, value: str) -> frozenset[str]:
        self._ensure_open()
        return self._casefold_members(
            "subtype", _text(value, "subtype")
        )

    def by_keyword(self, value: str) -> frozenset[str]:
        self._ensure_open()
        return self._casefold_members(
            "keyword", _text(value, "keyword")
        )

    def by_mana_value(self, value: int | float) -> frozenset[str]:
        self._ensure_open()
        return self._indexes.get(
            "mana_value", _number(value, "value")
        )

    def mana_value_between(
        self, min_value: int | float, max_value: int | float
    ) -> frozenset[str]:
        self._ensure_open()
        low = _number(min_value, "min_value")
        high = _number(max_value, "max_value")

        if low > high:
            raise ValueError("min_value must be <= max_value")

        def build() -> set[str]:
            result: set[str] = set()
            for value in self._indexes.values("mana_value"):
                if low <= value <= high:
                    result.update(
                        self._indexes.get("mana_value", value)
                    )
            return result

        return self._indexes.derive(
            "mana_range", (low, high), build
        )

    def produces_mana(self, symbol: str) -> frozenset[str]:
        self._ensure_open()
        if not isinstance(symbol, str):
            raise TypeError("symbol must be a string")
        if len(symbol) != 1 or symbol.upper() not in _MANA_BITS:
            raise ValueError(
                "symbol must be one of W, U, B, R, G, or C"
            )

        mask = _MANA_BITS[symbol.upper()]

        def build() -> set[str]:
            result: set[str] = set()
            for present in self._indexes.values("produced_mana"):
                if present & mask:
                    result.update(
                        self._indexes.get("produced_mana", present)
                    )
            return result

        return self._indexes.derive(
            "produces_mana", mask, build
        )

    def get_tag(self, slug: str) -> Tag | None:
        self._ensure_open()
        _text(slug, "slug")
        return self._store.get_tag(slug)

    def by_tag(
        self, slug: str, *, include_descendants: bool = True
    ) -> frozenset[str]:
        self._ensure_open()
        if type(include_descendants) is not bool:
            raise TypeError("include_descendants must be bool")

        tag = self.get_tag(slug)
        if tag is None:
            raise UnknownTagError(
                f"unknown Oracle tag: {slug!r}"
            )

        key = tag.slug.casefold()
        if not include_descendants:
            return self._casefold_members("tag", key)

        def build() -> set[str]:
            result: set[str] = set()
            visited: set[str] = set()
            remaining = [key]

            while remaining:
                current = remaining.pop()
                if current in visited:
                    continue
                visited.add(current)
                result.update(
                    self._casefold_members("tag", current)
                )
                entry = self._store.get_tag(current)
                if entry is not None:
                    remaining.extend(
                        child.casefold()
                        for child in entry.children
                    )

            return result

        return self._indexes.derive("expanded_tag", key, build)

    def by_legality(
        self, format: str, status: str
    ) -> frozenset[str]:
        self._ensure_open()
        code = _text(format, "format")
        state = _text(status, "status")

        if state not in _STATUSES:
            raise ValueError(
                f"unknown legality status: {status!r}"
            )
        if code not in {
            value.casefold() for value in self.formats
        }:
            raise UnknownFormatError(
                f"unknown format: {format!r}"
            )

        return self._indexes.derive(
            "legality_casefold",
            (code, state),
            lambda: set().union(*(
                self._indexes.get("legality", key)
                for key in self._indexes.values("legality")
                if (
                    key[0].casefold() == code
                    and key[1].casefold() == state
                )
            )),
        )

    def _check_set(self, code: str) -> str:
        key = _text(code, "code")
        if key not in {
            value.casefold() for value in self.set_codes
        }:
            raise UnknownSetError(
                f"unknown set code: {code!r}"
            )
        return key

    def _check_rarity(self, rarity: str) -> str:
        key = _text(rarity, "rarity")
        if key not in {
            value.casefold() for value in self.rarities
        }:
            raise ValueError(f"unknown rarity: {rarity!r}")
        return key

    def _check_game(self, game: str | None) -> str | None:
        if game is None:
            return None
        key = _text(game, "game")
        if key not in {
            value.casefold() for value in self.games
        }:
            raise ValueError(f"unknown game: {game!r}")
        return key

    def _history_members(
        self,
        *,
        code: str | None = None,
        rarity: str | None = None,
        game: str | None,
    ) -> frozenset[str]:
        def build() -> set[str]:
            result: set[str] = set()
            for set_code, actual_rarity, platform in (
                self._indexes.values("set_rarity")
            ):
                if (
                    (code is None or code == set_code.casefold())
                    and (
                        rarity is None
                        or rarity == actual_rarity.casefold()
                    )
                    and (
                        game is None
                        or game == platform.casefold()
                    )
                ):
                    result.update(self._indexes.get(
                        "set_rarity",
                        (set_code, actual_rarity, platform),
                    ))
            return result

        return self._indexes.derive(
            "history", (code, rarity, game), build
        )

    def by_set(
        self, code: str, *, game: str | None = "paper"
    ) -> frozenset[str]:
        self._ensure_open()
        return self._history_members(
            code=self._check_set(code),
            game=self._check_game(game),
        )

    def by_rarity(
        self, rarity: str, *, game: str | None = "paper"
    ) -> frozenset[str]:
        self._ensure_open()
        return self._history_members(
            rarity=self._check_rarity(rarity),
            game=self._check_game(game),
        )

    def by_set_rarity(
        self,
        code: str,
        rarity: str,
        *,
        game: str | None = "paper",
    ) -> frozenset[str]:
        self._ensure_open()
        return self._history_members(
            code=self._check_set(code),
            rarity=self._check_rarity(rarity),
            game=self._check_game(game),
        )

    def legalities_for(
        self, oracle_id: str
    ) -> tuple[Legality, ...]:
        self._ensure_open()
        _text(oracle_id, "oracle_id")
        return self._store.legalities_for(oracle_id)

    def taggings_for(
        self, oracle_id: str
    ) -> tuple[Tagging, ...]:
        self._ensure_open()
        _text(oracle_id, "oracle_id")
        return self._store.taggings_for(oracle_id)

    def set_rarities_for(
        self, oracle_id: str
    ) -> tuple[SetRarity, ...]:
        self._ensure_open()
        _text(oracle_id, "oracle_id")
        return self._store.set_rarities_for(oracle_id)

    def get_set(self, code: str) -> SetInfo | None:
        self._ensure_open()
        _text(code, "code")
        return self._store.get_set(code)

    def _catalog(self, name: str) -> frozenset[str]:
        self._ensure_open()
        if name not in self._catalog_cache:
            self._catalog_cache[name] = frozenset(
                row[0]
                for row in self._store._rows(
                    _CATALOG_SQL[name]
                )
            )
        return self._catalog_cache[name]

    @property
    def types(self) -> frozenset[str]:
        return self._catalog("types")

    @property
    def supertypes(self) -> frozenset[str]:
        return self._catalog("supertypes")

    @property
    def subtypes(self) -> frozenset[str]:
        return self._catalog("subtypes")

    @property
    def keywords(self) -> frozenset[str]:
        return self._catalog("keywords")

    @property
    def formats(self) -> frozenset[str]:
        return self._catalog("formats")

    @property
    def set_codes(self) -> frozenset[str]:
        return self._catalog("set_codes")

    @property
    def layouts(self) -> frozenset[str]:
        return self._catalog("layouts")

    @property
    def tag_slugs(self) -> frozenset[str]:
        return self._catalog("tag_slugs")

    @property
    def rarities(self) -> frozenset[str]:
        return self._catalog("rarities")

    @property
    def games(self) -> frozenset[str]:
        return self._catalog("games")
