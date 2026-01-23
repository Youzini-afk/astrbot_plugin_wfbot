from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

from .wf_http import HttpClient


Platform = Literal["pc", "ps4", "xbox", "switch", "mobile"]


@dataclass(frozen=True)
class MarketResponse:
    status: int
    json: Any | None
    raw: bytes
    url: str


class WarframeMarketClient:
    """
    Thin wrapper over warframe.market API with NyxBot-like headers:
    - Accept-Language: zh-CN...
    - Language: zh-hans
    - Platform: pc/ps4/xbox/switch/mobile
    - Crossplay: true
    """

    BASE_V1 = "https://api.warframe.market/v1"
    BASE_V2 = "https://api.warframe.market/v2"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def get_items(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V2}/items", platform=platform)

    async def get_orders_item(self, slug: str, *, platform: Platform = "pc") -> MarketResponse:
        slug = slug.strip()
        return await self._get(f"{self.BASE_V2}/orders/item/{urllib.parse.quote(slug)}", platform=platform)

    async def get_item_set(self, slug: str, *, platform: Platform = "pc") -> MarketResponse:
        slug = slug.strip()
        return await self._get(f"{self.BASE_V2}/item/{urllib.parse.quote(slug)}/set", platform=platform)

    async def search_auctions(self, query_params: dict[str, str], *, platform: Platform = "pc") -> MarketResponse:
        # NyxBot uses v1 /auctions/search with query string.
        qs = urllib.parse.urlencode(query_params, doseq=True)
        url = f"{self.BASE_V1}/auctions/search"
        if qs:
            url = f"{url}?{qs}"
        return await self._get(url, platform=platform)

    async def get_ducats(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V1}/tools/ducats", platform=platform)

    async def get_lich_weapons(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V2}/lich/weapons", platform=platform)

    async def get_sister_weapons(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V2}/sister/weapons", platform=platform)

    async def get_lich_ephemeras(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V2}/lich/ephemeras", platform=platform)

    async def get_sister_ephemeras(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V2}/sister/ephemeras", platform=platform)

    async def get_riven_weapons(self, *, platform: Platform = "pc") -> MarketResponse:
        return await self._get(f"{self.BASE_V2}/riven/weapons", platform=platform)

    async def _get(self, url: str, *, platform: Platform) -> MarketResponse:
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Language": "zh-hans",
            "Platform": platform,
            "Pragma": "no-cache",
            "Crossplay": "true",
        }
        resp = await self._http.get(url, headers=headers)
        data = None
        if 200 <= resp.status < 300:
            try:
                data = resp.json()
            except Exception:
                data = None
        return MarketResponse(status=resp.status, json=data, raw=resp.body, url=resp.url)

