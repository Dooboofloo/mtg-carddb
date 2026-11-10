"""Small shared HTTP guard for explicit Scryfall source downloads.

No networking occurs at import time. Callers retain responsibility for
source-specific payload validation and their filesystem cleanup.
"""

from __future__ import annotations

from collections.abc import Callable
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request


_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_MAX_ATTEMPTS = 3


def _is_https_url(url: object) -> bool:
    if not isinstance(url, str):
        return False

    try:
        parts = urlsplit(url)

        return (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.fragment
            and parts.port in (None, 443)
        )

    except ValueError:
        return False


def open_verified_https(
    request: Request,
    *,
    opener: Callable,
    timeout: float,
    error_type: type[Exception],
):
    """Open a response, reject insecure effective URLs, retry 5xx only.

    Retries happen before any response body is consumed.
    A failed body download is never silently resumed or spliced
    together with another.

    HTTP 429 deliberately fails fast to respect Scryfall's guidance.
    """
    url = request.full_url

    if not _is_https_url(url):
        raise error_type(
            f"refusing insecure request URL: {url!r}"
        )

    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = opener(
                request,
                timeout=timeout,
            )

        except HTTPError as exc:
            code = exc.code
            exc.close()

            if code == 429:
                raise error_type(
                    f"Scryfall rate-limited request to {url} "
                    "(HTTP 429); stop and retry manually later"
                ) from exc

            if (
                code not in _RETRYABLE_STATUS
                or attempt == _MAX_ATTEMPTS - 1
            ):
                raise error_type(
                    f"request failed for {url}: HTTP {code}"
                ) from exc

        except (
            URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise error_type(
                    f"request failed for {url}: {exc}"
                ) from exc

        else:
            try:
                final_url = response.geturl()

                if not _is_https_url(final_url):
                    raise error_type(
                        "unsafe final response URL for "
                        f"{url}: {final_url!r}"
                    )

            except BaseException:
                response.close()
                raise

            return response

        sleep(2 ** attempt)

    raise AssertionError("unreachable HTTP retry state")
