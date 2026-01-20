from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wf_cache import FileCache
from wf_http import HttpClient


WARFRAME_WORLD_STATE_URL = "https://api.warframe.com/cdn/worldState.php"


@dataclass(frozen=True)
class WorldStateResult:
    status: int
    json: Any | None
    raw: bytes
    url: str


class WorldStateClient:
    def __init__(self, http: HttpClient, cache: FileCache) -> None:
        self._http = http
        self._cache = cache

    async def fetch(self) -> WorldStateResult:
        resp = await self._http.get(WARFRAME_WORLD_STATE_URL)
        data = None
        if 200 <= resp.status < 400:
            # NyxBot treats 2xx and 3xx as acceptable.
            try:
                data = resp.json()
            except Exception:
                data = None
        return WorldStateResult(status=resp.status, json=data, raw=resp.body, url=resp.url)

    async def refresh_cache(self) -> WorldStateResult:
        result = await self.fetch()
        if result.json is not None:
            # Keep both raw and parsed formats for flexibility.
            self._cache.write_bytes(result.raw, "worldstate", "latest.json")
            self._cache.write_json(result.json, "worldstate", "latest.parsed.json")
        return result

    def load_cached(self) -> Any | None:
        return self._cache.read_json("worldstate", "latest.parsed.json")

