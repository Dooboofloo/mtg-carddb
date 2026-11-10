"""Download verified Scryfall bulk sources into a new directory.

Only the current gzip-compressed JSONL bulk format is supported.
No existing directory or source file is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .network import open_verified_https
from .provenance import BulkSource
from .jsonl_limits import iter_bounded_gzip_lines


class BulkAcquisitionError(RuntimeError):
    """A Scryfall bulk source could not be acquired or validated."""


@dataclass(frozen=True, slots=True)
class AcquiredBulkSources:
    cards_path: Path
    tags_path: Path
    bulk_sources: tuple[BulkSource, BulkSource]


_BULK_TYPES = (
    ("default_cards", "default-cards.jsonl.gz", "card"),
    ("oracle_tags", "oracle-tags.jsonl.gz", "tag"),
)

_API_ROOT = "https://api.scryfall.com/bulk-data"

_DEFAULT_USER_AGENT = (
    "mtg-carddb/0.1 (+https://github.com/Dooboofloo/mtg-carddb)"
)

_TIMEOUT_SECONDS = 60
_CHUNK_BYTES = 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_BULK_BYTES = 1024 * 1024 * 1024  # 1 GiB hard ceiling


def _https_url(value: object) -> str:
    if not isinstance(value, str):
        raise BulkAcquisitionError("missing HTTPS download URL")

    parsed = urlsplit(value)

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BulkAcquisitionError(
            f"expected an absolute HTTPS download URL: {value!r}"
        )

    return value


def _metadata(kind: str, user_agent: str) -> dict:
    request = Request(
        f"{_API_ROOT}/{kind}",
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
        },
    )

    with open_verified_https(
        request,
        opener=urlopen,
        timeout=_TIMEOUT_SECONDS,
        error_type=BulkAcquisitionError,
    ) as response:
        content = response.read(_MAX_METADATA_BYTES + 1)

    if len(content) > _MAX_METADATA_BYTES:
        raise BulkAcquisitionError(
            f"{kind}: metadata response too large"
        )

    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BulkAcquisitionError(
            f"{kind}: invalid bulk metadata JSON"
        ) from exc

    if (
        not isinstance(data, dict)
        or data.get("object") != "bulk_data"
        or data.get("type") != kind
    ):
        raise BulkAcquisitionError(
            f"{kind}: unexpected bulk metadata"
        )

    if "jsonl_download_uri" not in data:
        raise BulkAcquisitionError(
            f"{kind}: jsonl_download_uri unavailable; "
            "legacy JSON-array downloads are not supported"
        )

    _https_url(data["jsonl_download_uri"])

    if not isinstance(data.get("updated_at"), str):
        raise BulkAcquisitionError(
            f"{kind}: missing updated_at"
        )

    return data


def _validate_jsonl_gzip(
    path: Path,
    kind: str,
    expected_object: str,
) -> int:
    """Check bounded gzip JSONL, CRC, objects and record count."""

    count = 0

    try:
        for count, line in iter_bounded_gzip_lines(
            path
        ):
            if not line.strip():
                raise BulkAcquisitionError(
                    f"{kind}: blank JSONL record "
                    f"at line {count}"
                )

            entry = json.loads(line)

            if (
                not isinstance(entry, dict)
                or entry.get("object") != expected_object
            ):
                raise BulkAcquisitionError(
                    f"{kind}: unexpected object "
                    f"at line {count}"
                )

    except (
        OSError,
        EOFError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise BulkAcquisitionError(
            f"{kind}: invalid gzip JSONL: {exc}"
        ) from exc

    if count == 0:
        raise BulkAcquisitionError(
            f"{kind}: empty bulk export"
        )

    return count


def _download(
    metadata: dict,
    kind: str,
    path: Path,
    expected_object: str,
    user_agent: str,
) -> BulkSource:
    url = _https_url(
        metadata["jsonl_download_uri"]
    )

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/gzip,*/*",
            "Accept-Encoding": "identity",
        },
    )

    expected_size = metadata.get("compressed_size")

    if expected_size is not None:
        if type(expected_size) is not int or expected_size < 0:
            raise BulkAcquisitionError(
                f"{kind}: invalid compressed_size metadata"
            )

        if expected_size > _MAX_BULK_BYTES:
            raise BulkAcquisitionError(
                f"{kind}: advertised compressed size exceeds "
                "the 1 GiB download limit"
            )

    digest = hashlib.sha256()
    size = 0

    with open_verified_https(
        request,
        opener=urlopen,
        timeout=_TIMEOUT_SECONDS,
        error_type=BulkAcquisitionError,
    ) as response:
        final_url = response.geturl()

        with path.open("xb") as output:
            while chunk := response.read(_CHUNK_BYTES):
                size += len(chunk)

                if size > _MAX_BULK_BYTES:
                    raise BulkAcquisitionError(
                        f"{kind}: download exceeds the 1 GiB limit"
                    )

                if (
                    expected_size is not None
                    and size > expected_size
                ):
                    raise BulkAcquisitionError(
                        f"{kind}: compressed size mismatch: "
                        f"expected {expected_size}, "
                        f"received over {expected_size}"
                    )

                output.write(chunk)
                digest.update(chunk)

    if expected_size is not None and size != expected_size:
        raise BulkAcquisitionError(
            f"{kind}: compressed size mismatch: "
            f"expected {expected_size}, received {size}"
        )

    count = _validate_jsonl_gzip(
        path,
        kind,
        expected_object,
    )

    return BulkSource(
        source_type=kind,
        source_url=final_url,
        source_updated_at=metadata["updated_at"],
        downloaded_at=datetime.now(
            timezone.utc
        ).isoformat(),
        sha256=digest.hexdigest(),
        record_count=count,
    )


def acquire_bulk_sources(
    destination_directory: str | Path,
    *,
    user_agent: str = _DEFAULT_USER_AGENT,
) -> AcquiredBulkSources:
    """Download both bulk exports into a new directory.

    The destination must not exist. All downloaded files are
    removed if either source fails, including interruption.
    No existing files are touched.
    """

    destination = Path(destination_directory)

    if (
        not isinstance(user_agent, str)
        or not user_agent.strip()
    ):
        raise ValueError(
            "user_agent must be nonempty"
        )

    if (
        destination.exists()
        or destination.is_symlink()
    ):
        raise FileExistsError(
            f"destination already exists: {destination}"
        )

    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"destination parent does not exist: "
            f"{destination.parent}"
        )

    # Exclusive creation. No existing directory is reused.
    destination.mkdir()

    try:
        sources: list[BulkSource] = []

        for kind, filename, expected_object in _BULK_TYPES:
            metadata = _metadata(kind, user_agent)

            sources.append(
                _download(
                    metadata,
                    kind,
                    destination / filename,
                    expected_object,
                    user_agent,
                )
            )

        return AcquiredBulkSources(
            cards_path=destination / _BULK_TYPES[0][1],
            tags_path=destination / _BULK_TYPES[1][1],
            bulk_sources=(
                sources[0],
                sources[1],
            ),
        )

    except BaseException:
        shutil.rmtree(destination)
        raise
