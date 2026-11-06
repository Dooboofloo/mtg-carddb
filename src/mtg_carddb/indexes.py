"""Internal, storage-independent inverted indexes for Oracle-ID memberships.

The category loaders supplied to IndexStore can eventually come from SQLite,
but this module knows nothing about SQL, Scryfall, or Magic mechanics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Hashable, TypeAlias

IndexKey: TypeAlias = Hashable
MembershipRow: TypeAlias = tuple[IndexKey, str]
CategoryLoader: TypeAlias = Callable[[], Iterable[MembershipRow]]


def _freeze_members(
    members: Iterable[str],
    valid_ids: frozenset[str] | None,
) -> frozenset[str]:
    """Freeze a membership collection and optionally enforce a known universe."""
    if isinstance(members, (str, bytes)):
        raise TypeError("members must be an iterable of IDs, not a string")

    result: set[str] = set()
    for oracle_id in members:
        if not isinstance(oracle_id, str):
            raise TypeError("each member ID must be a string")
        if valid_ids is not None and oracle_id not in valid_ids:
            raise ValueError(f"member ID is outside the known universe: {oracle_id!r}")
        result.add(oracle_id)
    return frozenset(result)


def build_index(
    rows: Iterable[MembershipRow],
    *,
    valid_ids: frozenset[str] | None = None,
) -> Mapping[IndexKey, frozenset[str]]:
    """Build an immutable mapping from index keys to sets of member IDs.

    Repeated (key, ID) rows are deduplicated. Compound hashable keys such as
    (set_code, rarity, game) are preserved as a single key.
    """
    buckets: dict[IndexKey, set[str]] = {}
    for key, oracle_id in rows:
        # Provide an understandable failure rather than exposing a dict error.
        try:
            hash(key)
        except TypeError as exc:
            raise TypeError("index keys must be hashable") from exc

        if not isinstance(oracle_id, str):
            raise TypeError("each member ID must be a string")
        if valid_ids is not None and oracle_id not in valid_ids:
            raise ValueError(f"member ID is outside the known universe: {oracle_id!r}")

        buckets.setdefault(key, set()).add(oracle_id)

    # The original dictionaries and sets are not exposed anywhere else.
    return MappingProxyType({
        key: frozenset(ids)
        for key, ids in buckets.items()
    })


class IndexStore:
    """Load complete index categories on first access and cache their results.

    `loaders` is supplied by internal CardDB wiring, not by applications. Each
    loader emits (key, Oracle ID) rows for one category. A loaded category
    remains unchanged for the lifetime of this IndexStore.
    """

    def __init__(
        self,
        loaders: Mapping[str, CategoryLoader],
        *,
        valid_ids: Iterable[str] | None = None,
    ) -> None:
        self._loaders = dict(loaders)
        for category, loader in self._loaders.items():
            if not isinstance(category, str) or not callable(loader):
                raise TypeError("loaders must map category names to callables")

        self._valid_ids = (
            None if valid_ids is None else _freeze_members(valid_ids, None)
        )
        self._loaded: dict[str, Mapping[IndexKey, frozenset[str]]] = {}
        self._loading: set[str] = set()
        self._derived: dict[tuple[str, IndexKey], frozenset[str]] = {}

    def _category(self, category: str) -> Mapping[IndexKey, frozenset[str]]:
        if category not in self._loaders:
            raise KeyError(f"unknown index category: {category!r}")
        if category in self._loaded:
            return self._loaded[category]
        if category in self._loading:
            raise RuntimeError(f"recursive index load for category {category!r}")

        self._loading.add(category)
        try:
            index = build_index(
                self._loaders[category](),
                valid_ids=self._valid_ids,
            )
        finally:
            self._loading.remove(category)

        # Publish only a completely constructed category. Failed loads can retry.
        self._loaded[category] = index
        return index

    def get(self, category: str, key: IndexKey) -> frozenset[str]:
        """Return members for a known category/key, or an empty frozen set."""
        return self._category(category).get(key, frozenset())

    def values(self, category: str) -> frozenset[IndexKey]:
        """Discover available keys in a category, hydrating it if needed."""
        return frozenset(self._category(category))

    def derive(
        self,
        namespace: str,
        key: IndexKey,
        build: Callable[[], Iterable[str]],
    ) -> frozenset[str]:
        """Memoize one derived membership without changing any base index.

        Namespace/key pairs must uniquely identify a computation for this
        snapshot. The callback runs only when the value is first requested.
        """
        cache_key = (namespace, key)
        if cache_key not in self._derived:
            result = _freeze_members(build(), self._valid_ids)
            self._derived[cache_key] = result
        return self._derived[cache_key]
