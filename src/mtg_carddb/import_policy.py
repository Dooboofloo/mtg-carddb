"""Source-backed exclusions used during CardDB imports.

Only Oracle identities exclusive to art_series printings may have
their tag assignments excluded. Unknown references remain errors.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping
from typing import Any


def _source_oracle_ids(
    printing: Mapping[str, Any],
) -> frozenset[str]:
    """Extract Oracle identities represented by a source printing."""

    if printing.get("layout") == "reversible_card":
        faces = printing.get("card_faces")

        if not isinstance(faces, list):
            return frozenset()

        return frozenset(
            face["oracle_id"]
            for face in faces
            if isinstance(face, dict)
            and isinstance(face.get("oracle_id"), str)
        )

    oracle_id = printing.get("oracle_id")

    if isinstance(oracle_id, str):
        return frozenset({oracle_id})

    return frozenset()


def find_art_only_oracle_ids(
    printings: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    """Find identities present exclusively in art-series printings.

    Classification uses the raw source universe, not the set of
    successfully normalized cards. This prevents normalization bugs
    from incorrectly classifying ordinary cards as art-only.
    """

    art_ids: set[str] = set()
    non_art_ids: set[str] = set()

    for printing in printings:
        identities = _source_oracle_ids(printing)

        if printing.get("layout") == "art_series":
            art_ids.update(identities)
        else:
            non_art_ids.update(identities)

    return frozenset(art_ids - non_art_ids)


def exclude_art_only_taggings(
    tags: Iterable[dict[str, Any]],
    art_only_ids: Collection[str],
    excluded_by_tag: Counter[str],
) -> Iterator[dict[str, Any]]:
    """Omit verified art-only assignments without hiding bad references.

    Preserve tag definitions, aliases, hierarchy and all other
    assignments. Never mutate source tag dictionaries.
    """

    for tag in tags:
        taggings = tag.get("taggings")

        if not isinstance(taggings, list):
            # Leave malformed data for the strict importer to reject.
            yield tag
            continue

        retained = []

        for tagging in taggings:
            oracle_id = (
                tagging.get("oracle_id")
                if isinstance(tagging, dict)
                else None
            )

            if (
                isinstance(oracle_id, str)
                and oracle_id in art_only_ids
            ):
                excluded_by_tag[str(tag.get("slug"))] += 1
            else:
                retained.append(tagging)

        yield {**tag, "taggings": retained}
