from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any, Iterable

from wf_cache import FileCache
from wf_config import WarframeConfig
from wf_events import EventBus
from wf_store_file import FileStore
from wf_store_sqlite import SqliteStore
from wf_source_public_export import PublicExportUpdateResult
from wf_source_worldstate import WorldStateResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from wf_datasource import WarframeDataSource


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_slug(dt: datetime | None = None) -> str:
    dt = dt or _utcnow()
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _is_older_than(path: Path, *, cutoff: datetime) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except FileNotFoundError:
        return False
    return mtime < cutoff


@dataclass(frozen=True)
class MirrorDataset:
    """
    A mirrored JSON dataset (NyxBot's ApiDataSourceUtils style).

    name: used for cache folder naming
    urls: mirror URL list, fetched concurrently and first-success wins
    """

    name: str
    urls: list[str]


class WarframeDataManager:
    """
    NyxBot-inspired data management:
    - 拉取（定时刷新、并发镜像取首个成功）
    - 存储（latest + snapshots + metadata）
    - 清理（保留数量 + 按天数过期清理 + PublicExport 无用文件清理）
    """

    def __init__(self, ds: WarframeDataSource) -> None:
        self._ds = ds
        self._cfg: WarframeConfig = ds.config
        self._cache: FileCache = ds.cache
        self.events = EventBus()

        self._store: Any | None = None
        if self._cfg.enable_sqlite:
            try:
                self._store = SqliteStore(self._cfg.sqlite_path)
            except Exception as e:
                logger.warning("sqlite disabled (%s); falling back to file store", e)
                self._store = None
        if self._store is None and self._cfg.enable_file_store:
            self._store = FileStore(self._cache.base_dir / "store")

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

        self._worldstate_lock = asyncio.Lock()
        self._public_export_lock = asyncio.Lock()
        self._mirrors_lock = asyncio.Lock()
        self._market_lock = asyncio.Lock()

        self._mirrors: list[MirrorDataset] = []

        # In-memory cache (NyxBot caches for 3 minutes)
        self._worldstate_mem: Any | None = None
        self._worldstate_mem_at: float | None = None

    def register_mirrors(self, datasets: Iterable[MirrorDataset]) -> None:
        self._mirrors = list(datasets)

    # ---------- Public APIs (pull + store) ----------

    async def refresh_worldstate(self, *, snapshot: bool = True) -> WorldStateResult:
        async with self._worldstate_lock:
            old_meta = self._cache.read_json("worldstate", "latest.meta.json") or {}
            old_sha = old_meta.get("sha256") if isinstance(old_meta, dict) else None
            old_json = self._ds.worldstate.load_cached()

            result = await self._ds.worldstate.refresh_cache()
            if result.json is None:
                return result

            self._worldstate_mem = result.json
            self._worldstate_mem_at = time.time()

            new_sha = hashlib.sha256(result.raw).hexdigest()
            meta = {
                "fetched_at": _utcnow().isoformat(),
                "status": result.status,
                "url": result.url,
                "sha256": new_sha,
            }
            self._cache.write_json(meta, "worldstate", "latest.meta.json")

            if snapshot:
                slug = _timestamp_slug()
                self._cache.write_bytes(result.raw, "worldstate", "snapshots", f"{slug}.json")
                self._cache.write_json(meta, "worldstate", "snapshots", f"{slug}.meta.json")
                self._trim_snapshots(self._cache.path("worldstate", "snapshots"), keep=self._cfg.keep_worldstate_snapshots)

            if self._store is not None:
                self._store.put_snapshot(
                    source="warframe",
                    name="worldstate",
                    status=result.status,
                    url=result.url,
                    sha256=new_sha,
                    json_obj=result.json,
                    raw=result.raw,
                )

            changed = (old_sha is not None) and (old_sha != new_sha)
            if changed:
                await self.events.emit(
                    "worldstate.updated",
                    {
                        "old_sha256": old_sha,
                        "new_sha256": new_sha,
                        "old": old_json,
                        "new": result.json,
                        "fetched_at": meta["fetched_at"],
                    },
                )
            return result

    def get_worldstate_cached(self) -> Any | None:
        ttl = float(max(0.0, self._cfg.worldstate_cache_ttl_seconds))
        if self._worldstate_mem_at is not None and (time.time() - self._worldstate_mem_at) <= ttl:
            return self._worldstate_mem
        data = self._ds.worldstate.load_cached()
        self._worldstate_mem = data
        self._worldstate_mem_at = time.time()
        return data

    async def refresh_public_export(self) -> PublicExportUpdateResult:
        async with self._public_export_lock:
            res = await self._ds.public_export.update(language=self._cfg.public_export_language)
            meta = {
                "fetched_at": _utcnow().isoformat(),
                "language": self._cfg.public_export_language,
                "index_status": res.index_status,
                "changed_files": res.changed_files,
                "downloaded_files": res.downloaded_files,
            }
            self._cache.write_json(meta, "public_export", "latest.meta.json")
            # Remove export files not referenced by current hashes (optional hygiene)
            self._cleanup_public_export_orphans(language=self._cfg.public_export_language)
            if res.downloaded_files:
                await self.events.emit(
                    "public_export.updated",
                    {
                        "language": self._cfg.public_export_language,
                        "changed_files": res.changed_files,
                        "downloaded_files": res.downloaded_files,
                        "fetched_at": meta["fetched_at"],
                    },
                )
            return res

    async def refresh_mirrors(self) -> dict[str, Any]:
        """
        Refresh all registered mirror datasets. Returns {dataset_name: json or None}.
        """
        async with self._mirrors_lock:
            out: dict[str, Any] = {}
            for ds in self._mirrors:
                old_meta = self._cache.read_json("mirrors", ds.name, "latest.meta.json") or {}
                old_sha = old_meta.get("sha256") if isinstance(old_meta, dict) else None
                result = await self._ds.mirrors.fetch_first_json(ds.urls)
                out[ds.name] = result.json

                if result.json is None:
                    continue

                raw = (
                    json_bytes(result.json)
                    if isinstance(result.json, (dict, list))
                    else str(result.json).encode("utf-8", errors="replace")
                )
                new_sha = hashlib.sha256(raw).hexdigest()
                meta = {
                    "fetched_at": _utcnow().isoformat(),
                    "url": result.url,
                    "status": result.status,
                    "sha256": new_sha,
                }
                self._cache.write_json(result.json, "mirrors", ds.name, "latest.json")
                self._cache.write_json(meta, "mirrors", ds.name, "latest.meta.json")

                slug = _timestamp_slug()
                self._cache.write_json(result.json, "mirrors", ds.name, "snapshots", f"{slug}.json")
                self._cache.write_json(meta, "mirrors", ds.name, "snapshots", f"{slug}.meta.json")
                self._trim_snapshots(self._cache.path("mirrors", ds.name, "snapshots"), keep=self._cfg.keep_mirror_snapshots)

                if self._store is not None:
                    self._store.put_snapshot(
                        source="mirrors",
                        name=ds.name,
                        status=int(result.status or 0),
                        url=str(result.url or ""),
                        sha256=new_sha,
                        json_obj=result.json,
                        raw=raw,
                    )

                if old_sha is not None and old_sha != new_sha:
                    await self.events.emit(
                        "mirrors.updated",
                        {
                            "dataset": ds.name,
                            "old_sha256": old_sha,
                            "new_sha256": new_sha,
                            "url": result.url,
                            "status": result.status,
                            "fetched_at": meta["fetched_at"],
                        },
                    )

            return out

    async def refresh_market_bootstrap(self) -> dict[str, int]:
        """
        Refresh 'list' endpoints used for later queries (items, riven weapons, lich/sister weapons, ephemeras).
        """
        async with self._market_lock:
            saved: dict[str, int] = {}

            def store(name: str, payload: Any | None, status: int, url: str) -> None:
                if payload is None:
                    return
                raw = json_bytes(payload) if isinstance(payload, (dict, list)) else str(payload).encode("utf-8", errors="replace")
                sha = hashlib.sha256(raw).hexdigest()
                meta = {"fetched_at": _utcnow().isoformat(), "status": status, "url": url}
                self._cache.write_json(payload, "market", name, "latest.json")
                self._cache.write_json(meta, "market", name, "latest.meta.json")
                if self._store is not None:
                    self._store.put_snapshot(
                        source="market",
                        name=name,
                        status=status,
                        url=url,
                        sha256=sha,
                        json_obj=payload,
                        raw=raw,
                    )
                saved[name] = 1

            resp = await self._ds.market.get_items()
            store("items", resp.json, resp.status, resp.url)

            resp = await self._ds.market.get_riven_weapons()
            store("riven_weapons", resp.json, resp.status, resp.url)

            resp = await self._ds.market.get_lich_weapons()
            store("lich_weapons", resp.json, resp.status, resp.url)

            resp = await self._ds.market.get_sister_weapons()
            store("sister_weapons", resp.json, resp.status, resp.url)

            resp = await self._ds.market.get_lich_ephemeras()
            store("lich_ephemeras", resp.json, resp.status, resp.url)

            resp = await self._ds.market.get_sister_ephemeras()
            store("sister_ephemeras", resp.json, resp.status, resp.url)

            return saved

    # ---------- Cleanup ----------

    def cleanup(self) -> None:
        cutoff = _utcnow() - timedelta(days=int(max(0, self._cfg.cleanup_retention_days)))
        self._delete_older_than(self._cache.path("worldstate", "snapshots"), cutoff=cutoff)
        self._delete_older_than(self._cache.path("mirrors"), cutoff=cutoff)
        self._delete_older_than(self._cache.path("market"), cutoff=cutoff)
        # PublicExport is content-addressed-ish; prefer orphan cleanup + age cleanup for indexes.
        self._delete_older_than(self._cache.path("public_export"), cutoff=cutoff, patterns=(".lzma", ".txt"))
        self._prune_empty_dirs(self._cache.base_dir)
        if self._store is not None:
            self._store.prune(retention_days=self._cfg.cleanup_retention_days)

    # ---------- Scheduler (asyncio) ----------

    async def start_async(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="warframe-data-manager")

    async def stop_async(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._store is not None:
            close = getattr(self._store, "close", None)
            if callable(close):
                close()
        await self._ds.aclose()

    async def refresh_all_once(self) -> None:
        await self.refresh_worldstate(snapshot=True)
        await self.refresh_public_export()
        await self.refresh_mirrors()
        await self.refresh_market_bootstrap()
        self.cleanup()

    async def _run_loop(self) -> None:
        next_worldstate = 0.0
        next_public_export = 0.0
        next_mirrors = 0.0
        next_market = 0.0
        next_cleanup = 0.0

        while not self._stop_event.is_set():
            now = time.time()
            try:
                if self._cfg.worldstate_refresh_interval > 0 and now >= next_worldstate:
                    await self.refresh_worldstate(snapshot=True)
                    next_worldstate = now + float(self._cfg.worldstate_refresh_interval)

                if self._cfg.public_export_refresh_interval > 0 and now >= next_public_export:
                    await self.refresh_public_export()
                    next_public_export = now + float(self._cfg.public_export_refresh_interval)

                if self._cfg.mirrors_refresh_interval > 0 and now >= next_mirrors:
                    await self.refresh_mirrors()
                    next_mirrors = now + float(self._cfg.mirrors_refresh_interval)

                if self._cfg.market_bootstrap_refresh_interval > 0 and now >= next_market:
                    await self.refresh_market_bootstrap()
                    next_market = now + float(self._cfg.market_bootstrap_refresh_interval)

                if now >= next_cleanup:
                    self.cleanup()
                    next_cleanup = now + 6 * 3600.0
            except Exception:
                logger.exception("warframe data manager loop error")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    # ---------- Internals ----------

    def _trim_snapshots(self, snapshots_dir: Path, *, keep: int) -> None:
        if keep <= 0:
            return
        if not snapshots_dir.exists():
            return
        files = sorted([p for p in snapshots_dir.glob("*.json") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep:]:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            meta = p.with_suffix(".meta.json")
            try:
                meta.unlink()
            except FileNotFoundError:
                pass

    def _delete_older_than(self, root: Path, *, cutoff: datetime, patterns: tuple[str, ...] | None = None) -> None:
        if not root.exists():
            return
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if patterns and not p.name.endswith(patterns):
                continue
            if _is_older_than(p, cutoff=cutoff):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass

    def _prune_empty_dirs(self, root: Path) -> None:
        if not root.exists():
            return
        # bottom-up
        for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir():
                try:
                    next(p.iterdir())
                except StopIteration:
                    try:
                        p.rmdir()
                    except OSError:
                        pass

    def _cleanup_public_export_orphans(self, *, language: str) -> None:
        hashes = self._cache.read_json("public_export", f"keys_{language}.json")
        if not isinstance(hashes, dict):
            return
        wanted = {str(k) for k in hashes.keys()}
        export_dir = self._cache.path("public_export", "export")
        if not export_dir.exists():
            return
        for p in export_dir.glob("*.json"):
            if p.name not in wanted:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass


def json_bytes(obj: Any) -> bytes:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


