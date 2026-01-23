from __future__ import annotations

import time
import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .wf_http import HttpClient


@dataclass(frozen=True)
class MirrorFetchResult:
    url: str | None
    status: int | None
    json: Any | None


class MirrorSourceFetcher:
    """
    NyxBot-like multi-source fetch:
    - Concurrently request all URLs
    - Return first successfully parsed JSON
    - Retry up to N times with backoff
    """

    def __init__(
        self,
        http: HttpClient,
        *,
        retries: int = 2,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self._http = http
        self._retries = int(max(0, retries))
        self._retry_backoff_seconds = float(max(0.0, retry_backoff_seconds))

    async def fetch_first_json(
        self,
        urls: Sequence[str],
        *,
        validate: Callable[[Any], bool] | None = None,
    ) -> MirrorFetchResult:
        if not urls:
            return MirrorFetchResult(url=None, status=None, json=None)

        for attempt in range(self._retries + 1):
            result = await self._fetch_first_json_once(urls, validate=validate)
            if result.json is not None:
                return result
            if attempt < self._retries:
                await asyncio.sleep(self._retry_backoff_seconds)
        return MirrorFetchResult(url=None, status=None, json=None)

    async def _fetch_first_json_once(
        self,
        urls: Sequence[str],
        *,
        validate: Callable[[Any], bool] | None,
    ) -> MirrorFetchResult:
        async def task(url: str) -> MirrorFetchResult:
            resp = await self._http.get(url)
            if not (200 <= resp.status < 300):
                return MirrorFetchResult(url=url, status=resp.status, json=None)
            try:
                data = resp.json()
            except Exception:
                return MirrorFetchResult(url=url, status=resp.status, json=None)
            if validate and not validate(data):
                return MirrorFetchResult(url=url, status=resp.status, json=None)
            return MirrorFetchResult(url=url, status=resp.status, json=data)

        tasks = [asyncio.create_task(task(u)) for u in urls]
        try:
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    try:
                        res = d.result()
                    except Exception:
                        continue
                    if res.json is not None:
                        for p in pending:
                            p.cancel()
                        return res
            return MirrorFetchResult(url=None, status=None, json=None)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

