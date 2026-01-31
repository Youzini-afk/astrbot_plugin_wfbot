from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .wf_logging import debug as log_debug
from .wf_logging import exception as log_exception
from .wf_logging import info as log_info
from .wf_logging import warning as log_warning

from .wf_cache import FileCache
from .wf_config import WarframeConfig
from .wf_events import EventBus
from .wf_store_file import FileStore
from .wf_store_sqlite import SqliteStore
from .wf_source_public_export import PublicExportUpdateResult
from .wf_source_worldstate import WorldStateResult

if TYPE_CHECKING:
    from .wf_datasource import WarframeDataSource


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


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class MirrorDataset:
    name: str
    urls: list[str]


class WarframeDataManager:
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
                log_warning("sqlite disabled (%s); falling back to file store", e, category="manager")
                self._store = None
        if self._store is None and self._cfg.enable_file_store:
            self._store = FileStore(self._cache.base_dir / "store")

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

        self._worldstate_lock = asyncio.Lock()
        self._public_export_lock = asyncio.Lock()
        self._mirrors_lock = asyncio.Lock()
        self._cycles_lock = asyncio.Lock()
        self._market_lock = asyncio.Lock()

        self._mirrors: list[MirrorDataset] = []
        self._cycles: list[MirrorDataset] = []

        self._worldstate_mem: Any | None = None
        self._worldstate_mem_at: float | None = None

    def register_mirrors(self, datasets: Iterable[MirrorDataset]) -> None:
        self._mirrors = list(datasets)

    def register_cycles(self, datasets: Iterable[MirrorDataset]) -> None:
        self._cycles = list(datasets)

    async def refresh_worldstate(self, *, snapshot: bool = True) -> WorldStateResult:
        async with self._worldstate_lock:
            old_meta = (await self.get_worldstate_meta()) or {}
            old_sha = old_meta.get("sha256")
            old_json = await self._ds.worldstate.load_cached()

            result = await self._ds.worldstate.refresh_cache()
            if result.json is None:
                return result

            self._worldstate_mem = result.json

            self._worldstate_mem_at = time.time()

            new_sha = await asyncio.to_thread(_sha256_hex, result.raw)
            meta = {
                "fetched_at": _utcnow().isoformat(),
                "status": result.status,
                "url": result.url,
                "sha256": new_sha,
            }
            await self._cache.write_json(meta, "worldstate", "latest.meta.json")

            if snapshot:
                slug = _timestamp_slug()
                await self._cache.write_bytes(result.raw, "worldstate", "snapshots", f"{slug}.json")
                await self._cache.write_json(meta, "worldstate", "snapshots", f"{slug}.meta.json")
                await asyncio.to_thread(self._trim_snapshots, self._cache.path("worldstate", "snapshots"), keep=self._cfg.keep_worldstate_snapshots)

            if self._store is not None:
                put = getattr(self._store, "put_snapshot", None)
                if callable(put):
                    await asyncio.to_thread(
                        put,
                        source="warframe",
                        name="worldstate",
                        status=result.status,
                        url=result.url,
                        sha256=new_sha,
                        json_obj=result.json,
                        raw=result.raw,
                    )

            if old_sha is not None and old_sha != new_sha:
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

    async def get_worldstate_cached(self) -> Any | None:
        ttl = float(max(0.0, self._cfg.worldstate_cache_ttl_seconds))
        if self._worldstate_mem_at is not None and (time.time() - self._worldstate_mem_at) <= ttl:
            return self._worldstate_mem
        data = await self._ds.worldstate.load_cached()
        self._worldstate_mem = data
        self._worldstate_mem_at = time.time()
        return data

    async def get_worldstate_meta(self) -> dict[str, Any] | None:
        meta = await self._cache.read_json("worldstate", "latest.meta.json")
        return meta if isinstance(meta, dict) else None


    async def get_mirror_cached(self, name: str) -> Any | None:
        return await self._cache.read_json("mirrors", name, "latest.json")

    async def get_mirror_meta(self, name: str) -> dict[str, Any] | None:
        meta = await self._cache.read_json("mirrors", name, "latest.meta.json")
        return meta if isinstance(meta, dict) else None
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
            await self._cache.write_json(meta, "public_export", "latest.meta.json")
            await self._cleanup_public_export_orphans(language=self._cfg.public_export_language)
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
        async with self._mirrors_lock:
            out: dict[str, Any] = {}
            for ds in self._mirrors:
                old_meta = await self._cache.read_json("mirrors", ds.name, "latest.meta.json")
                old_sha = old_meta.get("sha256") if isinstance(old_meta, dict) else None

                result = await self._ds.mirrors.fetch_first_json(ds.urls)
                out[ds.name] = result.json
                if result.json is None:
                    meta = {
                        "fetched_at": _utcnow().isoformat(),
                        "url": result.url,
                        "status": result.status,
                        "sha256": None,
                    }
                    await self._cache.write_json(meta, "mirrors", ds.name, "latest.meta.json")
                    continue

                raw = await asyncio.to_thread(_json_bytes, result.json) if isinstance(result.json, (dict, list)) else str(result.json).encode("utf-8", errors="replace")
                new_sha = await asyncio.to_thread(_sha256_hex, raw)
                meta = {
                    "fetched_at": _utcnow().isoformat(),
                    "url": result.url,
                    "status": result.status,
                    "sha256": new_sha,
                }
                await self._cache.write_json(result.json, "mirrors", ds.name, "latest.json")
                await self._cache.write_json(meta, "mirrors", ds.name, "latest.meta.json")

                slug = _timestamp_slug()
                await self._cache.write_json(result.json, "mirrors", ds.name, "snapshots", f"{slug}.json")
                await self._cache.write_json(meta, "mirrors", ds.name, "snapshots", f"{slug}.meta.json")
                await asyncio.to_thread(self._trim_snapshots, self._cache.path("mirrors", ds.name, "snapshots"), keep=self._cfg.keep_mirror_snapshots)

                if self._store is not None:
                    put = getattr(self._store, "put_snapshot", None)
                    if callable(put):
                        await asyncio.to_thread(
                            put,
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

    async def refresh_cycles(self) -> dict[str, Any]:
        async with self._cycles_lock:
            def _is_cycle_payload(data: Any) -> bool:
                if not isinstance(data, dict):
                    return False
                if "error" in data:
                    return False
                for k in ("timeLeft", "expiry", "expiration", "expiryDate", "endTime", "isDay", "isWarm", "state"):
                    if k in data:
                        return True
                return False

            async def _fetch_worldstate_cycles() -> tuple[dict[str, dict], str | None, int | None]:
                urls = [
                    "https://api.warframestat.us/pc",
                    "https://api.warframestat.us/pc/",
                    "https://api.warframestat.us/pc?language=zh",
                    "https://api.warframestat.us/pc?language=en",
                    "http://api.warframestat.us/pc",
                    "http://api.warframestat.us/pc/",
                    "http://api.warframestat.us/pc?language=zh",
                    "http://api.warframestat.us/pc?language=en",
                    "https://api.warframestat.us/pc/worldstate",
                    "https://api.warframestat.us/pc/worldstate?language=zh",
                    "https://api.warframestat.us/pc/worldstate?language=en",
                    "http://api.warframestat.us/pc/worldstate",
                    "http://api.warframestat.us/pc/worldstate?language=zh",
                    "http://api.warframestat.us/pc/worldstate?language=en",
                    "https://r.jina.ai/http://api.warframestat.us/pc",
                    "https://r.jina.ai/http://api.warframestat.us/pc/",
                    "https://r.jina.ai/http://api.warframestat.us/pc?language=zh",
                    "https://r.jina.ai/http://api.warframestat.us/pc?language=en",
                    "https://r.jina.ai/http://api.warframestat.us/pc/worldstate",
                    "https://r.jina.ai/http://api.warframestat.us/pc/worldstate?language=zh",
                    "https://r.jina.ai/http://api.warframestat.us/pc/worldstate?language=en",
                    "https://r.jina.ai/https://api.warframestat.us/pc",
                    "https://r.jina.ai/https://api.warframestat.us/pc/",
                    "https://r.jina.ai/https://api.warframestat.us/pc?language=zh",
                    "https://r.jina.ai/https://api.warframestat.us/pc?language=en",
                    "https://r.jina.ai/https://api.warframestat.us/pc/worldstate",
                    "https://r.jina.ai/https://api.warframestat.us/pc/worldstate?language=zh",
                    "https://r.jina.ai/https://api.warframestat.us/pc/worldstate?language=en",
                ]
                for url in urls:
                    try:
                        resp = await self._ds.http.get(url)
                    except Exception as e:
                        log_debug("cycle worldstate fetch error: %s (%s)", url, e, category="cycle")
                        continue
                    if not (200 <= resp.status < 300):
                        log_debug("cycle worldstate status: %s (%s)", url, resp.status, category="cycle")
                        continue
                    try:
                        data = resp.json()
                    except Exception as e:
                        log_debug("cycle worldstate json parse failed: %s (%s)", url, e, category="cycle")
                        continue
                    if not isinstance(data, dict) or "error" in data:
                        log_debug("cycle worldstate invalid payload: %s", url, category="cycle")
                        continue
                    cycles: dict[str, dict] = {}
                    for key in ("earthCycle", "cetusCycle", "vallisCycle", "cambionCycle", "zarimanCycle", "duviriCycle"):
                        v = data.get(key)
                        if not isinstance(v, dict):
                            continue
                        if _is_cycle_payload(v) or key == "earthCycle":
                            cycles[key] = v
                    if cycles:
                        return cycles, url, int(resp.status or 0)
                return {}, None, None

            out: dict[str, Any] = {}
            missing: list[str] = []
            for ds in self._cycles:
                if self._cfg.log_cycle_enabled and self._cfg.log_cycle_fetch_success:
                    log_debug("fetching cycle: %s from %s", ds.name, ds.urls[0] if ds.urls else "?", category="cycle")
                result = await self._ds.mirrors.fetch_first_json(ds.urls, validate=_is_cycle_payload)
                out[ds.name] = result.json
                if result.json is None:
                    if self._cfg.log_cycle_enabled and self._cfg.log_cycle_fetch_failures:
                        log_warning("cycle data fetch failed: %s (status=%s, url=%s)", ds.name, result.status, result.url, category="cycle")
                    missing.append(ds.name)
                    meta = {"fetched_at": _utcnow().isoformat(), "url": result.url, "status": result.status, "sha256": None}
                    await self._cache.write_json(meta, "mirrors", ds.name, "latest.meta.json")
                    continue

                raw = await asyncio.to_thread(_json_bytes, result.json) if isinstance(result.json, (dict, list)) else str(result.json).encode("utf-8", errors="replace")
                sha = await asyncio.to_thread(_sha256_hex, raw)
                meta = {"fetched_at": _utcnow().isoformat(), "url": result.url, "status": result.status, "sha256": sha}
                if self._cfg.log_cycle_enabled and self._cfg.log_cycle_fetch_success:
                    log_debug("cycle data cached: %s (status=%s)", ds.name, result.status, category="cycle")
                await self._cache.write_json(result.json, "mirrors", ds.name, "latest.json")
                await self._cache.write_json(meta, "mirrors", ds.name, "latest.meta.json")

            if missing:
                # Try to recover from cached official worldstate first
                try:
                    ws = await self._ds.worldstate.load_cached()
                except Exception:
                    ws = None
                if isinstance(ws, dict):
                    recovered = False
                    # Helper to compute cycle state based on activation time
                    def _compute_cycle_state(cycle_dict: dict, cycle_type: str) -> dict:
                        if not isinstance(cycle_dict, dict):
                            return cycle_dict
                        activation = cycle_dict.get("activation")
                        if activation is None:
                            return cycle_dict
                        try:
                            now_ts = float(ws.get("Time") or time.time())
                            activation_ts = float(activation)
                            cycle_dict = dict(cycle_dict)  # Make a copy
                            
                            if cycle_type == "cetus":
                                cycle_duration = 150 * 60  # 150 min
                                phase_duration = 100 * 60  # 100 min day
                                elapsed = (now_ts - activation_ts) % cycle_duration
                                is_day = elapsed < phase_duration
                                cycle_dict["state"] = "day" if is_day else "night"
                                cycle_dict["isDay"] = is_day
                                next_phase_end = activation_ts + (((int(elapsed / phase_duration) + 1) * phase_duration) if is_day else cycle_duration)
                                if next_phase_end <= now_ts:
                                    next_phase_end += cycle_duration
                                cycle_dict["expiry"] = next_phase_end
                            elif cycle_type == "vallis":
                                cycle_duration = 160 * 60  # 160 min
                                phase_duration = 80 * 60   # 80 min warm
                                elapsed = (now_ts - activation_ts) % cycle_duration
                                is_warm = elapsed < phase_duration
                                cycle_dict["state"] = "warm" if is_warm else "cold"
                                cycle_dict["isWarm"] = is_warm
                                next_phase_end = activation_ts + (((int(elapsed / phase_duration) + 1) * phase_duration) if is_warm else cycle_duration)
                                if next_phase_end <= now_ts:
                                    next_phase_end += cycle_duration
                                cycle_dict["expiry"] = next_phase_end
                            elif cycle_type == "cambion":
                                cycle_duration = 150 * 60  # 150 min
                                phase_duration = 75 * 60   # 75 min Fass
                                elapsed = (now_ts - activation_ts) % cycle_duration
                                is_fass = elapsed < phase_duration
                                cycle_dict["state"] = "fass" if is_fass else "vome"
                                next_phase_end = activation_ts + (((int(elapsed / phase_duration) + 1) * phase_duration) if is_fass else cycle_duration)
                                if next_phase_end <= now_ts:
                                    next_phase_end += cycle_duration
                                cycle_dict["expiry"] = next_phase_end
                        except Exception as e:
                            log_debug("cycle state computation failed for %s: %s", cycle_type, e, category="cycle")
                        return cycle_dict
                    
                    for ds_name in list(missing):
                        key = ds_name.replace("cycle_", "") + "Cycle"
                        cycle_type = ds_name.replace("cycle_", "")
                        v = ws.get(key)
                        if isinstance(v, dict) and (_is_cycle_payload(v) or ds_name == "cycle_earth"):
                            # Recompute cycle state based on activation time
                            v = _compute_cycle_state(v, cycle_type)
                            raw = await asyncio.to_thread(_json_bytes, v)
                            sha = await asyncio.to_thread(_sha256_hex, raw)
                            meta = {"fetched_at": _utcnow().isoformat(), "url": "worldstate", "status": 200, "sha256": sha}
                            await self._cache.write_json(v, "mirrors", ds_name, "latest.json")
                            await self._cache.write_json(meta, "mirrors", ds_name, "latest.meta.json")
                            out[ds_name] = v
                            recovered = True
                            if self._cfg.log_cycle_enabled and self._cfg.log_cycle_fetch_success:
                                log_info("cycle data recovered from cached worldstate: %s", ds_name, category="cycle")
                    if recovered:
                        missing = [n for n in missing if n not in out]

            if missing:
                cycles, url, status = await _fetch_worldstate_cycles()
                if cycles:
                    for ds_name in list(missing):
                        key = ds_name.replace("cycle_", "") + "Cycle"
                        v = cycles.get(key)
                        if not isinstance(v, dict):
                            continue
                        raw = await asyncio.to_thread(_json_bytes, v)
                        sha = await asyncio.to_thread(_sha256_hex, raw)
                        meta = {"fetched_at": _utcnow().isoformat(), "url": url, "status": status, "sha256": sha}
                        await self._cache.write_json(v, "mirrors", ds_name, "latest.json")
                        await self._cache.write_json(meta, "mirrors", ds_name, "latest.meta.json")
                        out[ds_name] = v
                        if self._cfg.log_cycle_enabled and self._cfg.log_cycle_fetch_success:
                        log_info("cycle data recovered from worldstate: %s", ds_name, category="cycle")
                else:
                    if self._cfg.log_cycle_enabled and self._cfg.log_cycle_fetch_failures:
                        log_warning("cycle worldstate fallback failed; missing=%s", missing, category="cycle")
            return out

    async def refresh_market_bootstrap(self) -> dict[str, int]:
        async with self._market_lock:
            saved: dict[str, int] = {}

            async def store(name: str, resp) -> None:
                if resp.json is None:
                    return
                raw = await asyncio.to_thread(_json_bytes, resp.json) if isinstance(resp.json, (dict, list)) else str(resp.json).encode("utf-8", errors="replace")
                sha = await asyncio.to_thread(_sha256_hex, raw)
                meta = {"fetched_at": _utcnow().isoformat(), "status": resp.status, "url": resp.url, "sha256": sha}
                await self._cache.write_json(resp.json, "market", name, "latest.json")
                await self._cache.write_json(meta, "market", name, "latest.meta.json")
                if self._store is not None:
                    put = getattr(self._store, "put_snapshot", None)
                    if callable(put):
                        await asyncio.to_thread(
                            put,
                            source="market",
                            name=name,
                            status=resp.status,
                            url=resp.url,
                            sha256=sha,
                            json_obj=resp.json,
                            raw=raw,
                        )
                saved[name] = 1

            await store("items", await self._ds.market.get_items())
            await store("riven_weapons", await self._ds.market.get_riven_weapons())
            await store("lich_weapons", await self._ds.market.get_lich_weapons())
            await store("sister_weapons", await self._ds.market.get_sister_weapons())
            await store("lich_ephemeras", await self._ds.market.get_lich_ephemeras())
            await store("sister_ephemeras", await self._ds.market.get_sister_ephemeras())

            return saved

    def cleanup(self) -> None:
        cutoff = _utcnow() - timedelta(days=int(max(0, self._cfg.cleanup_retention_days)))
        self._delete_older_than(self._cache.path("worldstate", "snapshots"), cutoff=cutoff)
        self._delete_older_than(self._cache.path("mirrors"), cutoff=cutoff)
        self._delete_older_than(self._cache.path("market"), cutoff=cutoff)
        self._delete_older_than(self._cache.path("public_export"), cutoff=cutoff, patterns=(".lzma", ".txt"))
        self._prune_empty_dirs(self._cache.base_dir)
        if self._store is not None:
            prune = getattr(self._store, "prune", None)
            if callable(prune):
                prune(retention_days=self._cfg.cleanup_retention_days)

    async def cleanup_async(self) -> None:
        await asyncio.to_thread(self.cleanup)

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
                await asyncio.to_thread(close)
        await self._ds.aclose()

    async def refresh_all_once(self) -> None:
        try:
            await self.refresh_worldstate(snapshot=True)
        except Exception:
            log_exception("refresh_worldstate failed", category="manager")

        try:
            await self.refresh_public_export()
        except Exception:
            log_exception("refresh_public_export failed", category="manager")

        try:
            log_info("starting refresh_cycles...", category="cycle")
            result = await self.refresh_cycles()
            log_info("refresh_cycles completed: %s", {k: (type(v).__name__ if v is not None else "None") for k, v in result.items()}, category="cycle")
        except Exception:
            log_exception("refresh_cycles failed", category="cycle")

        try:
            await self.refresh_mirrors()
        except Exception:
            log_exception("refresh_mirrors failed", category="manager")

        try:
            await self.refresh_market_bootstrap()
        except Exception:
            log_exception("refresh_market_bootstrap failed", category="manager")

        await self.cleanup_async()

    async def _run_loop(self) -> None:
        next_worldstate = 0.0
        next_public_export = 0.0
        next_cycles = 0.0
        next_mirrors = 0.0
        next_market = 0.0
        next_cleanup = 0.0

        while not self._stop_event.is_set():
            now = time.time()
            try:
                if self._cfg.worldstate_refresh_interval > 0 and now >= next_worldstate:
                    await self.refresh_worldstate(snapshot=True)
                    await self.refresh_cycles()
                    next_worldstate = now + float(self._cfg.worldstate_refresh_interval)
                    next_cycles = next_worldstate

                if self._cfg.public_export_refresh_interval > 0 and now >= next_public_export:
                    await self.refresh_public_export()
                    next_public_export = now + float(self._cfg.public_export_refresh_interval)

                if self._cfg.mirrors_refresh_interval > 0 and now >= next_mirrors:
                    await self.refresh_mirrors()
                    next_mirrors = now + float(self._cfg.mirrors_refresh_interval)

                # In case worldstate interval is disabled, still refresh cycles periodically.
                if self._cfg.worldstate_refresh_interval <= 0 and now >= next_cycles:
                    await self.refresh_cycles()
                    next_cycles = now + 600.0

                if self._cfg.market_bootstrap_refresh_interval > 0 and now >= next_market:
                    await self.refresh_market_bootstrap()
                    next_market = now + float(self._cfg.market_bootstrap_refresh_interval)

                if now >= next_cleanup:
                    await self.cleanup_async()
                    next_cleanup = now + 6 * 3600.0
            except Exception:
                log_exception("warframe data manager loop error", category="manager")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def _trim_snapshots(self, snapshots_dir: Path, *, keep: int) -> None:
        if keep <= 0 or not snapshots_dir.exists():
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
        for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir():
                try:
                    next(p.iterdir())
                except StopIteration:
                    try:
                        p.rmdir()
                    except OSError:
                        pass

    async def _cleanup_public_export_orphans(self, *, language: str) -> None:
        hashes = await self._cache.read_json("public_export", f"keys_{language}.json")
        if not isinstance(hashes, dict):
            return
        wanted = {str(k) for k in hashes.keys()}
        export_dir = self._cache.path("public_export", "export")
        await asyncio.to_thread(self._cleanup_public_export_orphans_sync, export_dir, wanted)

    def _cleanup_public_export_orphans_sync(self, export_dir: Path, wanted: set[str]) -> None:
        if not export_dir.exists():
            return
        for p in export_dir.glob("*.json"):
            if p.name not in wanted:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
