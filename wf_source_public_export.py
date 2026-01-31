from __future__ import annotations

import asyncio
import lzma
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .wf_logging import debug as log_debug
from .wf_logging import warning as log_warning
import aiofiles
import aiohttp

from .wf_cache import FileCache
from .wf_http import HttpClient

PUBLIC_EXPORT_INDEX_URLS = [
    "https://origin.warframe.com/PublicExport/index_%s.txt.lzma",
    "https://content.warframe.com/PublicExport/index_%s.txt.lzma",
]
PUBLIC_EXPORT_MANIFEST_URL = "http://content.warframe.com/PublicExport/Manifest/%s"

_SAFE_EXPORT_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,240}\.json$", re.IGNORECASE)


def _parse_index_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lines.append(line)
    return lines


def _extract_export_filename(index_key_line: str) -> str:
    # NyxBot: substring(key, 0, key.indexOf("!"))
    if "!" in index_key_line:
        return index_key_line.split("!", 1)[0]
    return index_key_line


def _sanitize_export_filename(name: str) -> str | None:
    s = (name or "").strip()
    if not s:
        return None
    if "/" in s or "\\" in s:
        return None
    if ".." in s:
        return None
    if not _SAFE_EXPORT_FILENAME_RE.fullmatch(s):
        return None
    return s


def _hash_map(index_lines: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in index_lines:
        if "!" not in line:
            continue
        name, h = line.split("!", 1)
        out[name] = h
    return out


@dataclass(frozen=True)
class PublicExportUpdateResult:
    language: str
    index_status: int
    changed_files: list[str]
    downloaded_files: list[str]


class PublicExportClient:
    """
    Implements NyxBot-like PublicExport updater:
    1) Download LZMA index
    2) Decompress to text lines (Export*.json!hash)
    3) Compare with saved hashes and download only changed manifests
    """

    def __init__(self, http: HttpClient, cache: FileCache) -> None:
        self._http = http
        self._cache = cache

    async def _fetch_index_bytes(self, *, language: str, attempt: int) -> tuple[bytes | None, int]:
        urls = []
        for base in PUBLIC_EXPORT_INDEX_URLS:
            url = base % language
            if attempt > 0:
                url = f"{url}?t={int(time.time())}"
            urls.append(url)
        last_status = 0
        last_error = None
        for url in urls:
            try:
                resp = await self._http.get(url)
            except Exception as e:
                last_error = e
                continue
            last_status = int(resp.status or 0)
            if not (200 <= resp.status < 300):
                continue
            raw = resp.body
            if not raw or len(raw) < 32:
                continue
            try:
                _ = await asyncio.to_thread(lzma.decompress, raw)
            except lzma.LZMAError:
                continue
            return raw, last_status
        # fallback: force system proxy (trust_env=True) for warframe.com if direct fails
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {
                "Accept": "*/*",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            }
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                for url in urls:
                    try:
                        async with session.get(url, headers=headers) as resp:
                            last_status = int(resp.status or 0)
                            if not (200 <= resp.status < 300):
                                continue
                            raw = await resp.read()
                    except Exception as e:
                        last_error = e
                        continue
                    if not raw or len(raw) < 32:
                        continue
                    try:
                        _ = await asyncio.to_thread(lzma.decompress, raw)
                    except lzma.LZMAError:
                        continue
                    return raw, last_status
        except Exception as e:
            last_error = e
        if last_error:
            log_debug("public export index fetch error: %s", last_error, category="public_export")
        return None, last_status

    async def update(self, *, language: str = "zh") -> PublicExportUpdateResult:
        index_path = self._cache.path("public_export", f"index_{language}.txt.lzma")
        index_text_path = self._cache.path("public_export", f"index_{language}.txt")
        export_dir = self._cache.path("public_export", "export")

        last_status = 0
        decompressed: bytes | None = None
        raw_bytes: bytes | None = None
        for attempt in range(2):
            raw_bytes, last_status = await self._fetch_index_bytes(language=language, attempt=attempt)
            if raw_bytes is None:
                log_debug("public export index fetch failed (attempt=%s, status=%s)", attempt + 1, last_status, category="public_export")
                continue
            try:
                decompressed = await asyncio.to_thread(lzma.decompress, raw_bytes)
                break
            except lzma.LZMAError as e:
                log_debug("public export index decompress failed (attempt=%s): %s", attempt + 1, e, category="public_export")
                raw_bytes = None
                decompressed = None

        if decompressed is None:
            # fallback to cached index file (if any)
            try:
                if await asyncio.to_thread(index_path.exists):
                    async with aiofiles.open(index_path, "rb") as f:
                        cached_raw = await f.read()
                    if cached_raw and len(cached_raw) >= 32:
                        decompressed = await asyncio.to_thread(lzma.decompress, cached_raw)
                        raw_bytes = cached_raw
                        log_debug("using cached public export index", category="public_export")
            except Exception as e:
                log_debug("public export cached index decompress failed: %s", e, category="public_export")
        if decompressed is None:
            return PublicExportUpdateResult(
                language=language,
                index_status=last_status,
                changed_files=[],
                downloaded_files=[],
            )

        await asyncio.to_thread(index_text_path.parent.mkdir, parents=True, exist_ok=True)
        if raw_bytes is not None:
            async with aiofiles.open(index_path, "wb") as f:
                await f.write(raw_bytes)
        async with aiofiles.open(index_text_path, "wb") as f:
            await f.write(decompressed)

        async with aiofiles.open(index_text_path, "r", encoding="utf-8", errors="replace") as f:
            index_text = await f.read()
        index_lines = _parse_index_lines(index_text)
        current_hashes = _hash_map(index_lines)

        hashes_file = self._cache.path("public_export", f"keys_{language}.json")
        old_hashes = {}
        if await asyncio.to_thread(hashes_file.exists):
            old = await self._cache.read_json("public_export", f"keys_{language}.json")
            if isinstance(old, dict):
                old_hashes = {str(k): str(v) for k, v in old.items()}

        await self._cache.write_json(current_hashes, "public_export", f"keys_{language}.json")

        changed = [name for name, h in current_hashes.items() if old_hashes.get(name) != h]
        if not changed:
            return PublicExportUpdateResult(
                language=language,
                index_status=resp.status,
                changed_files=[],
                downloaded_files=[],
            )

        downloaded: list[str] = []
        await asyncio.to_thread(export_dir.mkdir, parents=True, exist_ok=True)
        export_root = export_dir.resolve()

        for line in index_lines:
            filename_raw = _extract_export_filename(line)
            if filename_raw not in changed:
                continue
            if "ExportRecipes" in filename_raw or "ExportFusionBundles" in filename_raw:
                continue

            filename = _sanitize_export_filename(filename_raw)
            if not filename:
                log_warning("skip suspicious public export filename: %s", filename_raw, category="public_export")
                continue

            out_path = (export_dir / filename).resolve()
            try:
                out_path.relative_to(export_root)
            except ValueError:
                log_warning("skip public export path traversal: %s", filename_raw, category="public_export")
                continue

            manifest_url = PUBLIC_EXPORT_MANIFEST_URL % line
            manifest_resp = await self._http.download_to(manifest_url, str(out_path))
            if 200 <= manifest_resp.status < 300:
                downloaded.append(filename)

        return PublicExportUpdateResult(
            language=language,
            index_status=last_status,
            changed_files=changed,
            downloaded_files=downloaded,
        )

    def export_path(self, filename: str) -> Path:
        return self._cache.path("public_export", "export", filename)
