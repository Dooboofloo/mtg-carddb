"""Public MTG CardDB library API.

Opening a CardDatabase never downloads or updates card data.
Database creation and updates are explicit lifecycle operations.
"""

from .acquisition import BulkAcquisitionError
from .activation import SnapshotActivationError
from .builder import SnapshotBuildError, SnapshotBuildResult
from .database import (
    CardDatabase,
    UnknownFormatError,
    UnknownSetError,
    UnknownTagError,
    default_database_path,
)
from .freshness import (
    DEFAULT_STALE_AFTER_DAYS,
    DatabaseFreshness,
    DatabaseNotInitializedError,
    database_freshness,
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
from .reference_sources import ReferenceArchiveError
from .storage import (
    AmbiguousTagError,
    DatabaseClosedError,
    DatabaseCompatibilityError,
    DatabaseOpenError,
)
from .workflow import (
    DatabaseWorkflowError,
    DatabaseWorkflowResult,
    ProgressCallback,
    WorkflowProgress,
    initialize_database,
    update_database,
)

__all__ = [
    "CardDatabase",
    "default_database_path",
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
    "DatabaseNotInitializedError",
    "DatabaseFreshness",
    "DEFAULT_STALE_AFTER_DAYS",
    "database_freshness",
    "initialize_database",
    "update_database",
    "DatabaseWorkflowResult",
    "WorkflowProgress",
    "ProgressCallback",
    "DatabaseWorkflowError",
    "SnapshotBuildResult",
    "SnapshotBuildError",
    "BulkAcquisitionError",
    "SnapshotActivationError",
    "ReferenceArchiveError",
]
