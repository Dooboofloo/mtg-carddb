"""Choose one reproducible Oracle representative from normalized candidates.

The caller supplies candidates belonging to one Oracle identity. Full bulk
aggregation and durable staging are left to the import pipeline.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .models import Card, CardImages, Legality
from .normalization import Candidate, NormalizationError


class RepresentativeConflictError(NormalizationError):
    """Reliable source candidates disagree on an Oracle-level fact."""


@dataclass(frozen=True, slots=True)
class SelectedRepresentative:
    card: Card
    legalities: tuple[Legality, ...]
    mechanics_printing_id: str
    legality_printing_id: str
    aggregated_formats: frozenset[str] = frozenset()
    images: CardImages = CardImages()
    image_printing_id: str | None = None


def _rank(candidate: Candidate) -> tuple[bool, bool, bool, int, str]:
    return (
        bool(candidate.type_issues),
        candidate.reversible,
        candidate.lang != "en",
        -date.fromisoformat(candidate.released_at).toordinal(),
        candidate.printing_id,
    )


_CORE_FIELDS = (
    "name",
    "type_line",
    "mana_value",
    "colors",
    "color_identity",
    "produced_mana",
    "reserved",
    "game_changer",
    "keywords",
)

_FACE_FIELDS = (
    "type_line",
    "supertypes",
    "types",
    "mana_cost",
    "oracle_text",
    "colors",
    "color_indicator",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "hand_modifier",
    "life_modifier",
)

def _image_rank(candidate: Candidate) -> tuple[object, ...]:
    """Prefer normal English paper cards over special treatments, then newest.

    This approximates Scryfall's preferred display printing; their exact
    selection policy is not published in the bulk records.
    """
    special_set = candidate.image_set_code in {"sld", "plst"}
    special_category = candidate.set_type in {
        "box", "promo", "memorabilia", "masterpiece", "token",
        "minigame", "from_the_vault", "premium_deck", "spellbook",
    }
    alternate_treatment = (
        candidate.image_full_art
        or any(effect in {"showcase", "extendedart", "inverted", "borderless"}
               for effect in candidate.image_frame_effects)
        or candidate.border_color == "borderless"
    )
    return (
        candidate.lang != "en",
        bool(candidate.image_games) and "paper" not in candidate.image_games,
        special_set or candidate.image_promo or special_category,
        alternate_treatment,
        candidate.reversible,
        -date.fromisoformat(candidate.released_at).toordinal(),
        candidate.printing_id,
    )


_OLDSCHOOL_PRIORITY = {
    "not_legal": 0,
    "legal": 1,
    "restricted": 2,
    "banned": 3,
}

def _validate_consistency(
    group: tuple[Candidate, ...],
) -> None:
    # Linear in the number of candidates; basic lands can have
    # many printings.
    seen_printings: dict[str, Candidate] = {}
    core: dict[str, tuple[object, str]] = {}

    faces: dict[
        tuple[str, ...],
        dict[tuple[int, str], tuple[object, str]],
    ] = {}

    oracle_id = group[0].oracle_id

    for candidate in group:
        previous = seen_printings.setdefault(
            candidate.printing_id,
            candidate,
        )

        if previous != candidate:
            raise RepresentativeConflictError(
                f"{oracle_id}: duplicate printing ID with "
                f"different data: {candidate.printing_id}"
            )

        for field in _CORE_FIELDS:
            if field == "type_line" and candidate.type_issues:
                continue
            value = getattr(candidate, field)

            if value is None:
                continue

            if field in core and core[field][0] != value:
                raise RepresentativeConflictError(
                    f"{oracle_id}: conflicting {field} in printings "
                    f"{core[field][1]} and {candidate.printing_id}"
                )

            core[field] = (value, candidate.printing_id)

        # Compare faces only when their arrangements correspond.
        # Different physical layouts are not automatically conflicts.
        arrangement = tuple(
            face.name for face in candidate.faces
        )
        facts = faces.setdefault(arrangement, {})

        for face in candidate.faces:
            for field in _FACE_FIELDS:
                if field == "type_line" and candidate.type_issues:
                    continue
                value = getattr(face, field)

                if value is None:
                    continue

                key = (face.face_index, field)

                if key in facts and facts[key][0] != value:
                    raise RepresentativeConflictError(
                        f"{oracle_id}: conflicting face "
                        f"{face.name!r} {field} in "
                        f"{facts[key][1]} and "
                        f"{candidate.printing_id}"
                    )

                facts[key] = (value, candidate.printing_id)


def select_representative(
    candidates: Iterable[Candidate],
) -> SelectedRepresentative:
    """Select independent mechanical and legality projections for one ID.

    Mechanical priority: complete, catalog-verified, nonreversible, English, newest, ID.
    Legality priority: non-gold/non-memorabilia, English, newest, ID.

    Equally eligible legality conflicts and mechanical disagreements fail.
    """
    group = tuple(candidates)

    if not group:
        raise NormalizationError(
            "cannot select representative from empty group"
        )

    oracle_id = group[0].oracle_id

    if any(
        candidate.oracle_id != oracle_id
        for candidate in group
    ):
        raise NormalizationError(
            "candidate group contains multiple Oracle IDs"
        )

    complete = [
        candidate
        for candidate in group
        if not candidate.missing
    ]

    if not complete:
        details = "; ".join(
            f'{candidate.printing_id} missing '
            f'{", ".join(sorted(candidate.missing))}'
            for candidate in sorted(group, key=_rank)
        )

        raise NormalizationError(
            f"{oracle_id}: no complete mechanics candidate: {details}"
        )

    # Partial candidates may still contain valid source-backed
    # Oracle characteristics, so they participate in consistency checks.
    _validate_consistency(group)

    mechanics = min(complete, key=_rank)
    card = mechanics.to_card()

    with_legalities = [
        candidate
        for candidate in group
        if candidate.legalities is not None
    ]

    if not with_legalities:
        raise NormalizationError(
            f"{oracle_id}: no source-backed legalities"
        )

    def ineligible(candidate: Candidate) -> bool:
        return (
            candidate.border_color == "gold"
            or candidate.set_type == "memorabilia"
        )

    best_eligibility = min(
        ineligible(candidate)
        for candidate in with_legalities
    )

    peers = [
        candidate
        for candidate in with_legalities
        if ineligible(candidate) == best_eligibility
    ]

    expected_other = tuple(
        legality
        for legality in peers[0].legalities
        if legality.format != "oldschool"
    )

    oldschool_states: set[str] = set()

    for peer in peers:
        assert peer.legalities is not None

        actual_other = tuple(
            legality
            for legality in peer.legalities
            if legality.format != "oldschool"
        )

        if actual_other != expected_other:
            raise RepresentativeConflictError(
                f"{oracle_id}: legality disagreement between "
                f"equally eligible printings "
                f"{peers[0].printing_id} and {peer.printing_id}"
            )

        for legality in peer.legalities:
            if legality.format == "oldschool":
                oldschool_states.add(legality.status)

    legality_source = min(peers, key=_rank)
    assert legality_source.legalities is not None

    legalities = list(expected_other)

    if oldschool_states:
        # An ineligible printing does not override an eligible one.
        # Among eligible printings, retain the strongest restriction.
        status = max(
            oldschool_states,
            key=_OLDSCHOOL_PRIORITY.__getitem__,
        )
        legalities.append(Legality("oldschool", status))

    legalities.sort(key=lambda entry: (entry.format, entry.status))

    # Images are printing-specific, so choose a complete image-bearing
    # printing independently from the Oracle mechanics/legality projections.
    # Prefer ordinary artwork, independently of mechanical/legality selection.
    image_candidates = [item for item in group if item.image_front is not None]
    image_source = min(image_candidates, key=_image_rank) if image_candidates else None

    return SelectedRepresentative(
        card=card,
        legalities=tuple(legalities),
        mechanics_printing_id=mechanics.printing_id,
        legality_printing_id=legality_source.printing_id,
        images=(
            CardImages(image_source.image_front, image_source.image_back)
            if image_source is not None else CardImages()
        ),
        image_printing_id=(
            image_source.printing_id if image_source is not None else None
        ),
        aggregated_formats=(
            frozenset({"oldschool"})
            if len(oldschool_states) > 1
            else frozenset()
        ),
    )
