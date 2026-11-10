"""Capture and verify exact Scryfall set and type-catalog responses.

Archives preserve the source JSON bytes. Converting those responses
into ReferenceData is a separate operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from time import sleep
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .network import open_verified_https

class ReferenceArchiveError(ValueError):
    """A Scryfall reference source or saved archive is invalid."""


_SETS_URL = "https://api.scryfall.com/sets"

_CATALOG_NAMES = (
    "supertypes",
    "artifact-types",
    "creature-types",
    "enchantment-types",
    "land-types",
    "planeswalker-types",
    "spell-types",
)

_CATALOG_ROOT = "https://api.scryfall.com/catalog/"

_USER_AGENT = (
    "mtg-carddb/0.1 "
    "(+https://github.com/Dooboofloo/mtg-carddb)"
)

_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_MAX_SET_PAGES = 50


@dataclass(frozen=True, slots=True)
class ReferenceSource:
    source_type: str
    source_url: str
    filename: str
    downloaded_at: str
    sha256: str
    record_count: int
    source_updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceArchive:
    directory: Path
    sources: tuple[ReferenceSource, ...]

    def json_for_url(self, url: str) -> dict:
        """Retrieve a verified response for a captured Scryfall URL."""
        for source in self.sources:
            if source.source_url == url:
                raw = (
                    self.directory / source.filename
                ).read_bytes()

                if hashlib.sha256(raw).hexdigest() != source.sha256:
                    raise ReferenceArchiveError(
                        "reference checksum mismatch: "
                        f"{source.filename}"
                    )

                return _parse_document(raw, source.source_type)

        raise ReferenceArchiveError(
            f"URL absent from archive: {url}"
        )


def _set_page_url(url: str) -> str:
    """Restrict pagination to Scryfall's Sets endpoint."""
    parsed = urlsplit(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.scryfall.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path != "/sets"
        or parsed.fragment
    ):
        raise ReferenceArchiveError(
            f"invalid Sets pagination URL: {url!r}"
        )

    return url


def _parse_document(
    raw: bytes,
    source_type: str,
) -> dict:
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ReferenceArchiveError(
            f"{source_type}: invalid JSON response"
        ) from exc

    if (
        not isinstance(document, dict)
        or not isinstance(document.get("data"), list)
    ):
        raise ReferenceArchiveError(
            f"{source_type}: missing data array"
        )

    rows = document["data"]

    if source_type.startswith("sets:page:"):
        if (
            document.get("object") != "list"
            or type(document.get("has_more")) is not bool
            or any(
                not isinstance(row, dict)
                or row.get("object") != "set"
                or not isinstance(row.get("id"), str)
                or not isinstance(row.get("code"), str)
                for row in rows
            )
        ):
            raise ReferenceArchiveError(
                f"{source_type}: invalid set listing"
            )

        if document["has_more"]:
            if not isinstance(document.get("next_page"), str):
                raise ReferenceArchiveError(
                    f"{source_type}: missing next_page"
                )

            _set_page_url(document["next_page"])

    elif source_type.startswith("catalog:"):
        if (
            document.get("object") != "catalog"
            or not rows
            or any(
                not isinstance(item, str) or not item
                for item in rows
            )
        ):
            raise ReferenceArchiveError(
                f"{source_type}: invalid catalog"
            )
    else:
        raise ReferenceArchiveError(
            f"unknown reference source: {source_type}"
        )

    return document


def _download_json(
    url: str,
    user_agent: str,
) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
        },
    )

    with open_verified_https(
        request,
        opener=urlopen,
        timeout=60,
        error_type=ReferenceArchiveError,
    ) as response:
        raw = response.read(_MAX_DOCUMENT_BYTES + 1)

    if len(raw) > _MAX_DOCUMENT_BYTES:
        raise ReferenceArchiveError(
            f"oversized Scryfall response: {url}"
        )

    return raw


def capture_reference_sources(
    destination_directory: str | Path,
    *,
    user_agent: str = _USER_AGENT,
) -> ReferenceArchive:
    """Capture complete Sets pages and seven type catalogs.

    The destination must not exist. On failure, removes only the
    newly created directory. Existing files are never replaced.
    """

    destination = Path(destination_directory)

    if (
        not isinstance(user_agent, str)
        or not user_agent.strip()
    ):
        raise ValueError(
            "user_agent must be nonempty"
        )

    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"destination exists: {destination}"
        )

    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"parent directory missing: {destination.parent}"
        )

    destination.mkdir()
    sources: list[ReferenceSource] = []

    def capture(
        url: str,
        kind: str,
        filename: str,
    ) -> dict:
        if sources:
            # Scryfall requests fewer than 10 API calls per second.
            sleep(0.12)

        raw = _download_json(url, user_agent)
        document = _parse_document(raw, kind)

        (destination / filename).write_bytes(raw)

        sources.append(
            ReferenceSource(
                source_type=kind,
                source_url=url,
                filename=filename,
                downloaded_at=datetime.now(
                    timezone.utc
                ).isoformat(),
                sha256=hashlib.sha256(raw).hexdigest(),
                record_count=len(document["data"]),
            )
        )

        return document

    try:
        url = _SETS_URL
        seen: set[str] = set()

        for number in range(1, _MAX_SET_PAGES + 1):
            _set_page_url(url)

            if url in seen:
                raise ReferenceArchiveError(
                    "cyclic Sets pagination"
                )

            seen.add(url)

            document = capture(
                url,
                f"sets:page:{number}",
                f"sets-{number:04d}.json",
            )

            if not document["has_more"]:
                break

            url = document["next_page"]
        else:
            raise ReferenceArchiveError(
                "too many Sets pages"
            )

        for name in _CATALOG_NAMES:
            capture(
                _CATALOG_ROOT + name,
                f"catalog:{name}",
                f"catalog-{name}.json",
            )

        manifest = {
            "archive_version": 1,
            "sources": [
                asdict(source)
                for source in sources
            ],
        }

        (destination / "manifest.json").write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )

        return load_reference_sources(destination)

    except BaseException:
        shutil.rmtree(destination)
        raise


def load_reference_sources(
    directory: str | Path,
) -> ReferenceArchive:
    """Verify saved bytes, catalogs, and Sets page continuity."""

    directory = Path(directory)

    try:
        manifest = json.loads(
            (directory / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReferenceArchiveError(
            "missing or invalid reference manifest"
        ) from exc

    if (
        not isinstance(manifest, dict)
        or manifest.get("archive_version") != 1
    ):
        raise ReferenceArchiveError(
            "unsupported reference archive version"
        )

    entries = manifest.get("sources")

    if not isinstance(entries, list):
        raise ReferenceArchiveError(
            "invalid reference sources list"
        )

    sources: list[ReferenceSource] = []
    documents: dict[str, dict] = {}
    types: set[str] = set()
    urls: set[str] = set()

    for entry in entries:
        try:
            source = ReferenceSource(**entry)
        except (TypeError, ValueError) as exc:
            raise ReferenceArchiveError(
                "invalid reference source entry"
            ) from exc

        if (
            not isinstance(source.source_type, str)
            or not isinstance(source.source_url, str)
            or not isinstance(source.filename, str)
        ):
            raise ReferenceArchiveError(
                "invalid reference source fields"
            )

        if (
            source.source_type in types
            or source.source_url in urls
        ):
            raise ReferenceArchiveError(
                "duplicate reference source"
            )

        types.add(source.source_type)
        urls.add(source.source_url)

        # Reject path traversal from untrusted manifest entries.
        if (
            not re.fullmatch(
                r"(?:sets-\d{4}|catalog-[a-z-]+)\.json",
                source.filename,
            )
            or Path(source.filename).name != source.filename
        ):
            raise ReferenceArchiveError(
                "invalid reference filename"
            )

        if (
            not isinstance(source.sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                source.sha256,
            )
        ):
            raise ReferenceArchiveError(
                "invalid reference checksum"
            )

        if (
            type(source.record_count) is not int
            or source.record_count < 0
        ):
            raise ReferenceArchiveError(
                "invalid reference record count"
            )

        if not isinstance(source.downloaded_at, str):
            raise ReferenceArchiveError(
                "invalid download timestamp"
            )

        try:
            timestamp = datetime.fromisoformat(
                source.downloaded_at
            )

            if (
                timestamp.tzinfo is None
                or timestamp.utcoffset() is None
            ):
                raise ValueError(
                    "naive timestamp"
                )
        except ValueError as exc:
            raise ReferenceArchiveError(
                "invalid download timestamp"
            ) from exc

        try:
            raw = (
                directory / source.filename
            ).read_bytes()
        except OSError as exc:
            raise ReferenceArchiveError(
                "missing reference source file"
            ) from exc

        if (
            hashlib.sha256(raw).hexdigest()
            != source.sha256
        ):
            raise ReferenceArchiveError(
                "reference checksum mismatch: "
                f"{source.filename}"
            )

        document = _parse_document(
            raw,
            source.source_type,
        )

        if (
            len(document["data"])
            != source.record_count
        ):
            raise ReferenceArchiveError(
                "reference record count mismatch"
            )

        documents[source.source_type] = document
        sources.append(source)

    required = {
        f"catalog:{name}"
        for name in _CATALOG_NAMES
    }

    pages = [
        source
        for source in sources
        if source.source_type.startswith("sets:page:")
    ]

    if (
        not pages
        or len(pages) > _MAX_SET_PAGES
        or len(sources) != len(required) + len(pages)
    ):
        raise ReferenceArchiveError(
            "missing or extra reference sources"
        )

    expected = required | {
        f"sets:page:{number}"
        for number in range(1, len(pages) + 1)
    }

    if {
        source.source_type
        for source in sources
    } != expected:
        raise ReferenceArchiveError(
            "incomplete reference catalogs or page sequence"
        )

    for number in range(1, len(pages) + 1):
        kind = f"sets:page:{number}"

        source = next(
            s for s in pages
            if s.source_type == kind
        )

        document = documents[kind]

        if number == 1:
            if source.source_url != _SETS_URL:
                raise ReferenceArchiveError(
                    "invalid first Sets URL"
                )
        else:
            prior = documents[
                f"sets:page:{number - 1}"
            ]

            if (
                not prior["has_more"]
                or prior["next_page"]
                != source.source_url
            ):
                raise ReferenceArchiveError(
                    "broken Sets page link"
                )

        _set_page_url(source.source_url)

        if (
            document["has_more"]
            != (number < len(pages))
        ):
            raise ReferenceArchiveError(
                "incomplete Sets pagination"
            )

        if (
            source.filename
            != f"sets-{number:04d}.json"
        ):
            raise ReferenceArchiveError(
                "unexpected Sets filename"
            )

    for name in _CATALOG_NAMES:
        source = next(
            s for s in sources
            if s.source_type == f"catalog:{name}"
        )

        if (
            source.source_url
            != _CATALOG_ROOT + name
            or source.filename
            != f"catalog-{name}.json"
        ):
            raise ReferenceArchiveError(
                "invalid catalog URL or filename"
            )

    return ReferenceArchive(
        directory=directory,
        sources=tuple(sources),
    )
