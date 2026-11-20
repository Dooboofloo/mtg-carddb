"""Normalize one Scryfall printing into Oracle-identity candidates.

No database writes, downloads, set history, or representative selection occur here.
Type vocabulary is supplied externally from import-time catalogs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from math import isfinite
import re
from typing import Any, Mapping

from .printed_types import parse_line_alias
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


def _produced_mana(
    data: Mapping[str, Any],
    context: str,
) -> frozenset[str] | None:
    """Return WUBRGC production, or unknown for unrepresentable resources."""

    # Sole Performer produces {T}, which is not WUBRGC mana.
    # None means the six-symbol projection is unavailable.
    if data.get("produced_mana") == ["T"]:
        return None

    return _symbols(
        data, "produced_mana", context, mana=True
    )

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
class TypeInterpretation:
    """Parsed attributes and recoveries; original type_line is unchanged."""

    supertypes: frozenset[str]
    types: frozenset[str]
    subtypes: frozenset[str]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TypeVocabulary:
    """Catalog-based type parser with conservative structural recovery.

    An absent separator is inferred only if a unique catalog-backed split
    exists. Explicitly separated, unknown subtypes are retained as printed,
    not silently autocorrected; unexpected card types remain errors.
    """

    supertypes: frozenset[str]
    card_types: frozenset[str]
    subtypes: frozenset[str]

    def __post_init__(self) -> None:
        for name in ("supertypes", "card_types", "subtypes"):
            value = getattr(self, name)
            if isinstance(value, (str, bytes)):
                raise TypeError(f"{name} must contain catalog strings")
            frozen = frozenset(value)
            if any(not isinstance(item, str) or not item for item in frozen):
                raise TypeError(f"{name} must contain nonempty strings")
            object.__setattr__(self, name, frozen)

    def _known_subtypes(self, tokens: list[str]) -> tuple[str, ...] | None:
        """Deterministic complete segmentation; None if not catalog-backed."""
        options: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for name in self.subtypes:
            words = tuple(name.split())
            if words:
                options.setdefault(words[0], []).append((words, name))
        for entries in options.values():
            entries.sort(key=lambda item: (-len(item[0]), item[1]))
        n = len(tokens)
        choices: list[tuple[str, int] | None] = [None] * (n + 1)
        choices[n] = ("", n)
        for pos in range(n - 1, -1, -1):
            for words, name in options.get(tokens[pos], ()):
                nxt = pos + len(words)
                if nxt <= n and tokens[pos:nxt] == list(words) and choices[nxt]:
                    choices[pos] = (name, nxt)
                    break
        if choices[0] is None:
            return None
        found: list[str] = []
        pos = 0
        while pos < n:
            choice = choices[pos]
            assert choice is not None
            name, pos = choice
            found.append(name)
        return tuple(found)

    def _subtypes_with_unknowns(self, tokens: list[str]) -> tuple[str, ...]:
        """Retain contiguous unknown tokens as literal labels, without correcting spelling."""
        # Prefer catalog-backed segmentation when possible, and otherwise
        # retain unfamiliar words. Never guess a corrected spelling.
        found: list[str] = []
        unknown: list[str] = []
        pos = 0
        while pos < len(tokens):
            choices = [
                (len(words), name)
                for name in self.subtypes
                if (words := name.split())
                and tokens[pos:pos + len(words)] == words
            ]
            if not choices:
                unknown.append(tokens[pos])
                pos += 1
                continue
            if unknown:
                found.append(" ".join(unknown))
                unknown.clear()
            length, name = max(choices, key=lambda item: (item[0], item[1]))
            found.append(name)
            pos += length
        if unknown:
            found.append(" ".join(unknown))
        return tuple(found)

    @lru_cache(maxsize=8192)
    def interpret(self, type_line: str, *, strict: bool = False) -> TypeInterpretation:
        if not isinstance(type_line, str) or not type_line:
            raise NormalizationError("missing type line")

        line = parse_line_alias(type_line)
        parts = re.split(r"\s+(?:—|–|--|-)\s+", line)
        if len(parts) > 2 or "—" in parts[0]:
            raise NormalizationError(f"unsupported type line: {type_line!r}")

        valid_left = self.supertypes | self.card_types
        words = parts[0].split()
        issues: list[str] = []
        if len(parts) == 2 and any(sep in type_line for sep in (" -- ", " – ", " - ")):
            issues.append("alternate_separator")

        # Only a unique split where *all* following subtypes are known can
        # recover a missing delimiter. Unknown suffixes are too ambiguous.
        if len(parts) == 1 and (
            any(word not in valid_left for word in words)
            or not any(word in self.card_types for word in words)
        ):
            options = []
            for pos in range(1, len(words)):
                left = words[:pos]
                right = words[pos:]
                if (
                    all(word in valid_left for word in left)
                    and any(word in self.card_types for word in left)
                    and self._known_subtypes(right) is not None
                ):
                    options.append(pos)
            if len(options) != 1 or strict:
                raise NormalizationError(f"unrecognized type line: {type_line!r}")
            words, right = words[:options[0]], words[options[0]:]
            parts = [" ".join(words), " ".join(right)]
            issues.append("inferred_separator")

        if (not words or any(word not in valid_left for word in words)
                or not any(word in self.card_types for word in words)):
            raise NormalizationError(f"unrecognized type line: {type_line!r}")
        super_names = frozenset(word for word in words if word in self.supertypes)
        type_names = frozenset(word for word in words if word in self.card_types)
        if len(parts) == 1:
            return TypeInterpretation(super_names, type_names, frozenset(), tuple(issues))

        tokens = parts[1].split()
        if not tokens:
            raise NormalizationError(f"empty subtype list: {type_line!r}")
        if type_names == frozenset({"Plane"}):
            return TypeInterpretation(
                super_names, type_names, frozenset({parts[1].strip()}), tuple(issues)
            )

        known = self._known_subtypes(tokens)
        if known is None:
            if strict:
                raise NormalizationError(f"unrecognized subtype in {type_line!r}")
            known = self._subtypes_with_unknowns(tokens)
            issues.append("unknown_subtype")
        return TypeInterpretation(super_names, type_names, frozenset(known), tuple(issues))

    def parse(self, type_line: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        result = self.interpret(type_line)
        return result.supertypes, result.types, result.subtypes


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
    image_front: str | None = None
    image_back: str | None = None
    # Source-printing metadata does not define Oracle mechanics.
    image_set_code: str | None = None
    image_promo: bool = False
    image_full_art: bool = False
    image_frame_effects: tuple[str, ...] = ()
    image_games: tuple[str, ...] = ()
    type_issues: tuple[str, ...] = ()

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
    *,
    allow_missing_type_line: bool = False,
    issue_codes: list[str] | None = None,
) -> CardFace:
    name = _string(data, "name", context)

    if allow_missing_type_line and "type_line" not in data:
        # The source does not print a type line on this continuation face.
        type_line = ""
        supertypes = types = subtypes = frozenset()
    else:
        type_line = _string(data, "type_line", context)
        parsed = vocab.interpret(type_line)
        supertypes, types, subtypes = (
            parsed.supertypes, parsed.types, parsed.subtypes
        )
        if issue_codes is not None:
            issue_codes.extend(parsed.issues)

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


def _normal_image(data: object) -> str | None:
    """Read only the normal image URI, without requiring imagery to exist."""
    if not isinstance(data, Mapping):
        return None
    image_uris = data.get("image_uris")
    if not isinstance(image_uris, Mapping):
        return None
    url = image_uris.get("normal")
    return url if isinstance(url, str) and url.startswith("https://") else None


def _printing_images(printing: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Distinguish physical card sides from merely multiple logical faces.

    Normal single-sided and split/adventure cards have their imagery on the
    root object. Double-faced cards expose images on their card_faces.
    """
    root = _normal_image(printing)
    if root is not None:
        return root, None
    faces = printing.get("card_faces")
    if not isinstance(faces, list):
        return None, None
    images = tuple(_normal_image(face) for face in faces)
    front = images[0] if images else None
    back = images[1] if len(images) == 2 and front is not None else None
    return front, back


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

    # Image printing preference is independent of Oracle mechanics.
    raw_effects = printing.get("frame_effects")
    frame_effects = (
        tuple(x for x in raw_effects if isinstance(x, str))
        if isinstance(raw_effects, list) else ()
    )
    raw_games = printing.get("games")
    image_games = (
        tuple(x for x in raw_games if isinstance(x, str))
        if isinstance(raw_games, list) else ()
    )
    image_metadata = dict(
        image_set_code=printing.get("set") if isinstance(printing.get("set"), str) else None,
        image_promo=printing.get("promo") is True,
        image_full_art=printing.get("full_art") is True,
        image_frame_effects=frame_effects,
        image_games=image_games,
    )

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

        for side_index, face_data in enumerate(raw_faces):
            if not isinstance(face_data, dict):
                raise NormalizationError(
                    f"{printing_id}: invalid reversible face"
                )

            oracle_id = _string(
                face_data, "oracle_id", printing_id
            )

            context = f"{printing_id}/{oracle_id}"
            issues: list[str] = []
            face = _face(
                face_data, 0, vocabulary, context, issue_codes=issues
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
                    produced_mana=_produced_mana(
                        face_data, context
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
                    image_front=_normal_image(face_data),
                    image_back=_normal_image(raw_faces[1 - side_index]),
                    type_issues=tuple(sorted(set(issues))),
                    **image_metadata,
                )
            )

        unique: dict[str, Candidate] = {}

        for candidate in result:
            previous = unique.get(candidate.oracle_id)

            if previous is not None and (
                replace(previous, image_front=None, image_back=None)
                != replace(candidate, image_front=None, image_back=None)
            ):
                # A reversible printing can put the same Oracle card on
                # both sides, each with a different image. Swapped image
                # URLs are not a difference in mechanical card data.
                # Some reversible printings of Adventures present the
                # creature and Adventure faces separately, but both faces
                # reference the SAME Oracle identity.
                if (
                    previous.layout == candidate.layout == "adventure"
                    and previous.name != candidate.name
                ):
                    if previous.mana_value != candidate.mana_value:
                        raise NormalizationError(
                            f"{printing_id}: shared-ID adventure faces "
                            "have different mana values"
                        )

                    front = previous.faces[0]
                    back = replace(
                        candidate.faces[0],
                        face_index=1,
                    )

                    # These reversible printings duplicate the creature's
                    # Oracle text on the Adventure face. That value cannot
                    # reliably describe the Adventure's actual rules text.
                    # Preserve absence rather than store a false fact.
                    if back.oracle_text == front.oracle_text:
                        back = replace(back, oracle_text=None)

                    unique[candidate.oracle_id] = replace(
                        previous,
                        name=f"{previous.name} // {candidate.name}",
                        type_line=(
                            f"{previous.type_line} // {candidate.type_line}"
                        ),
                        layout="adventure",
                        colors=_symbols(
                            printing, "colors", printing_id
                        ),
                        color_identity=_symbols(
                            printing, "color_identity", printing_id
                        ),
                        produced_mana=_produced_mana(
                            printing, printing_id
                        ),
                        keywords=_keywords(printing, printing_id),
                        reserved=_boolean(
                            printing, "reserved", printing_id
                        ),
                        game_changer=_boolean(
                            printing, "game_changer", printing_id
                        ),
                        legalities=_legality_map(
                            printing, printing_id
                        ),
                        faces=(front, back),
                        image_back=candidate.image_front,
                        type_issues=tuple(sorted(set(previous.type_issues + candidate.type_issues))),
                    )
                    continue

                raise NormalizationError(
                    f"{printing_id}: reversible faces share Oracle ID "
                    f"{candidate.oracle_id!r} but disagree mechanically"
                )

            # For identical Oracle mechanics, keep the first physical side
            # so front/back image URLs stay in the printing's orientation.
            unique.setdefault(candidate.oracle_id, candidate)

        return tuple(unique.values())


    oracle_id = _string(
        printing, "oracle_id", printing_id
    )

    issues: list[str] = []
    if raw_faces is None:
        faces = (
            _face(printing, 0, vocabulary, printing_id, issue_codes=issues),
        )
    else:
        if not isinstance(raw_faces, list) or not raw_faces:
            raise NormalizationError(
                f"{printing_id}: invalid card_faces"
            )
  
        faces_list: list[CardFace] = []

        for index, value in enumerate(raw_faces):
            if not isinstance(value, dict):
                _invalid_face(printing_id, index)

            is_continuation = (
                layout == "double_faced_token"
                and len(raw_faces) == 2
                and index == 1
                and printing.get("type_line") == "Card"
                and isinstance(raw_faces[0], dict)
                and raw_faces[0].get("type_line") == "Card"
                and isinstance(value.get("name"), str)
                and value["name"].casefold().endswith("(cont'd)")
            )

            faces_list.append(
                _face(
                    value,
                    index,
                    vocabulary,
                    f"{printing_id}/face{index}",
                    allow_missing_type_line=is_continuation,
                    issue_codes=issues,
                )
            )

        faces = tuple(faces_list)

    image_front, image_back = _printing_images(printing)
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
            produced_mana=_produced_mana(
                printing, printing_id
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
            image_front=image_front,
            image_back=image_back,
            type_issues=tuple(sorted(set(issues))),
            **image_metadata,
        ),
    )
