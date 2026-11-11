"""Rate-limited GET client for public board APIs (no POST/apply)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from packages.core.services.board_discovery.types import (
    DEFAULT_MIN_REQUEST_INTERVAL,
    HTTP_TIMEOUT_SECONDS,
    USER_AGENT,
)


class BoardHttpClient:
    """GET-only client with ~1 req/s pacing and optional If-None-Match."""

    def __init__(
        self,
        *,
        min_interval: float = DEFAULT_MIN_REQUEST_INTERVAL,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._min_interval = min_interval
        self._last_request_at = 0.0
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "headers": {"User-Agent": USER_AGENT, "Accept": "application/json"},
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def get(self, url: str, *, etag: str | None = None) -> httpx.Response:
        self._pace()
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        response = self._client.get(url, headers=headers)
        self._last_request_at = time.monotonic()
        return response

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BoardHttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pace(self) -> None:
        if self._last_request_at <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
