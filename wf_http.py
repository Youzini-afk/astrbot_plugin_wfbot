from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping
from urllib.parse import urlparse

import aiohttp
import aiofiles
from astrbot.api import logger  # type: ignore


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.body.decode(encoding, errors=errors)

    def json(self) -> Any:
        text = self.body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except Exception:
            # Some proxy endpoints (e.g. r.jina.ai) wrap JSON with a text header.
            # Fall back to parsing from the first JSON-looking character.
            idx_obj = text.find("{")
            idx_arr = text.find("[")
            idxs = [i for i in (idx_obj, idx_arr) if i != -1]
            if not idxs:
                raise
            start = min(idxs)
            return json.loads(text[start:])


class HttpClient:
    """
    Async HTTP client (AstrBot best practice: do not block event loop).

    Notes:
    - trust_env=True respects system proxy env.
    - For *.warframe.com, NyxBot disables proxy; we mimic that by using trust_env=False.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        user_agent: str | None = None,
        no_proxy_suffixes: tuple[str, ...] = ("warframe.com",),
        retries: int = 2,
        retry_backoff_seconds: float = 2.0,
        max_request_time_seconds: float | None = 30.0,
        proxy_url: str | None = None,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=max(connect_timeout, read_timeout))
        self._user_agent = user_agent or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        self._no_proxy_suffixes = tuple(no_proxy_suffixes)
        self._retries = int(max(0, retries))
        self._retry_backoff_seconds = float(max(0.0, retry_backoff_seconds))
        self._max_request_time_seconds = None if max_request_time_seconds is None else float(max(0.0, max_request_time_seconds))
        self._proxy_url = proxy_url if proxy_url else None

        self._session_env: aiohttp.ClientSession | None = None
        self._session_direct: aiohttp.ClientSession | None = None

    async def aclose(self) -> None:
        sessions = [self._session_env, self._session_direct]
        self._session_env = None
        self._session_direct = None
        for s in sessions:
            if s is None:
                continue
            try:
                await s.close()
            except Exception:
                logger.debug("http session close failed", exc_info=True)

    async def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        request_headers: MutableMapping[str, str] = {
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": self._user_agent,
        }
        if headers:
            request_headers.update(headers)

        attempt = 0
        start_ts = time.monotonic()
        while True:
            attempt += 1
            last_exc: Exception | None = None
            # For direct hosts, try direct first, then (optionally) proxy/env as a fallback.
            host = urlparse(url).hostname or ""
            direct = any(host == s or host.endswith("." + s) for s in self._no_proxy_suffixes)
            modes = [False] if direct else [True]
            if direct:
                env_proxy = any(os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"))
                if self._proxy_url is not None or env_proxy:
                    modes.append(True)
            for force_proxy in modes:
                try:
                    session, use_proxy = await self._session_for_url(url, force_proxy=force_proxy)
                    proxy = self._proxy_url if use_proxy else None
                    async with session.get(url, headers=dict(request_headers), proxy=proxy) as resp:
                        body = await resp.read()
                        return HttpResponse(
                            status=int(resp.status),
                            headers={k: v for k, v in resp.headers.items()},
                            body=body,
                            url=str(resp.url),
                        )
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exc = e
                    logger.debug(
                        "http request failed (attempt=%s, proxy=%s): %s",
                        attempt,
                        "yes" if force_proxy else "no",
                        url,
                        exc_info=True,
                    )
                    # If direct failed and proxy is available, try proxy fallback in this attempt.
                    continue
            if self._max_request_time_seconds is not None and (time.monotonic() - start_ts) >= self._max_request_time_seconds:
                if last_exc:
                    raise last_exc
                raise
            if attempt > (1 + self._retries):
                if last_exc:
                    raise last_exc
                raise
            await asyncio.sleep(self._retry_backoff_seconds)

    async def download_to(self, url: str, out_path: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        resp = await self.get(url, headers=headers)
        if 200 <= resp.status < 300:
            await asyncio.to_thread(os.makedirs, os.path.dirname(out_path) or ".", exist_ok=True)
            async with aiofiles.open(out_path, "wb") as f:
                await f.write(resp.body)
        return resp

    async def _session_for_url(self, url: str, *, force_proxy: bool = False) -> tuple[aiohttp.ClientSession, bool]:
        host = urlparse(url).hostname or ""
        direct = (not force_proxy) and any(host == s or host.endswith("." + s) for s in self._no_proxy_suffixes)
        if direct:
            if self._session_direct is None or self._session_direct.closed:
                self._session_direct = aiohttp.ClientSession(timeout=self._timeout, trust_env=False)
            return self._session_direct, False

        if self._session_env is None or self._session_env.closed:
            self._session_env = aiohttp.ClientSession(timeout=self._timeout, trust_env=True)
        return self._session_env, True

