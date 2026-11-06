"""Choose one reproducible Oracle representative from normalized candidates.

The caller supplies candidates belonging to one Oracle identity. Full bulk
aggregation and durable staging are left to the import pipeline.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .models import Card, Legality
from .normalization import Candidate, NormalizationError


class RepresentativeConflictError(NormalizationError):
    """Reliable source candidates disagree on an Oracle-level fact."""


@dataclass(frozen=True, slots=True)
class SelectedRepresentative:
    card: Card
    legalities: tuple[Legality, ...]
    mechanics_printing_id: str
    legality_printing_id: str


def _rank(candidate: Candidate) -> tuple[bool, bool, int, str]:
    return (
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

    Mechanical priority: complete, nonreversible, English, newest, ID.
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

    expected = peers[0].legalities

    for peer in peers[1:]:
        if peer.legalities != expected:
            raise RepresentativeConflictError(
                f"{oracle_id}: legality disagreement between "
                f"equally eligible printings "
                f"{peers[0].printing_id} and {peer.printing_id}"
            )

    legality_source = min(peers, key=_rank)
    assert legality_source.legalities is not None

    return SelectedRepresentative(
        card=card,
        legalities=legality_source.legalities,
        mechanics_printing_id=mechanics.printing_id,
        legality_printing_id=legality_source.printing_id,
    )
