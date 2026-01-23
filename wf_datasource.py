from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .wf_cache import FileCache
from .wf_config import WarframeConfig
from .wf_http import HttpClient
from .wf_urls import (
    WARFRAME_DATA_SOURCE_ALIAS,
    WARFRAME_DATA_SOURCE_CAMBION_CYCLE,
    WARFRAME_DATA_SOURCE_CETUS_CYCLE,
    WARFRAME_DATA_SOURCE_DUVIRI_CYCLE,
    WARFRAME_DATA_SOURCE_EARTH_CYCLE,
    WARFRAME_DATA_SOURCE_MARKET_RIVEN_TION,
    WARFRAME_DATA_SOURCE_MARKET_RIVEN_TION_ALIAS,
    WARFRAME_DATA_SOURCE_NODES,
    WARFRAME_DATA_SOURCE_REWARD_POOL,
    WARFRAME_DATA_SOURCE_RIVEN_ANALYSE_TREND,
    WARFRAME_DATA_SOURCE_SOLNODES,
    WARFRAME_DATA_SOURCE_STATE_TRANSLATION,
    WARFRAME_DATA_SOURCE_VALLIS_CYCLE,
    WARFRAME_DATA_SOURCE_ZARIMAN_CYCLE,
)
from .wf_source_mirrors import MirrorSourceFetcher
from .wf_source_public_export import PublicExportClient
from .wf_source_market import WarframeMarketClient
from .wf_source_worldstate import WorldStateClient


@dataclass(frozen=True)
class WarframeDataSource:
    """
    Convenience facade assembling all fetchers with shared config.

    This is intended to be created once in your AstrBot plugin and reused.
    """

    config: WarframeConfig
    http: HttpClient
    cache: FileCache
    worldstate: WorldStateClient
    public_export: PublicExportClient
    mirrors: MirrorSourceFetcher
    market: WarframeMarketClient

    @classmethod
    def create(cls, config: WarframeConfig | None = None, *, plugin_id: str = "astrbot_plugin_wfbot") -> "WarframeDataSource":
        cfg = config or WarframeConfig()
        # Resolve a sensible default data_dir when used inside AstrBot.
        if str(cfg.data_dir) in {".", ""}:
            try:
                from astrbot.api.star import StarTools  # type: ignore

                cfg = WarframeConfig(**{**cfg.__dict__, "data_dir": Path(StarTools.get_data_dir(plugin_id)) / "warframe"})
            except Exception:
                cfg = WarframeConfig(**{**cfg.__dict__, "data_dir": Path(__file__).resolve().parent / ".plugin_data" / "warframe"})

        # If sqlite_path is relative, place it under data_dir.
        if not cfg.sqlite_path.is_absolute():
            cfg = WarframeConfig(**{**cfg.__dict__, "sqlite_path": cfg.data_dir / cfg.sqlite_path})
        cache = FileCache(cfg.data_dir)
        http = HttpClient(
            connect_timeout=cfg.connect_timeout,
            read_timeout=cfg.read_timeout,
            retries=cfg.retries,
            retry_backoff_seconds=cfg.retry_backoff_seconds,
            max_request_time_seconds=cfg.http_max_request_time_seconds,
            no_proxy_suffixes=cfg.http_no_proxy_suffixes if cfg.http_no_proxy_enabled else (),
        )
        return cls(
            config=cfg,
            http=http,
            cache=cache,
            worldstate=WorldStateClient(http, cache),
            public_export=PublicExportClient(http, cache),
            mirrors=MirrorSourceFetcher(http, retries=cfg.retries, retry_backoff_seconds=cfg.retry_backoff_seconds),
            market=WarframeMarketClient(http),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    def create_manager(self):
        """
        Returns a WarframeDataManager pre-registered with NyxBot-style mirror datasets.
        """
        from .wf_manager import MirrorDataset, WarframeDataManager

        mgr = WarframeDataManager(self)
        mgr.register_mirrors(
            [
                MirrorDataset("alias", list(WARFRAME_DATA_SOURCE_ALIAS)),
                MirrorDataset("market_riven_tion", list(WARFRAME_DATA_SOURCE_MARKET_RIVEN_TION)),
                MirrorDataset("market_riven_tion_alias", list(WARFRAME_DATA_SOURCE_MARKET_RIVEN_TION_ALIAS)),
                MirrorDataset("nodes", list(WARFRAME_DATA_SOURCE_NODES)),
                MirrorDataset("solnodes", list(WARFRAME_DATA_SOURCE_SOLNODES)),
                MirrorDataset("reward_pool", list(WARFRAME_DATA_SOURCE_REWARD_POOL)),
                MirrorDataset("riven_analyse_trend", list(WARFRAME_DATA_SOURCE_RIVEN_ANALYSE_TREND)),
                MirrorDataset("state_translation", list(WARFRAME_DATA_SOURCE_STATE_TRANSLATION)),
            ]
        )
        mgr.register_cycles(
            [
                MirrorDataset("cycle_earth", list(WARFRAME_DATA_SOURCE_EARTH_CYCLE)),
                MirrorDataset("cycle_cetus", list(WARFRAME_DATA_SOURCE_CETUS_CYCLE)),
                MirrorDataset("cycle_vallis", list(WARFRAME_DATA_SOURCE_VALLIS_CYCLE)),
                MirrorDataset("cycle_cambion", list(WARFRAME_DATA_SOURCE_CAMBION_CYCLE)),
                MirrorDataset("cycle_zariman", list(WARFRAME_DATA_SOURCE_ZARIMAN_CYCLE)),
                MirrorDataset("cycle_duviri", list(WARFRAME_DATA_SOURCE_DUVIRI_CYCLE)),
            ]
        )
        return mgr
