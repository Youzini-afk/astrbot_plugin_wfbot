from __future__ import annotations

import lzma
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .wf_cache import FileCache
from .wf_http import HttpClient

logger = logging.getLogger(__name__)

PUBLIC_EXPORT_INDEX_URL = "https://origin.warframe.com/PublicExport/index_%s.txt.lzma"
PUBLIC_EXPORT_MANIFEST_URL = "http://content.warframe.com/PublicExport/Manifest/%s"


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

    async def update(self, *, language: str = "zh") -> PublicExportUpdateResult:
        index_path = self._cache.path("public_export", f"index_{language}.txt.lzma")
        index_text_path = self._cache.path("public_export", f"index_{language}.txt")
        export_dir = self._cache.path("public_export", "export")

        index_url = PUBLIC_EXPORT_INDEX_URL % language
        last_status = 0
        decompressed: bytes | None = None
        # index is sometimes served partially; if cached file becomes corrupted, delete and retry once.
        for attempt in range(2):
            url = index_url if attempt == 0 else f"{index_url}?t={int(time.time())}"
            resp = await self._http.download_to(url, str(index_path))
            last_status = int(resp.status or 0)
            if not (200 <= resp.status < 300):
                return PublicExportUpdateResult(
                    language=language,
                    index_status=resp.status,
                    changed_files=[],
                    downloaded_files=[],
                )

            try:
                raw = index_path.read_bytes()
                if len(raw) < 32:
                    raise lzma.LZMAError("index file too small")
                decompressed = lzma.decompress(raw)
                break
            except (FileNotFoundError, lzma.LZMAError) as e:
                logger.warning("public export index decompress failed (attempt=%s): %s", attempt + 1, e)
                try:
                    index_path.unlink()
                except FileNotFoundError:
                    pass
                try:
                    index_text_path.unlink()
                except FileNotFoundError:
                    pass
                decompressed = None

        if decompressed is None:
            return PublicExportUpdateResult(
                language=language,
                index_status=last_status,
                changed_files=[],
                downloaded_files=[],
            )

        index_text_path.parent.mkdir(parents=True, exist_ok=True)
        index_text_path.write_bytes(decompressed)

        index_lines = _parse_index_lines(index_text_path.read_text("utf-8", errors="replace"))
        current_hashes = _hash_map(index_lines)

        hashes_file = self._cache.path("public_export", f"keys_{language}.json")
        old_hashes = {}
        if hashes_file.exists():
            old = self._cache.read_json("public_export", f"keys_{language}.json")
            if isinstance(old, dict):
                old_hashes = {str(k): str(v) for k, v in old.items()}

        self._cache.write_json(current_hashes, "public_export", f"keys_{language}.json")

        changed = [name for name, h in current_hashes.items() if old_hashes.get(name) != h]
        if not changed:
            return PublicExportUpdateResult(
                language=language,
                index_status=resp.status,
                changed_files=[],
                downloaded_files=[],
            )

        downloaded: list[str] = []
        export_dir.mkdir(parents=True, exist_ok=True)

        for line in index_lines:
            filename = _extract_export_filename(line)
            if filename not in changed:
                continue
            if "ExportRecipes" in filename or "ExportFusionBundles" in filename:
                continue

            out_path = export_dir / filename
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

