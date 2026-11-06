"""Immutable, storage-independent values exposed by MTG CardDB."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from math import isfinite

_COLORS = frozenset("WUBRG")
_MANA = _COLORS | {"C"}
_LEGALITY_STATUSES = frozenset({
    "legal", "restricted", "banned", "not_legal"
})


def _strings(values: Iterable[str], name: str) -> frozenset[str]:
    """Freeze a collection, rejecting strings as collections of characters."""
    if isinstance(values, (str, bytes, dict)):
        raise TypeError(
            f"{name} must be an iterable of strings, not a string/mapping"
        )
    try:
        result = frozenset(values)
    except TypeError as exc:
        raise TypeError(
            f"{name} must be an iterable of hashable strings"
        ) from exc
    if not all(isinstance(item, str) for item in result):
        raise TypeError(f"{name} must contain only strings")
    return result


def _color_set(
    values: Iterable[str] | None,
    name: str,
    *,
    allow_colorless_mana: bool = False,
) -> frozenset[str] | None:
    if values is None:
        return None
    result = _strings(values, name)
    allowed = _MANA if allow_colorless_mana else _COLORS
    if not result <= allowed:
        raise ValueError(
            f"{name} must contain canonical uppercase symbols "
            f"from {''.join(sorted(allowed))}"
        )
    return result


def _require_str(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")


def _optional_str(value: object, name: str) -> None:
    if value is not None:
        _require_str(value, name)


@dataclass(frozen=True, slots=True)
class CardFace:
    face_index: int
    name: str
    type_line: str

    mana_cost: str | None = None
    oracle_text: str | None = None

    colors: frozenset[str] | None = None
    color_indicator: frozenset[str] | None = None

    supertypes: frozenset[str] = field(default_factory=frozenset)
    types: frozenset[str] = field(default_factory=frozenset)
    subtypes: frozenset[str] = field(default_factory=frozenset)

    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    defense: str | None = None

    hand_modifier: str | None = None
    life_modifier: str | None = None

    def __post_init__(self) -> None:
        if type(self.face_index) is not int:
            raise TypeError("face_index must be an integer")
        if self.face_index < 0:
            raise ValueError("face_index must be nonnegative")

        for key in ("name", "type_line"):
            _require_str(getattr(self, key), key)

        for key in (
            "mana_cost",
            "oracle_text",
            "power",
            "toughness",
            "loyalty",
            "defense",
            "hand_modifier",
            "life_modifier",
        ):
            _optional_str(getattr(self, key), key)

        for key in ("colors", "color_indicator"):
            object.__setattr__(
                self,
                key,
                _color_set(getattr(self, key), key),
            )

        for key in ("supertypes", "types", "subtypes"):
            object.__setattr__(
                self,
                key,
                _strings(getattr(self, key), key),
            )


@dataclass(frozen=True, slots=True)
class Card:
    oracle_id: str
    name: str
    layout: str
    type_line: str
    mana_value: float

    colors: frozenset[str] | None
    color_identity: frozenset[str]
    produced_mana: frozenset[str] | None

    reserved: bool
    game_changer: bool | None
    keywords: frozenset[str]
    faces: tuple[CardFace, ...]

    def __post_init__(self) -> None:
        for key in ("oracle_id", "name", "layout", "type_line"):
            _require_str(getattr(self, key), key)

        if (
            isinstance(self.mana_value, bool)
            or not isinstance(self.mana_value, (int, float))
        ):
            raise TypeError("mana_value must be a finite number")

        if not isfinite(self.mana_value) or self.mana_value < 0:
            raise ValueError("mana_value must be finite and nonnegative")

        object.__setattr__(
            self, "mana_value", float(self.mana_value)
        )

        if type(self.reserved) is not bool:
            raise TypeError("reserved must be bool")

        if (
            self.game_changer is not None
            and type(self.game_changer) is not bool
        ):
            raise TypeError("game_changer must be bool or None")

        for key in ("colors", "color_identity", "produced_mana"):
            object.__setattr__(
                self,
                key,
                _color_set(
                    getattr(self, key),
                    key,
                    allow_colorless_mana=(key == "produced_mana"),
                ),
            )

        if self.color_identity is None:
            raise ValueError("color_identity cannot be None")

        object.__setattr__(
            self, "keywords", _strings(self.keywords, "keywords")
        )

        if isinstance(self.faces, (str, bytes)):
            raise TypeError(
                "faces must be a collection of CardFace objects"
            )
        try:
            faces = tuple(self.faces)
        except TypeError as exc:
            raise TypeError("faces must be iterable") from exc

        if not faces:
            raise ValueError("a card must have at least one face")

        if any(not isinstance(face, CardFace) for face in faces):
            raise TypeError(
                "faces must contain only CardFace objects"
            )

        if any(
            face.face_index != index
            for index, face in enumerate(faces)
        ):
            raise ValueError(
                "face indices must be contiguous and ordered from zero"
            )

        object.__setattr__(self, "faces", faces)

    @property
    def supertypes(self) -> frozenset[str]:
        return frozenset().union(
            *(face.supertypes for face in self.faces)
        )

    @property
    def types(self) -> frozenset[str]:
        return frozenset().union(
            *(face.types for face in self.faces)
        )

    @property
    def subtypes(self) -> frozenset[str]:
        return frozenset().union(
            *(face.subtypes for face in self.faces)
        )


@dataclass(frozen=True, slots=True)
class Legality:
    format: str
    status: str

    def __post_init__(self) -> None:
        _require_str(self.format, "format")
        _require_str(self.status, "status")
        if self.status not in _LEGALITY_STATUSES:
            raise ValueError(
                f"unsupported legality status: {self.status!r}"
            )


@dataclass(frozen=True, slots=True)
class SetRarity:
    set_code: str
    rarity: str
    game: str

    def __post_init__(self) -> None:
        for key in ("set_code", "rarity", "game"):
            _require_str(getattr(self, key), key)


@dataclass(frozen=True, slots=True)
class Tagging:
    tag_slug: str
    weight: str
    annotation: str | None = None

    def __post_init__(self) -> None:
        _require_str(self.tag_slug, "tag_slug")
        _require_str(self.weight, "weight")
        _optional_str(self.annotation, "annotation")


@dataclass(frozen=True, slots=True)
class Tag:
    slug: str
    label: str
    description: str | None = None
    aliases: frozenset[str] = field(default_factory=frozenset)
    parents: frozenset[str] = field(default_factory=frozenset)
    children: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for key in ("slug", "label"):
            _require_str(getattr(self, key), key)

        _optional_str(self.description, "description")

        for key in ("aliases", "parents", "children"):
            object.__setattr__(
                self,
                key,
                _strings(getattr(self, key), key),
            )


@dataclass(frozen=True, slots=True)
class SetInfo:
    code: str
    name: str
    set_type: str
    released_at: str | None
    digital: bool

    def __post_init__(self) -> None:
        for key in ("code", "name", "set_type"):
            _require_str(getattr(self, key), key)
        _optional_str(self.released_at, "released_at")
        if type(self.digital) is not bool:
            raise TypeError("digital must be bool")


@dataclass(frozen=True, slots=True)
class SourceInfo:
    source_type: str
    source_updated_at: str | None
    downloaded_at: str
    source_url: str
    sha256: str
    record_count: int

    def __post_init__(self) -> None:
        for key in (
            "source_type",
            "downloaded_at",
            "source_url",
            "sha256",
        ):
            _require_str(getattr(self, key), key)

        _optional_str(
            self.source_updated_at, "source_updated_at"
        )

        if type(self.record_count) is not int:
            raise TypeError("record_count must be an integer")
        if self.record_count < 0:
            raise ValueError("record_count must be nonnegative")


@dataclass(frozen=True, slots=True)
class DatabaseInfo:
    schema_version: int
    created_at: str
    builder_version: str
    sources: tuple[SourceInfo, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer")
        if self.schema_version < 0:
            raise ValueError("schema_version must be nonnegative")

        _require_str(self.created_at, "created_at")
        _require_str(self.builder_version, "builder_version")

        if isinstance(self.sources, (str, bytes)):
            raise TypeError(
                "sources must be a collection of SourceInfo objects"
            )

        try:
            sources = tuple(self.sources)
        except TypeError as exc:
            raise TypeError("sources must be iterable") from exc

        if any(
            not isinstance(source, SourceInfo)
            for source in sources
        ):
            raise TypeError(
                "sources must contain only SourceInfo objects"
            )

        object.__setattr__(
            self,
            "sources",
            tuple(sorted(
                sources,
                key=lambda source: source.source_type,
            )),
        )
