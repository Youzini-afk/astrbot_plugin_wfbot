from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api.all import *  # type: ignore

from wf_config import WarframeConfig
from wf_datasource import WarframeDataSource


@register("astrbot_plugin_wfbot", "astr_wfbot", "Warframe datasource manager", "0.1.0")
class WarframeDatasourcePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = Path(self.context.get_data_dir()) / "warframe"
        wf_cfg = WarframeConfig(
            data_dir=data_dir,
            public_export_language=str(self.config.get("public_export_language", "zh")),
            worldstate_refresh_interval=float(self.config.get("worldstate_refresh_interval", 600)),
            public_export_refresh_interval=float(self.config.get("public_export_refresh_interval", 21600)),
            mirrors_refresh_interval=float(self.config.get("mirrors_refresh_interval", 21600)),
            market_bootstrap_refresh_interval=float(self.config.get("market_bootstrap_refresh_interval", 86400)),
            cleanup_retention_days=int(self.config.get("cleanup_retention_days", 14)),
            keep_worldstate_snapshots=int(self.config.get("keep_worldstate_snapshots", 24)),
            keep_mirror_snapshots=int(self.config.get("keep_mirror_snapshots", 10)),
        )

        self.ds = WarframeDataSource.create(wf_cfg)
        self.mgr = self.ds.create_manager()

        self._task: asyncio.Task | None = None
        try:
            self._task = asyncio.create_task(self._start_background())
        except RuntimeError:
            # No running loop during plugin init in some environments; user can run /wf_refresh manually.
            self._task = None

    async def _start_background(self) -> None:
        await self.mgr.refresh_all_once()
        await self.mgr.start_async()

    @filter.command("wf_refresh")
    async def wf_refresh(self, event: AstrMessageEvent):
        await self.mgr.refresh_all_once()
        yield event.plain_result("ok")

    @filter.command("wf_stop")
    async def wf_stop(self, event: AstrMessageEvent):
        await self.mgr.stop_async()
        yield event.plain_result("stopped")

