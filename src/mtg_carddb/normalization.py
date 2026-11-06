"""Normalize one Scryfall printing into Oracle-identity candidates.

No database writes, downloads, set history, or representative selection occur here.
Type vocabulary is supplied externally from import-time catalogs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from math import isfinite
import re
from typing import Any, Mapping

from .models import Card, CardFace, Legality

_COLORS = frozenset("WUBRG")
_MANA = _COLORS | {"C"}


class NormalizationError(ValueError):
    """A source printing cannot be represented without inventing facts."""


def _string(data: Mapping[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise NormalizationError(f"{context}: missing or invalid {key}")
    return value


def _optional_string(
    data: Mapping[str, Any], key: str, context: str
) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise NormalizationError(f"{context}: invalid {key}")
    return value


def _symbols(
    data: Mapping[str, Any],
    key: str,
    context: str,
    *,
    mana: bool = False,
) -> frozenset[str] | None:
    value = data.get(key)
    if value is None:
        return None

    allowed = _MANA if mana else _COLORS
    if (
        not isinstance(value, list)
        or any(
            not isinstance(x, str) or x not in allowed
            for x in value
        )
    ):
        raise NormalizationError(f"{context}: invalid {key} symbols")

    return frozenset(value)


def _boolean(
    data: Mapping[str, Any], key: str, context: str
) -> bool | None:
    value = data.get(key)
    if value is not None and type(value) is not bool:
        raise NormalizationError(f"{context}: invalid {key}")
    return value


def _keywords(
    data: Mapping[str, Any], context: str
) -> frozenset[str] | None:
    value = data.get("keywords")
    if value is None:
        return None

    if not isinstance(value, list) or any(
        not isinstance(x, str) for x in value
    ):
        raise NormalizationError(f"{context}: invalid keywords")

    return frozenset(value)


def _mana_value(data: Mapping[str, Any], context: str) -> float:
    value = data.get("cmc")

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NormalizationError(f"{context}: missing or invalid cmc")

    try:
        parsed = float(value)
    except OverflowError as exc:
        raise NormalizationError(f"{context}: invalid cmc") from exc

    if not isfinite(parsed) or parsed < 0:
        raise NormalizationError(f"{context}: missing or invalid cmc")

    return parsed


def _legality_map(
    data: Mapping[str, Any], context: str
) -> tuple[Legality, ...] | None:
    raw = data.get("legalities")

    if raw is None:
        return None

    if not isinstance(raw, dict):
        raise NormalizationError(f"{context}: invalid legalities")

    try:
        return tuple(
            sorted(
                (
                    Legality(fmt, status)
                    for fmt, status in raw.items()
                ),
                key=lambda entry: (
                    entry.format,
                    entry.status,
                ),
            )
        )
    except (TypeError, ValueError) as exc:
        raise NormalizationError(
            f"{context}: invalid legalities: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class TypeVocabulary:
    """Exact, externally supplied Magic type vocabulary.

    `subtypes` is the union of subtype catalogs, including multiword names.
    Unknown tokens and unparseable subtype sequences are rejected.
    """

    supertypes: frozenset[str]
    card_types: frozenset[str]
    subtypes: frozenset[str]

    def __post_init__(self) -> None:
        for name in ("supertypes", "card_types", "subtypes"):
            value = getattr(self, name)

            if isinstance(value, (str, bytes)):
                raise TypeError(
                    f"{name} must contain catalog strings"
                )

            frozen = frozenset(value)

            if any(
                not isinstance(item, str) or not item
                for item in frozen
            ):
                raise TypeError(
                    f"{name} must contain nonempty strings"
                )

            object.__setattr__(self, name, frozen)

    @lru_cache(maxsize=8192)
    def parse(
        self, type_line: str
    ) -> tuple[
        frozenset[str],
        frozenset[str],
        frozenset[str],
    ]:
        if not isinstance(type_line, str) or not type_line:
            raise NormalizationError("missing type line")

        halves = type_line.split(" — ")

        if len(halves) > 2 or "—" in halves[0]:
            raise NormalizationError(
                f"unsupported type line: {type_line!r}"
            )

        left = halves[0].split()

        supertypes = frozenset(
            word for word in left
            if word in self.supertypes
        )
        types = frozenset(
            word for word in left
            if word in self.card_types
        )

        unknown = [
            word for word in left
            if word not in self.supertypes | self.card_types
        ]

        if unknown or not types:
            raise NormalizationError(
                f"unrecognized type line: {type_line!r}"
            )

        if len(halves) == 1:
            return supertypes, types, frozenset()

        tokens = halves[1].split()

        if not tokens:
            raise NormalizationError(
                f"empty subtype list: {type_line!r}"
            )

        # Prefer the longest recognized subtype.
        # "Time Lord" stays one subtype rather than "Time" + "Lord".
        options = sorted(
            (
                (tuple(name.split()), name)
                for name in self.subtypes
            ),
            key=lambda pair: (-len(pair[0]), pair[1]),
        )

        position = 0
        found: set[str] = set()

        while position < len(tokens):
            for words, name in options:
                if (
                    tuple(tokens[position:position + len(words)])
                    == words
                ):
                    found.add(name)
                    position += len(words)
                    break
            else:
                raise NormalizationError(
                    f"unrecognized subtype in {type_line!r}"
                )

        return supertypes, types, frozenset(found)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A source-backed, possibly incomplete Oracle mechanics projection."""

    oracle_id: str
    printing_id: str
    lang: str
    released_at: str
    reversible: bool
    border_color: str | None
    set_type: str | None

    name: str
    layout: str
    type_line: str
    mana_value: float

    colors: frozenset[str] | None
    color_identity: frozenset[str] | None
    produced_mana: frozenset[str] | None

    reserved: bool | None
    game_changer: bool | None
    keywords: frozenset[str] | None

    faces: tuple[CardFace, ...]
    legalities: tuple[Legality, ...] | None

    @property
    def missing(self) -> frozenset[str]:
        return frozenset(
            field
            for field in (
                "color_identity",
                "keywords",
                "reserved",
            )
            if getattr(self, field) is None
        )

    def to_card(self) -> Card:
        if self.missing:
            raise NormalizationError(
                f"{self.oracle_id}: {self.printing_id} "
                f"missing required mechanics: "
                f'{", ".join(sorted(self.missing))}'
            )

        assert self.color_identity is not None
        assert self.keywords is not None
        assert self.reserved is not None

        return Card(
            oracle_id=self.oracle_id,
            name=self.name,
            layout=self.layout,
            type_line=self.type_line,
            mana_value=self.mana_value,
            colors=self.colors,
            color_identity=self.color_identity,
            produced_mana=self.produced_mana,
            reserved=self.reserved,
            game_changer=self.game_changer,
            keywords=self.keywords,
            faces=self.faces,
        )


def _face(
    data: Mapping[str, Any],
    index: int,
    vocab: TypeVocabulary,
    context: str,
) -> CardFace:
    name = _string(data, "name", context)
    type_line = _string(data, "type_line", context)
    supertypes, types, subtypes = vocab.parse(type_line)

    return CardFace(
        face_index=index,
        name=name,
        type_line=type_line,
        mana_cost=_optional_string(data, "mana_cost", context),
        oracle_text=_optional_string(data, "oracle_text", context),
        colors=_symbols(data, "colors", context),
        color_indicator=_symbols(
            data, "color_indicator", context
        ),
        supertypes=supertypes,
        types=types,
        subtypes=subtypes,
        power=_optional_string(data, "power", context),
        toughness=_optional_string(data, "toughness", context),
        loyalty=_optional_string(data, "loyalty", context),
        defense=_optional_string(data, "defense", context),
        hand_modifier=_optional_string(
            data, "hand_modifier", context
        ),
        life_modifier=_optional_string(
            data, "life_modifier", context
        ),
    )


def _invalid_face(printing_id: str, index: int) -> CardFace:
    raise NormalizationError(
        f"{printing_id}: invalid face {index}"
    )


def normalize_printing(
    printing: Mapping[str, Any],
    vocabulary: TypeVocabulary,
) -> tuple[Candidate, ...]:
    """Produce one candidate normally, or one per independent reversible face.

    Reversible root-level identity, keywords, reserved, color identity,
    and legality are deliberately never distributed to the two identities.
    """
    if not isinstance(printing, dict) or printing.get("object") != "card":
        raise NormalizationError(
            "expected a Scryfall Card object"
        )

    printing_id = _string(printing, "id", "printing")
    lang = _string(printing, "lang", printing_id)
    released_at = _string(
        printing, "released_at", printing_id
    )

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", released_at):
        raise NormalizationError(
            f"{printing_id}: invalid released_at"
        )

    try:
        date.fromisoformat(released_at)
    except ValueError as exc:
        raise NormalizationError(
            f"{printing_id}: invalid released_at"
        ) from exc

    layout = _string(printing, "layout", printing_id)
    border_color = _optional_string(
        printing, "border_color", printing_id
    )
    set_type = _optional_string(
        printing, "set_type", printing_id
    )

    raw_faces = printing.get("card_faces")

    if layout == "reversible_card":
        if "oracle_id" in printing:
            raise NormalizationError(
                f"{printing_id}: unexpected reversible root oracle_id"
            )

        if not isinstance(raw_faces, list) or len(raw_faces) != 2:
            raise NormalizationError(
                f"{printing_id}: reversible needs two faces"
            )

        result: list[Candidate] = []
        seen: set[str] = set()

        for face_data in raw_faces:
            if not isinstance(face_data, dict):
                raise NormalizationError(
                    f"{printing_id}: invalid reversible face"
                )

            oracle_id = _string(
                face_data, "oracle_id", printing_id
            )

            if oracle_id in seen:
                raise NormalizationError(
                    f"{printing_id}: duplicate reversible oracle_id"
                )

            seen.add(oracle_id)
            context = f"{printing_id}/{oracle_id}"
            face = _face(
                face_data, 0, vocabulary, context
            )

            result.append(
                Candidate(
                    oracle_id=oracle_id,
                    printing_id=printing_id,
                    lang=lang,
                    released_at=released_at,
                    reversible=True,
                    border_color=border_color,
                    set_type=set_type,
                    name=face.name,
                    layout=_string(
                        face_data, "layout", context
                    ),
                    type_line=face.type_line,
                    mana_value=_mana_value(
                        face_data, context
                    ),
                    colors=face.colors,
                    color_identity=_symbols(
                        face_data, "color_identity", context
                    ),
                    produced_mana=_symbols(
                        face_data,
                        "produced_mana",
                        context,
                        mana=True,
                    ),
                    reserved=_boolean(
                        face_data, "reserved", context
                    ),
                    game_changer=_boolean(
                        face_data, "game_changer", context
                    ),
                    keywords=_keywords(face_data, context),
                    faces=(face,),
                    legalities=_legality_map(
                        face_data, context
                    ),
                )
            )

        return tuple(result)

    oracle_id = _string(
        printing, "oracle_id", printing_id
    )

    if raw_faces is None:
        faces = (
            _face(printing, 0, vocabulary, printing_id),
        )
    else:
        if not isinstance(raw_faces, list) or not raw_faces:
            raise NormalizationError(
                f"{printing_id}: invalid card_faces"
            )

        faces = tuple(
            _face(
                value,
                index,
                vocabulary,
                f"{printing_id}/face{index}",
            )
            if isinstance(value, dict)
            else _invalid_face(printing_id, index)
            for index, value in enumerate(raw_faces)
        )

    return (
        Candidate(
            oracle_id=oracle_id,
            printing_id=printing_id,
            lang=lang,
            released_at=released_at,
            reversible=False,
            border_color=border_color,
            set_type=set_type,
            name=_string(printing, "name", printing_id),
            layout=layout,
            type_line=_string(
                printing, "type_line", printing_id
            ),
            mana_value=_mana_value(
                printing, printing_id
            ),
            colors=_symbols(
                printing, "colors", printing_id
            ),
            color_identity=_symbols(
                printing, "color_identity", printing_id
            ),
            produced_mana=_symbols(
                printing,
                "produced_mana",
                printing_id,
                mana=True,
            ),
            reserved=_boolean(
                printing, "reserved", printing_id
            ),
            game_changer=_boolean(
                printing, "game_changer", printing_id
            ),
            keywords=_keywords(
                printing, printing_id
            ),
            faces=faces,
            legalities=_legality_map(
                printing, printing_id
            ),
        ),
    )
