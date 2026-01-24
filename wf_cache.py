from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles
from astrbot.api import logger  # type: ignore


@dataclass(frozen=True)
class FileCache:
    base_dir: Path

    def path(self, *parts: str) -> Path:
        return self.base_dir.joinpath(*parts)

    async def read_bytes(self, *parts: str) -> bytes | None:
        p = self.path(*parts)
        try:
            async with aiofiles.open(p, "rb") as f:
                return await f.read()
        except FileNotFoundError:
            return None

    async def write_bytes(self, data: bytes, *parts: str) -> Path:
        p = self.path(*parts)
        try:
            await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
            async with aiofiles.open(p, "wb") as f:
                await f.write(data)
        except OSError:
            logger.error("file cache write failed: %s", p, exc_info=True)
            raise
        return p

    async def read_json(self, *parts: str) -> Any | None:
        data = await self.read_bytes(*parts)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            logger.debug("file cache json decode failed: %s", self.path(*parts), exc_info=True)
            return None

    async def write_json(self, obj: Any, *parts: str) -> Path:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return await self.write_bytes(data, *parts)


