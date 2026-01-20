from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api.all import *  # type: ignore
import astrbot.api.message_components as Comp

from wf_config import WarframeConfig
from wf_datasource import WarframeDataSource
from wf_format import (
    build_nodes_map,
    format_alerts,
    format_cycles,
    format_fissures,
    format_invasions,
    format_void_trader,
)
from wf_render import ImageRenderConfig, get_or_render_png


@register("astrbot_plugin_wfbot", "astr_wfbot", "Warframe datasource manager", "0.1.0")
class WarframeDatasourcePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = Path(self.context.get_data_dir()) / "warframe"
        refresh_cfg = self.config.get("refresh", {})
        if not isinstance(refresh_cfg, dict):
            refresh_cfg = {}

        retention_cfg = self.config.get("retention", {})
        if not isinstance(retention_cfg, dict):
            retention_cfg = {}

        render_cfg = self.config.get("render", {})
        if not isinstance(render_cfg, dict):
            render_cfg = {}

        wf_cfg = WarframeConfig(
            data_dir=data_dir,
            public_export_language=str(self.config.get("public_export_language", "zh")),
            worldstate_refresh_interval=float(refresh_cfg.get("worldstate", self.config.get("worldstate_refresh_interval", 600))),
            public_export_refresh_interval=float(refresh_cfg.get("public_export", self.config.get("public_export_refresh_interval", 21600))),
            mirrors_refresh_interval=float(refresh_cfg.get("mirrors", self.config.get("mirrors_refresh_interval", 21600))),
            market_bootstrap_refresh_interval=float(refresh_cfg.get("market_bootstrap", self.config.get("market_bootstrap_refresh_interval", 86400))),
            cleanup_retention_days=int(retention_cfg.get("cleanup_days", self.config.get("cleanup_retention_days", 14))),
            keep_worldstate_snapshots=int(retention_cfg.get("keep_worldstate_snapshots", self.config.get("keep_worldstate_snapshots", 24))),
            keep_mirror_snapshots=int(retention_cfg.get("keep_mirror_snapshots", self.config.get("keep_mirror_snapshots", 10))),
        )

        self.img_cfg = ImageRenderConfig(
            enabled=bool(render_cfg.get("enabled", self.config.get("image_mode", False))),
            cache_images=bool(render_cfg.get("cache_images", self.config.get("cache_images", False))),
            keep_images=int(render_cfg.get("keep_images", self.config.get("keep_image_cache", 10))),
        )
        self.img_dir = data_dir / "images"

        self.ds = WarframeDataSource.create(wf_cfg)
        self.mgr = self.ds.create_manager()

        self._task: asyncio.Task | None = None
        try:
            self._task = asyncio.create_task(self._start_background())
        except RuntimeError:
            # No running loop during plugin init in some environments; user can run /wf refresh manually.
            self._task = None

    async def _send_text_or_image(self, event: AstrMessageEvent, *, title: str, text: str):
        # Keep newlines: prefer chain_result with Plain (some adapters strip plain_result).
        if not self.img_cfg.enabled:
            yield event.chain_result([Comp.Plain("\u200b" + text)])
            return

        path = get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            yield event.chain_result([Comp.Plain("\u200b" + text)])
            return

        yield event.chain_result([Comp.Image.fromFileSystem(str(path))])

    async def _start_background(self) -> None:
        await self.mgr.refresh_all_once()
        await self.mgr.start_async()

    async def _ensure_worldstate(self) -> dict | None:
        ws = self.mgr.get_worldstate_cached()
        if ws is None:
            await self.mgr.refresh_worldstate(snapshot=False)
            ws = self.mgr.get_worldstate_cached()
        return ws if isinstance(ws, dict) else None

    def _normalize_fissure_kind(self, kind: str | None) -> str:
        k = (kind or "normal").strip().lower()
        if k in {"normal", "n", "普通", "普通裂隙"}:
            return "normal"
        if k in {"steel", "sp", "钢铁", "钢铁之路", "钢铁裂隙"}:
            return "steel"
        if k in {"storm", "voidstorm", "railjack", "风暴", "虚空风暴"}:
            return "storm"
        return "normal"

    # Back-compat shortcut
    @filter.command("wf_refresh", alias={"wf更新", "wf刷新"})
    async def wf_refresh(self, event: AstrMessageEvent):
        await self.mgr.refresh_all_once()
        yield event.plain_result("ok")

    @filter.command_group("wf", alias={"星际战甲", "战甲"})
    def wf_group(self):
        pass

    @wf_group.command("help", alias={"h", "帮助", "菜单"})
    async def wf_help(self, event: AstrMessageEvent):
        msg = (
            "wf commands:\n"
            "- /wf refresh|刷新\n"
            "- /wf status|状态\n"
            "- /wf alerts|警报\n"
            "- /wf fissure|裂隙 [normal|steel|storm] (普通/钢铁/风暴)\n"
            "- /wf invasions|入侵\n"
            "- /wf cycle|循环\n"
            "- /wf void|虚空商人/奸商\n"
            f"image_mode={self.img_cfg.enabled} cache_images={self.img_cfg.cache_images}"
        )
        async for r in self._send_text_or_image(event, title="/wf help", text=msg):
            yield r

    @wf_group.command("refresh", alias={"update", "刷新", "更新"})
    async def wf_refresh2(self, event: AstrMessageEvent):
        await self.mgr.refresh_all_once()
        yield event.plain_result("ok")

    @wf_group.command("status", alias={"info", "状态"})
    async def wf_status(self, event: AstrMessageEvent):
        meta = self.mgr.get_worldstate_meta()
        if not meta:
            yield event.plain_result("no cache yet, use /wf refresh")
            return
        msg = "worldstate: status={status} fetched_at={fetched_at}".format(
            status=meta.get("status"), fetched_at=meta.get("fetched_at")
        )
        yield event.plain_result(msg)

    @wf_group.command("alerts", alias={"alert", "警报"})
    async def wf_alerts(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf refresh")
            return
        nodes = build_nodes_map(self.mgr.get_mirror_cached("nodes"))
        msg = format_alerts(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="Alerts", text=msg):
            yield r

    @wf_group.command("fissure", alias={"fissures", "裂隙"})
    async def wf_fissure(self, event: AstrMessageEvent, kind: str = "normal"):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf refresh")
            return
        nodes = build_nodes_map(self.mgr.get_mirror_cached("nodes"))
        k = self._normalize_fissure_kind(kind)
        msg = format_fissures(ws, nodes_map=nodes, kind=k)
        async for r in self._send_text_or_image(event, title=f"Fissures({k})", text=msg):
            yield r

    @wf_group.command("invasions", alias={"invasion", "入侵"})
    async def wf_invasions(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf refresh")
            return
        nodes = build_nodes_map(self.mgr.get_mirror_cached("nodes"))
        msg = format_invasions(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="Invasions", text=msg):
            yield r

    @wf_group.command("cycle", alias={"cycles", "循环", "轮换"})
    async def wf_cycle(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf refresh")
            return
        msg = format_cycles(ws)
        async for r in self._send_text_or_image(event, title="Cycles", text=msg):
            yield r

    @wf_group.command("void", alias={"baro", "奸商", "虚空商人"})
    async def wf_void(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf refresh")
            return
        msg = format_void_trader(ws)
        async for r in self._send_text_or_image(event, title="Void Trader", text=msg):
            yield r

    @filter.command("wf_stop", alias={"wf停止"})
    async def wf_stop(self, event: AstrMessageEvent):
        await self.mgr.stop_async()
        yield event.plain_result("stopped")

