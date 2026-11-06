"""MTG CardDB public API."""

from .database import (
    CardDatabase,
    UnknownFormatError,
    UnknownSetError,
    UnknownTagError,
)
from .models import (
    Card,
    CardFace,
    DatabaseInfo,
    Legality,
    SetInfo,
    SetRarity,
    SourceInfo,
    Tag,
    Tagging,
)
from .storage import (
    AmbiguousTagError,
    DatabaseClosedError,
    DatabaseCompatibilityError,
    DatabaseOpenError,
)

__all__ = [
    "CardDatabase",
    "Card",
    "CardFace",
    "DatabaseInfo",
    "Legality",
    "SetInfo",
    "SetRarity",
    "SourceInfo",
    "Tag",
    "Tagging",
    "UnknownTagError",
    "UnknownFormatError",
    "UnknownSetError",
    "AmbiguousTagError",
    "DatabaseClosedError",
    "DatabaseCompatibilityError",
    "DatabaseOpenError",
]
