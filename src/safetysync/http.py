"""Client HTTP commun : en-têtes d'auth, retries (429/5xx/erreurs réseau) et erreurs lisibles."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, service: str, status_code: int, method: str, url: str, body: str) -> None:
        super().__init__(f"{service} {method} {url} -> {status_code}: {body[:500]}")
        self.service = service
        self.status_code = status_code
        self.body = body


class HttpApi:
    """Petit wrapper httpx. `sleep` est injectable pour les tests."""

    service = "API"

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str],
        *,
        timeout: float = 60.0,
        max_retries: int = 4,
        http: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = dict(headers)
        self._max_retries = max_retries
        self._http = http or httpx.Client(timeout=timeout)
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        url = self.url(path)
        all_headers = {**self._headers, **(headers or {})}
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._http.request(
                    method, url, params=params, json=json, content=content, headers=all_headers
                )
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    raise ApiError(self.service, 0, method, url, str(exc)) from exc
                delay = 2**attempt
                logger.warning(
                    "%s %s %s : %s, nouvel essai dans %ss", self.service, method, url, exc, delay
                )
                self._sleep(delay)
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt <= self._max_retries:
                delay = _retry_delay(resp, attempt)
                logger.warning(
                    "%s %s %s -> %s, nouvel essai dans %ss",
                    self.service,
                    method,
                    url,
                    resp.status_code,
                    delay,
                )
                self._sleep(delay)
                continue
            if resp.status_code >= 400:
                raise ApiError(self.service, resp.status_code, method, url, resp.text)
            return resp

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self.request(method, path, **kwargs)
        if not resp.content:
            return {}
        return resp.json()


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    value = resp.headers.get("Retry-After")
    if value:
        try:
            return min(float(value), 120.0)
        except ValueError:
            pass
    return float(2**attempt)
