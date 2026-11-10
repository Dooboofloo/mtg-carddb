"""Offline tests for the shared Scryfall HTTP safety boundary."""

from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from mtg_carddb import network


class Reply(BytesIO):
    def __init__(
        self,
        url="https://api.scryfall.com/sets",
    ):
        super().__init__(b"{}")
        self.url = url

    def geturl(self):
        return self.url


def request():
    return Request(
        "https://api.scryfall.com/sets"
    )


def failed(status):
    return HTTPError(
        "https://api.scryfall.com/sets",
        status,
        "synthetic server failure",
        {},
        None,
    )


def open_with(opener):
    return network.open_verified_https(
        request(),
        opener=opener,
        timeout=1,
        error_type=RuntimeError,
    )


def test_https_final_response_is_accepted():
    with open_with(
        lambda *_a, **_k: Reply()
    ) as result:
        assert result.read() == b"{}"


def test_secure_redirect_is_accepted():
    with open_with(
        lambda *_a, **_k: Reply(
            "https://data.scryfall.io/example"
        )
    ) as result:
        assert result.geturl() == (
            "https://data.scryfall.io/example"
        )


def test_insecure_final_redirect_is_rejected_and_closed():
    result = Reply("http://example.test/sets")

    with pytest.raises(
        RuntimeError,
        match="unsafe final response URL",
    ):
        open_with(
            lambda *_a, **_k: result
        )

    assert result.closed


def test_rejects_untrusted_original_scheme_without_network():
    attempts = []

    with pytest.raises(
        RuntimeError,
        match="insecure request URL",
    ):
        network.open_verified_https(
            Request("http://example.test/sets"),
            opener=lambda *_a, **_k: attempts.append("opened"),
            timeout=1,
            error_type=RuntimeError,
        )

    assert attempts == []


def test_503_retried_then_success(monkeypatch):
    attempts = []
    waits = []

    monkeypatch.setattr(
        network,
        "sleep",
        waits.append,
    )

    def opener(*_a, **_k):
        attempts.append(1)

        if len(attempts) == 1:
            raise failed(503)

        return Reply()

    with open_with(opener):
        pass

    assert len(attempts) == 2
    assert waits == [1]


def test_retries_exhaust_after_three_502s(monkeypatch):
    attempts = []
    waits = []

    monkeypatch.setattr(
        network,
        "sleep",
        waits.append,
    )

    def opener(*_a, **_k):
        attempts.append(1)
        raise failed(502)

    with pytest.raises(
        RuntimeError,
        match="HTTP 502",
    ):
        open_with(opener)

    assert len(attempts) == 3
    assert waits == [1, 2]


def test_429_fails_immediately_without_retry(monkeypatch):
    attempts = []

    monkeypatch.setattr(
        network,
        "sleep",
        lambda _: pytest.fail("unexpected retry"),
    )

    def opener(*_a, **_k):
        attempts.append(1)
        raise failed(429)

    with pytest.raises(
        RuntimeError,
        match="HTTP 429",
    ):
        open_with(opener)

    assert len(attempts) == 1


def test_404_fails_immediately_without_retry(monkeypatch):
    attempts = []

    monkeypatch.setattr(
        network,
        "sleep",
        lambda _: pytest.fail("unexpected retry"),
    )

    def opener(*_a, **_k):
        attempts.append(1)
        raise failed(404)

    with pytest.raises(
        RuntimeError,
        match="HTTP 404",
    ):
        open_with(opener)

    assert len(attempts) == 1


def test_transient_connection_failure_retried(monkeypatch):
    attempts = []

    monkeypatch.setattr(
        network,
        "sleep",
        lambda _: None,
    )

    def opener(*_a, **_k):
        attempts.append(1)

        if len(attempts) == 1:
            raise URLError("temporary offline")

        return Reply()

    with open_with(opener):
        pass

    assert len(attempts) == 2
