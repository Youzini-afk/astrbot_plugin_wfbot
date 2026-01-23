from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api.all import *  # type: ignore
from astrbot.api.event import filter as afilter  # type: ignore
import astrbot.api.message_components as Comp

from .wf_config import WarframeConfig
from .wf_datasource import WarframeDataSource
from .wf_format import (
    build_nodes_map,
    format_alerts,
    format_cycles,
    format_daily_deals,
    format_duviri_cycle,
    format_fissures,
    format_invasions,
    format_nightwave,
    format_sortie,
    format_steel_path,
    format_void_trader,
    format_arbitration,
    format_archon_hunt,
    mission_emoji,
    translate_fissure_tier,
    translate_mission_type,
)
from .wf_render import ImageRenderConfig, get_or_render_png
from .wf_subscriptions import SubscriptionStore
from .wf_i18n import to_simplified_zh


PLUGIN_ID = "astrbot_plugin_wfbot"
logger = logging.getLogger(__name__)


@register("astrbot_plugin_wfbot", "Youzini", "Warframe 数据层（世界状态/PublicExport/多镜像/market），支持缓存、清理与定时刷新", "0.1.0")
class WarframeDatasourcePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = self._resolve_plugin_data_dir() / "warframe"

        refresh_cfg = self.config.get("refresh", {})
        if not isinstance(refresh_cfg, dict):
            refresh_cfg = {}

        retention_cfg = self.config.get("retention", {})
        if not isinstance(retention_cfg, dict):
            retention_cfg = {}

        render_cfg = self.config.get("render", {})
        if not isinstance(render_cfg, dict):
            render_cfg = {}

        subs_cfg = self.config.get("subscriptions", {})
        if not isinstance(subs_cfg, dict):
            subs_cfg = {}

        i18n_cfg = self.config.get("i18n", {})
        if not isinstance(i18n_cfg, dict):
            i18n_cfg = {}

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
            width=int(render_cfg.get("width", 1080)),
            font_size=int(render_cfg.get("font_size", 32)),
            title_font_size=int(render_cfg.get("title_font_size", 40)),
            pad=int(render_cfg.get("pad", 28)),
            line_gap=int(render_cfg.get("line_gap", 10)),
            font_cjk=(str(render_cfg.get("font_cjk")) if render_cfg.get("font_cjk") else None),
            font_emoji=(str(render_cfg.get("font_emoji")) if render_cfg.get("font_emoji") else None),
            emoji_mode=str(render_cfg.get("emoji_mode", "replace") or "replace"),
        )
        self.img_dir = data_dir / "images"

        self.ds = WarframeDataSource.create(wf_cfg)
        self.mgr = self.ds.create_manager()

        self._cycles_default_offset_seconds = int(subs_cfg.get("cycles_default_offset_minutes", 0)) * 60
        self._simplify_zh = bool(i18n_cfg.get("simplify_zh", True))

        self._sub_lock = asyncio.Lock()
        self._sub_store = SubscriptionStore(data_dir / "subscriptions.json")
        self.mgr.events.on("worldstate.updated", self._on_worldstate_updated)
        self._sub_tick_task: asyncio.Task | None = None
        self._cycle_mem: dict[str, dict[str, float | str]] = {}

        self._task: asyncio.Task | None = None
        try:
            self._task = asyncio.create_task(self._start_background())
        except RuntimeError:
            self._task = None

    def _resolve_plugin_data_dir(self) -> Path:
        # Prefer official path helper (AstrBot docs): data/plugin_data/<plugin_name>/
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path  # type: ignore

            plugin_name = str(getattr(self, "name", PLUGIN_ID) or PLUGIN_ID)
            return Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
        except Exception:
            pass

        # Newer AstrBot may provide context.get_data_dir() (or Star.get_data_dir()).
        get_data_dir = getattr(self.context, "get_data_dir", None)
        if callable(get_data_dir):
            try:
                return Path(str(get_data_dir()))
            except Exception:
                pass

        get_plugin_data_dir = getattr(self.context, "get_plugin_data_dir", None)
        if callable(get_plugin_data_dir):
            try:
                return Path(str(get_plugin_data_dir(PLUGIN_ID)))
            except Exception:
                pass

        star_get_data_dir = getattr(self, "get_data_dir", None)
        if callable(star_get_data_dir):
            try:
                return Path(str(star_get_data_dir()))
            except Exception:
                pass

        # Fallback: infer from plugin location: .../data/plugins/<plugin_id>/main.py
        here = Path(__file__).resolve()
        for p in here.parents:
            if p.name == "plugins" and p.parent.name == "data":
                return p.parent / "plugin_data" / PLUGIN_ID

        # Last resort: relative to CWD (best effort).
        return Path.cwd() / "data" / "plugin_data" / PLUGIN_ID

    async def _send_text_or_image(self, event: AstrMessageEvent, *, title: str, text: str):
        if not self.img_cfg.enabled:
            yield event.chain_result([Comp.Plain("\u200b" + text)])
            return

        path = get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            yield event.chain_result([Comp.Plain("\u200b" + text)])
            return

        yield event.chain_result([Comp.Image.fromFileSystem(str(path))])

    async def _push_plain_text(self, unified_msg_origin: str, text: str) -> bool:
        try:
            from astrbot.api.event import MessageChain

            await self.context.send_message(unified_msg_origin, MessageChain().message("\u200b" + text))
            return True
        except Exception:
            try:
                await self.context.send_message(unified_msg_origin, [Comp.Plain("\u200b" + text)])
                return True
            except Exception:
                return False

    def _try_build_at(self, user_id: str):
        try:
            if str(user_id).isdigit():
                return Comp.At(qq=int(str(user_id)))
            return Comp.At(qq=user_id)
        except Exception:
            return None

    def _cq_at_code(self, user_id: str) -> str | None:
        s = str(user_id)
        if not s.isdigit():
            return None
        return f"[CQ:at,qq={s}]"

    async def _push_with_mention(self, unified_msg_origin: str, *, platform: str | None, user_id: str | None, comps: list) -> bool:
        base = list(comps)
        if user_id:
            if platform == "aiocqhttp":
                cq = self._cq_at_code(user_id)
                if cq is not None:
                    with_at = [Comp.Plain(cq + "\u200b")] + base
                    try:
                        await self.context.send_message(unified_msg_origin, with_at)
                        return True
                    except Exception:
                        pass

            at = self._try_build_at(user_id)
            if at is not None:
                with_at = [at, Comp.Plain("\u200b")] + base
                try:
                    await self.context.send_message(unified_msg_origin, with_at)
                    return True
                except Exception:
                    pass

        try:
            await self.context.send_message(unified_msg_origin, base)
            return True
        except Exception:
            return False

    async def _push_text_or_image(self, unified_msg_origin: str, *, title: str, text: str) -> bool:
        if not self.img_cfg.enabled:
            return await self._push_plain_text(unified_msg_origin, text)

        path = get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            return await self._push_plain_text(unified_msg_origin, text)

        try:
            await self.context.send_message(unified_msg_origin, [Comp.Image.fromFileSystem(str(path))])
            return True
        except Exception:
            return await self._push_plain_text(unified_msg_origin, text)

    async def _push_to_subscriber(self, unified_msg_origin: str, *, platform: str | None, user_id: str | None, title: str, text: str) -> bool:
        if not self.img_cfg.enabled:
            return await self._push_with_mention(unified_msg_origin, platform=platform, user_id=user_id, comps=[Comp.Plain("\u200b" + text)])

        path = get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            return await self._push_with_mention(unified_msg_origin, platform=platform, user_id=user_id, comps=[Comp.Plain("\u200b" + text)])
        return await self._push_with_mention(unified_msg_origin, platform=platform, user_id=user_id, comps=[Comp.Image.fromFileSystem(str(path))])

    async def _start_background(self) -> None:
        await self.mgr.refresh_all_once()
        await self.mgr.start_async()
        if self._sub_tick_task is None or self._sub_tick_task.done():
            self._sub_tick_task = asyncio.create_task(self._subscription_tick_loop(), name="wf-subscription-tick")

    def _maybe_start_background(self) -> None:
        if self._task and not self._task.done():
            return
        try:
            self._task = asyncio.create_task(self._start_background())
        except RuntimeError:
            self._task = None

    async def _ensure_worldstate(self) -> dict | None:
        ws = self.mgr.get_worldstate_cached()
        if ws is None:
            await self.mgr.refresh_worldstate(snapshot=False)
            ws = self.mgr.get_worldstate_cached()
        return ws if isinstance(ws, dict) else None

    def _build_nodes_map(self) -> dict[str, str]:
        nodes = build_nodes_map(self.mgr.get_mirror_cached("nodes"))
        sol = build_nodes_map(self.mgr.get_mirror_cached("solnodes"))
        if sol:
            nodes.update(sol)
        if self._simplify_zh and nodes:
            nodes = {k: to_simplified_zh(v) for k, v in nodes.items() if isinstance(v, str)}
        return nodes

    def _build_cycles_ws(self) -> dict[str, dict]:
        def get(name: str) -> dict | None:
            v = self.mgr.get_mirror_cached(name)
            return v if isinstance(v, dict) else None

        out: dict[str, dict] = {}
        earth = get("cycle_earth")
        cetus = get("cycle_cetus")
        vallis = get("cycle_vallis")
        cambion = get("cycle_cambion")
        zariman = get("cycle_zariman")
        duviri = get("cycle_duviri")
        if earth:
            out["earthCycle"] = earth
        if cetus:
            out["cetusCycle"] = cetus
        if vallis:
            out["vallisCycle"] = vallis
        if cambion:
            out["cambionCycle"] = cambion
        if zariman:
            out["zarimanCycle"] = zariman
        if duviri:
            # keep both keys for compatibility
            out["duviriCycle"] = duviri
            out["duvalierCycle"] = duviri
        return out

    def _parse_topic_id(self, topic_id: str) -> tuple[str, dict[str, str]]:
        if "|" not in topic_id:
            if topic_id.startswith("fissures."):
                kind = topic_id.split(".", 1)[1] or "normal"
                return "fissures", {"kind": kind}
            return topic_id, {}

        base, rest = topic_id.split("|", 1)
        base = base.strip()
        filters: dict[str, str] = {}
        for part in rest.split(";"):
            if not part.strip() or "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k and v:
                filters[k] = v
        return base, filters

    def _make_topic_id(self, base: str, filters: dict[str, str]) -> str:
        base = base.strip()
        if base == "fissures":
            kind = (filters.get("kind") or "normal").strip().lower()
            rest = {k: v for k, v in filters.items() if k != "kind" and v}
            if not rest:
                return f"fissures.{kind}"
            parts = [f"kind={kind}"] + [f"{k}={rest[k]}" for k in sorted(rest.keys())]
            return base + "|" + ";".join(parts)
        if not filters:
            return base
        parts = [f"{k}={filters[k]}" for k in sorted(filters.keys()) if filters[k]]
        return base + "|" + ";".join(parts)

    def _norm_token(self, s: str) -> str:
        return (s or "").strip().lower().replace(" ", "")

    def _normalize_fissure_kind_token(self, raw: str) -> str | None:
        s = self._norm_token(raw)
        if s in {"普通", "普通裂隙", "普通裂缝", "normal", "n"}:
            return "normal"
        if s in {"钢铁", "钢铁之路", "钢铁裂隙", "钢铁裂缝", "steel", "sp", "steelpath"}:
            return "steel"
        if s in {"九重天", "九重天裂隙", "九重天裂缝", "风暴", "虚空风暴", "storm", "voidstorm", "railjack"}:
            return "storm"
        return None

    def _normalize_relic_tier_token(self, raw: str) -> str | None:
        s = self._norm_token(raw)
        if s in {"古纪", "lith"}:
            return "lith"
        if s in {"中纪", "meso"}:
            return "meso"
        if s in {"新纪", "neo"}:
            return "neo"
        if s in {"后纪", "axi"}:
            return "axi"
        if s in {"安魂", "安魂遗物", "requiem"}:
            return "requiem"
        return None

    def _normalize_mission_type_token(self, raw: str) -> str | None:
        s = self._norm_token(raw)
        mapping = {
            "defense": {"防御", "defense"},
            "mobiledefense": {"移动防御", "机动防御", "移动", "mobiledefense", "mobile_defense"},
            "capture": {"捕获", "capture"},
            "survival": {"生存", "survival"},
            "exterminate": {"歼灭", "exterminate"},
            "interception": {"拦截", "interception"},
            "spy": {"间谍", "spy"},
            "rescue": {"救援", "rescue"},
            "sabotage": {"破坏", "sabotage"},
            "excavation": {"挖掘", "excavation"},
            "disruption": {"扰乱", "disruption"},
        }
        for key, names in mapping.items():
            if s in {self._norm_token(x) for x in names}:
                return key
        return None

    def _mission_key_from_code(self, code) -> str:
        s = str(code or "").strip()
        if not s:
            return ""
        s = s.upper()
        if s.startswith("MT_"):
            s = s[3:]
        s = re.sub(r"[^A-Z0-9]+", "", s)
        return s.lower()

    def _tier_key_from_code(self, code) -> str:
        s = str(code or "").strip()
        if not s:
            return ""
        s0 = s.strip().lower().replace(" ", "").replace("_", "")
        if s0 in {"lith", "meso", "neo", "axi", "requiem"}:
            return s0
        if s0 in {"voidt1"}:
            return "lith"
        if s0 in {"voidt2"}:
            return "meso"
        if s0 in {"voidt3"}:
            return "neo"
        if s0 in {"voidt4"}:
            return "axi"
        if s0 in {"voidt5"}:
            return "requiem"
        # VoidT6 (Omnia) / other values: keep raw normalized for non-filtered display only.
        return s0

    def _normalize_cycle_zone_token(self, raw: str) -> str | None:
        s = self._norm_token(raw)
        if s in {"夜灵平原", "夜灵平野", "cetus", "plains"}:
            return "cetus"
        if s in {"地球", "earth"}:
            return "earth"
        if s in {"福尔图娜", "金星平原", "vallis", "orbvallis", "orb"}:
            return "vallis"
        if s in {"魔胎之境", "殁世幽都", "cambion", "cambiondrift"}:
            return "cambion"
        if s in {"扎里曼", "zariman"}:
            return "zariman"
        return None

    def _normalize_cycle_state_token(self, raw: str, *, zone: str | None) -> str | None:
        s = self._norm_token(raw)
        if s in {"白天", "day"}:
            return "day"
        if s in {"夜晚", "夜", "night"}:
            return "night"
        if zone == "vallis":
            if s in {"温暖", "暖", "warm"}:
                return "warm"
            if s in {"寒冷", "冷", "cold"}:
                return "cold"
        if zone == "cambion":
            if s in {"fass"}:
                return "fass"
            if s in {"vome"}:
                return "vome"
        return None

    def _parse_lead_seconds_token(self, raw: str) -> int | None:
        s = (raw or "").strip().lower().replace(" ", "")
        if not s:
            return None
        if s.startswith("后置") or s.startswith("延后") or s.startswith("滞后") or s.startswith("after"):
            s = s.replace("后置", "", 1).replace("延后", "", 1).replace("滞后", "", 1).replace("after", "", 1)
            m = re.fullmatch(r"(\d{1,4})(?:m|分钟)?", s)
            if not m:
                return None
            minutes = int(m.group(1))
            if minutes <= 0:
                return None
            return -int(minutes * 60)

        m = re.fullmatch(r"(?:提前)?(-?\d{1,4})(?:m|分钟)?", s)
        if not m:
            return None
        minutes = int(m.group(1))
        if minutes == 0:
            return None
        return int(minutes * 60)

    def _normalize_sub_topic_args(self, topic: str | None, *mods: str) -> str | None:
        t0 = self._norm_token(topic or "")
        args = [m for m in mods if (m or "").strip()]
        if not t0:
            return None

        # non-fissure topics (single token)
        if t0 in {"警报", "alert", "alerts"}:
            return "alerts"
        if t0 in {"入侵", "invasion", "invasions"}:
            return "invasions"
        if t0 in {"奸商", "虚空商人", "baro", "void"}:
            return "void_trader"
        if t0 in {"每日特惠", "特惠", "daily", "dailydeals"}:
            return "daily_deals"
        if t0 in {"突击", "sortie"}:
            return "sortie"
        if t0 in {"执刑官猎杀", "执刑官", "执行官", "执政官", "archon", "猎杀"}:
            return "archon"
        if t0 in {"仲裁", "arbitration"}:
            return "arbitration"
        if t0 in {"钢铁奖励", "steelreward", "steel_path", "steelpathreward"}:
            return "steel_path"
        if t0 in {"平原", "循环", "cycles", "cetus", "vallis", "cambion", "zariman", "夜灵平原", "夜灵平野", "地球", "福尔图娜", "魔胎之境", "扎里曼"}:
            base = "cycles"
            zone = self._normalize_cycle_zone_token(topic or "") or "cetus"
            state: str | None = None
            lead_seconds: int | None = None
            for a in args:
                z = self._normalize_cycle_zone_token(a)
                if z:
                    zone = z
                    continue
                st = self._normalize_cycle_state_token(a, zone=zone)
                if st:
                    state = st
                    continue
                ls = self._parse_lead_seconds_token(a)
                if ls is not None:
                    lead_seconds = ls
                    continue
                return None
            filters: dict[str, str] = {"zone": zone}
            if state:
                filters["state"] = state
            if lead_seconds is None and state and self._cycles_default_offset_seconds != 0:
                lead_seconds = self._cycles_default_offset_seconds
            if lead_seconds is not None and state:
                filters["lead"] = str(int(lead_seconds))
            return self._make_topic_id(base, filters)
        if t0 in {"轮换", "双衍王境", "duviri"}:
            return "duviri"
        if t0 in {"电波", "nightwave"}:
            return "nightwave"

        # fissures with optional filters
        if t0 in {"裂隙", "裂缝", "fissure", "fissures"}:
            base = "fissures"
            filters: dict[str, str] = {"kind": "normal"}
            for a in args:
                k = self._normalize_fissure_kind_token(a)
                if k:
                    filters["kind"] = k
                    continue
                tier = self._normalize_relic_tier_token(a)
                if tier:
                    filters["tier"] = tier
                    continue
                mt = self._normalize_mission_type_token(a)
                if mt:
                    filters["mission"] = mt
                    continue
                return None
            return self._make_topic_id(base, filters)

        # allow direct kind command words as topic
        kind = self._normalize_fissure_kind_token(topic or "")
        if kind:
            filters = {"kind": kind}
            for a in args:
                tier = self._normalize_relic_tier_token(a)
                if tier:
                    filters["tier"] = tier
                    continue
                mt = self._normalize_mission_type_token(a)
                if mt:
                    filters["mission"] = mt
                    continue
                return None
            return self._make_topic_id("fissures", filters)

        return None

    def _topic_label(self, topic: str) -> str:
        base, filters = self._parse_topic_id(topic)
        base_label = {
            "alerts": "警报",
            "invasions": "入侵",
            "fissures": "裂隙",
            "void_trader": "奸商",
            "daily_deals": "每日特惠",
            "sortie": "突击",
            "archon": "执刑官猎杀",
            "arbitration": "仲裁",
            "steel_path": "钢铁奖励",
            "cycles": "循环",
            "duviri": "轮换",
            "nightwave": "电波",
        }.get(base, base)

        if base != "fissures":
            if base == "cycles":
                zone = (filters.get("zone") or "").lower()
                state = (filters.get("state") or "").lower()
                zone_label = {
                    "cetus": "夜灵平原",
                    "earth": "地球",
                    "vallis": "福尔图娜",
                    "cambion": "魔胎之境",
                    "zariman": "扎里曼",
                }.get(zone, "平原")
                state_label = {
                    "day": "白天",
                    "night": "夜晚",
                    "warm": "温暖",
                    "cold": "寒冷",
                    "fass": "Fass",
                    "vome": "Vome",
                }.get(state, "")
                lead = filters.get("lead")
                lead_label = ""
                if lead:
                    try:
                        mins = int(int(lead) / 60)
                    except Exception:
                        mins = 0
                    if mins > 0:
                        lead_label = f" 提前{mins}分钟"
                    elif mins < 0:
                        lead_label = f" 后置{abs(mins)}分钟"
                return zone_label + (f" {state_label}" if state_label else "") + lead_label
            return base_label

        kind = (filters.get("kind") or "normal").lower()
        if kind == "steel":
            base_label = "钢铁裂隙"
        elif kind == "storm":
            base_label = "九重天裂隙"
        else:
            base_label = "裂隙"

        extra: list[str] = []
        tier = (filters.get("tier") or "").lower()
        if tier:
            extra.append({"lith": "古纪", "meso": "中纪", "neo": "新纪", "axi": "后纪", "requiem": "安魂"}.get(tier, tier))
        mission = (filters.get("mission") or "").lower()
        if mission:
            extra.append(
                {
                    "defense": "防御",
                    "mobiledefense": "移动防御",
                    "capture": "捕获",
                    "survival": "生存",
                    "exterminate": "歼灭",
                    "interception": "拦截",
                    "spy": "间谍",
                    "rescue": "救援",
                    "sabotage": "破坏",
                    "excavation": "挖掘",
                    "disruption": "扰乱",
                }.get(mission, mission)
            )
        return base_label + (" " + " ".join(extra) if extra else "")

    def _topic_text(self, topic: str, ws: dict) -> tuple[str, str]:
        nodes = self._build_nodes_map()
        base, filters = self._parse_topic_id(topic)

        if base == "alerts":
            return "警报", format_alerts(ws, nodes_map=nodes)
        if base == "invasions":
            return "入侵", format_invasions(ws, nodes_map=nodes)
        if base == "fissures":
            kind = (filters.get("kind") or "normal").lower()
            tier = (filters.get("tier") or "").lower() or None
            mission = (filters.get("mission") or "").lower() or None
            title = self._topic_label(topic)
            msg = self._format_fissures_filtered(ws, nodes_map=nodes, kind=kind, tier=tier, mission=mission)
            return title, msg
        if base == "void_trader":
            return "奸商", format_void_trader(ws)
        if base == "daily_deals":
            return "每日特惠", format_daily_deals(ws)
        if base == "sortie":
            return "突击", format_sortie(ws, nodes_map=nodes)
        if base == "archon":
            return "执刑官猎杀", format_archon_hunt(ws, nodes_map=nodes)
        if base == "arbitration":
            return "仲裁", format_arbitration(ws, nodes_map=nodes)
        if base == "steel_path":
            return "钢铁奖励", format_steel_path(ws)
        if base == "cycles":
            zone = (filters.get("zone") or "cetus").lower()
            desired = (filters.get("state") or "").lower() or None
            title = self._topic_label(topic) or "循环"
            ws2 = dict(ws)
            ws2.update(self._build_cycles_ws())
            msg = self._format_cycles_filtered(ws2, zone=zone, desired_state=desired)
            return title, msg
        if base == "duviri":
            ws2 = dict(ws)
            ws2.update(self._build_cycles_ws())
            return "轮换", format_duviri_cycle(ws2)
        if base == "nightwave":
            return "电波", format_nightwave(ws)
        return self._topic_label(topic), f"{topic}: -"

    def _format_fissures_filtered(
        self,
        ws: dict,
        *,
        nodes_map: dict[str, str] | None,
        kind: str,
        tier: str | None,
        mission: str | None,
        limit: int = 10,
    ) -> str:
        if kind == "storm":
            fiss = ws.get("voidStorms", [])
        else:
            fiss = ws.get("activeMissions", [])

        if not isinstance(fiss, list):
            return "fissures: -"

        out = []
        for m in fiss:
            if not isinstance(m, dict):
                continue
            hard = m.get("hard")
            if kind == "steel" and hard is not True:
                continue
            if kind == "normal" and hard is True:
                continue

            if tier:
                t_code = m.get("modifier") or m.get("tier") or ""
                t_key = self._tier_key_from_code(t_code)
                if not t_key or t_key != tier.lower():
                    continue

            if mission:
                mt_code = m.get("missionType") or m.get("MissionType") or ""
                mt_key = self._mission_key_from_code(mt_code)
                if not mt_key or mt_key != mission.lower():
                    continue

            out.append(m)

        title = {"normal": "普通", "steel": "钢铁", "storm": "九重天"}.get(kind, kind)
        filter_bits: list[str] = []
        if mission:
            filter_bits.append(
                {
                    "defense": "防御",
                    "mobiledefense": "移动防御",
                    "capture": "捕获",
                    "survival": "生存",
                    "exterminate": "歼灭",
                    "interception": "拦截",
                    "spy": "间谍",
                    "rescue": "救援",
                    "sabotage": "破坏",
                    "excavation": "挖掘",
                    "disruption": "扰乱",
                }.get(mission, mission)
            )
        if tier:
            filter_bits.append({"lith": "古纪", "meso": "中纪", "neo": "新纪", "axi": "后纪", "requiem": "安魂"}.get(tier, tier))
        head = f"🌀 裂缝·{title}（{len(out)}）"
        if filter_bits:
            head = head + "｜过滤：" + " ".join(filter_bits)
        lines = [head]
        if not out:
            lines.append("- 暂无符合条件的裂缝")
            return "\n".join(lines)
        for m in out[:limit]:
            node = str(m.get("node") or m.get("location") or "-")
            if nodes_map and node in nodes_map:
                node = nodes_map[node]
            tier_code = m.get("modifier") or m.get("tier")
            tier_zh = translate_fissure_tier(tier_code)
            mtype_code = m.get("missionType") or m.get("MissionType")
            mtype_zh = translate_mission_type(mtype_code)
            em = mission_emoji(mtype_code)
            eta = self._format_remaining(m.get("expiry"))
            lines.append(f"- {node}｜{em}{mtype_zh}｜{tier_zh}｜⏳{eta}")
        return "\n".join(lines)

    def _format_remaining(self, expiry) -> str:
        ts = self._parse_expiry_ts(expiry)
        if ts is None:
            return "-"
        now_ts = datetime.now(timezone.utc).timestamp()
        secs = ts - now_ts
        if secs <= 0:
            return "已结束"
        minutes = int(secs // 60)
        if minutes < 60:
            return f"{minutes}分"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}小时{minutes % 60}分"
        days = hours // 24
        return f"{days}天{hours % 24}小时"

    def _format_cycles_filtered(self, ws: dict, *, zone: str, desired_state: str | None) -> str:
        key = {
            "earth": "earthCycle",
            "cetus": "cetusCycle",
            "vallis": "vallisCycle",
            "cambion": "cambionCycle",
            "zariman": "zarimanCycle",
        }.get(zone, "cetusCycle")

        obj = ws.get(key)
        if not isinstance(obj, dict):
            return f"{key}: -"

        def state_of(o: dict) -> str:
            st = o.get("state")
            if isinstance(st, str) and st:
                return st.strip().lower()
            if key in {"earthCycle", "cetusCycle"} and "isDay" in o:
                return "day" if bool(o.get("isDay")) else "night"
            if key == "vallisCycle" and "isWarm" in o:
                return "warm" if bool(o.get("isWarm")) else "cold"
            return "-"

        st = state_of(obj)
        exp_raw = obj.get("expiry") or obj.get("endTime")
        exp_ts = self._parse_expiry_ts(exp_raw)
        now_ts = datetime.now(timezone.utc).timestamp()
        delta = (exp_ts - now_ts) if exp_ts is not None else None

        def fmt_delta(d: float | None) -> str:
            if d is None:
                tl = obj.get("timeLeft")
                return str(tl) if tl else "-"
            if d <= 0:
                return "已结束"
            minutes = int(d // 60)
            if minutes < 60:
                return f"{minutes}分"
            hours = minutes // 60
            if hours < 24:
                return f"{hours}小时{minutes % 60}分"
            days = hours // 24
            return f"{days}天{hours % 24}小时"

        def zone_title(z: str) -> str:
            return {
                "earth": "🌍 地球",
                "cetus": "🌾 夜灵平原",
                "vallis": "❄️ 福尔图娜",
                "cambion": "🦠 魔胎之境",
                "zariman": "🚢 扎里曼",
            }.get(z, z)

        def state_label(z: str, s: str) -> str:
            if z in {"earth", "cetus"}:
                return "☀️白天" if s == "day" else "🌙夜晚" if s == "night" else s
            if z == "vallis":
                return "🔥温暖" if s == "warm" else "❄️寒冷" if s == "cold" else s
            if z == "cambion":
                return "🟥Fass" if s == "fass" else "🟦Vome" if s == "vome" else s
            if z == "zariman":
                return f"⚔️{s}" if s else s
            return s

        title = zone_title(zone)
        cur = state_label(zone, st)

        if not desired_state:
            return f"{title}｜当前：{cur}｜⏳{fmt_delta(delta)}"

        des = state_label(zone, desired_state)
        if st == desired_state:
            return f"{title}｜当前：{cur}（已达成）"

        return f"{title}｜当前：{cur}｜距离{des}：⏳{fmt_delta(delta)}"

    def _parse_expiry_ts(self, expiry) -> float | None:
        if expiry is None:
            return None
        if isinstance(expiry, (int, float)):
            v = float(expiry)
            if v > 1e12:
                v = v / 1000.0
            return v
        if isinstance(expiry, str):
            s = expiry.strip()
            if not s:
                return None
            s = s.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(s).timestamp()
            except Exception:
                return None
        return None

    def _topic_should_notify(self, topic: str, ws: dict) -> bool:
        base, filters = self._parse_topic_id(topic)
        if base != "cycles":
            return True
        desired_state = (filters.get("state") or "").lower()
        if not desired_state:
            return True
        zone = (filters.get("zone") or "cetus").lower()
        key = {
            "earth": "earthCycle",
            "cetus": "cetusCycle",
            "vallis": "vallisCycle",
            "cambion": "cambionCycle",
            "zariman": "zarimanCycle",
        }.get(zone, "cetusCycle")
        obj = ws.get(key)
        if not isinstance(obj, dict):
            return False

        st = obj.get("state")
        if isinstance(st, str) and st:
            cur = st.strip().lower()
        elif "isDay" in obj:
            cur = "day" if bool(obj.get("isDay")) else "night"
        elif "isWarm" in obj:
            cur = "warm" if bool(obj.get("isWarm")) else "cold"
        else:
            cur = ""
        return cur == desired_state

    def _cycles_pre_reminder(self, topic: str, ws: dict) -> tuple[str, str, str] | None:
        base, filters = self._parse_topic_id(topic)
        if base != "cycles":
            return None
        desired_state = (filters.get("state") or "").lower()
        if not desired_state:
            return None
        lead_s = filters.get("lead")
        if not lead_s:
            return None
        try:
            lead = int(str(lead_s))
        except Exception:
            return None
        if lead == 0:
            return None

        zone = (filters.get("zone") or "cetus").lower()
        key = {
            "earth": "earthCycle",
            "cetus": "cetusCycle",
            "vallis": "vallisCycle",
            "cambion": "cambionCycle",
            "zariman": "zarimanCycle",
        }.get(zone, "cetusCycle")
        obj = ws.get(key)
        if not isinstance(obj, dict):
            return None

        # determine current state and next switch time (expiry of current)
        st = obj.get("state")
        if isinstance(st, str) and st:
            cur = st.strip().lower()
        elif "isDay" in obj:
            cur = "day" if bool(obj.get("isDay")) else "night"
        elif "isWarm" in obj:
            cur = "warm" if bool(obj.get("isWarm")) else "cold"
        else:
            cur = ""

        exp_ts = self._parse_expiry_ts(obj.get("expiry") or obj.get("endTime"))
        if exp_ts is None:
            return None
        now_ts = datetime.now(timezone.utc).timestamp()

        mem = self._cycle_mem.get(key)
        if mem is None:
            mem = {"state": cur, "changed_at": now_ts, "expiry": exp_ts}
            self._cycle_mem[key] = mem
        else:
            if str(mem.get("state")) != cur:
                mem["state"] = cur
                mem["changed_at"] = now_ts
            mem["expiry"] = exp_ts

        if lead > 0:
            # pre: before switching into desired state
            if cur == desired_state:
                return None
            delta = exp_ts - now_ts
            if delta <= 0 or delta > lead:
                return None
            mins = max(0, int(delta // 60))
            title = self._topic_label(topic)
            text = f"【预提醒】{title} 将在约 {mins} 分钟后开始"
            pre_sig = hashlib.sha256(json.dumps({"k": key, "desired": desired_state, "lead": lead, "expiry": exp_ts}, sort_keys=True).encode("utf-8")).hexdigest()
            return title, text, pre_sig

        # post: after switching into desired state
        if cur != desired_state:
            return None
        changed_at = float(mem.get("changed_at") or now_ts)
        passed = now_ts - changed_at
        target = abs(lead)
        if passed < target or passed > target + 35:
            return None
        title = self._topic_label(topic)
        mins = max(0, int(target // 60))
        text = f"【预提醒】{title} 已开始（后置{mins}分钟提醒）"
        pre_sig = hashlib.sha256(json.dumps({"k": key, "desired": desired_state, "lead": lead, "changed_at": int(changed_at)}, sort_keys=True).encode("utf-8")).hexdigest()
        return title, text, pre_sig

    async def _subscription_tick_loop(self) -> None:
        while True:
            try:
                await self._run_pre_reminders_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("wf subscription tick loop error")
            await asyncio.sleep(30.0)

    async def _run_pre_reminders_once(self) -> None:
        ws = self.mgr.get_worldstate_cached()
        if not isinstance(ws, dict):
            ws = {}
        ws = dict(ws)
        ws.update(self._build_cycles_ws())

        async with self._sub_lock:
            data = self._sub_store.load()

        items = data.get("items")
        if not isinstance(items, list) or not items:
            return

        to_send: list[tuple[str, str | None, str | None, str, str, str]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            umo = it.get("umo")
            uid = it.get("uid")
            platform = it.get("platform")
            topics = it.get("topics")
            if not isinstance(umo, str) or not umo or not isinstance(topics, dict):
                continue
            if uid is not None and not isinstance(uid, str):
                uid = None
            if platform is not None and not isinstance(platform, str):
                platform = None
            for t, meta in topics.items():
                if not isinstance(t, str) or not isinstance(meta, dict):
                    continue
                pr = self._cycles_pre_reminder(t, ws)
                if pr is None:
                    continue
                title, text, pre_sig = pr
                if meta.get("last_pre_sig") == pre_sig:
                    continue
                to_send.append((umo, platform, uid, t, title, text))

        if not to_send:
            return

        sent: list[tuple[str, str | None, str, str]] = []
        for umo, platform, uid, topic, title, text in to_send:
            ok = await self._push_to_subscriber(umo, platform=platform, user_id=uid, title=title, text=text)
            if ok:
                sent.append((umo, uid, topic, hashlib.sha256(text.encode("utf-8")).hexdigest()))

        if not sent:
            return

        async with self._sub_lock:
            data2 = self._sub_store.load()
            changed = False
            for umo, uid, topic, _ in sent:
                pr = self._cycles_pre_reminder(topic, ws)
                if pr is None:
                    continue
                _, _, pre_sig = pr
                changed = self._sub_store.set_last_pre_sig(data2, umo=umo, uid=uid, topic=topic, sig=pre_sig) or changed
            if changed:
                self._sub_store.save(data2)

    def _topic_sig(self, topic: str, ws: dict) -> str:
        def h(obj) -> str:
            raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            return hashlib.sha256(raw).hexdigest()

        def g(obj, *path, default=None):
            cur = obj
            for p in path:
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:
                    return default
            return cur if cur is not None else default

        base, filters = self._parse_topic_id(topic)

        if base == "alerts":
            alerts = g(ws, "alerts", default=[])
            if not isinstance(alerts, list):
                return h([])
            rows = []
            for a in alerts:
                if not isinstance(a, dict):
                    continue
                mi = a.get("missionInfo") or {}
                rows.append(
                    {
                        "id": a.get("id") or a.get("_id"),
                        "loc": g(mi, "location"),
                        "type": g(mi, "missionType") or g(mi, "missionTypeKey"),
                        "exp": a.get("expiry") or a.get("endTime"),
                    }
                )
            return h(sorted(rows, key=lambda x: str(x.get("id") or x.get("loc") or "")))

        if base == "fissures":
            kind = (filters.get("kind") or "normal").lower()
            tier = (filters.get("tier") or "").lower() or None
            mission = (filters.get("mission") or "").lower() or None
            fiss = g(ws, "voidStorms" if kind == "storm" else "activeMissions", default=[])
            if not isinstance(fiss, list):
                return h([])
            rows = []
            for m in fiss:
                if not isinstance(m, dict):
                    continue
                hard = m.get("hard")
                if kind == "steel" and hard is not True:
                    continue
                if kind == "normal" and hard is True:
                    continue
                if tier:
                    t_code = m.get("modifier") or m.get("tier") or ""
                    t_key = self._tier_key_from_code(t_code)
                    if not t_key or t_key != tier:
                        continue
                if mission:
                    mt_code = m.get("missionType") or m.get("MissionType") or ""
                    mt_key = self._mission_key_from_code(mt_code)
                    if not mt_key or mt_key != mission:
                        continue
                rows.append(
                    {
                        "id": m.get("id") or m.get("_id"),
                        "node": m.get("node") or m.get("location"),
                        "tier": m.get("modifier") or m.get("tier"),
                        "hard": bool(hard),
                        "exp": m.get("expiry"),
                    }
                )
            return h(sorted(rows, key=lambda x: str(x.get("id") or x.get("node") or "")))

        if base == "invasions":
            inv = g(ws, "invasions", default=[])
            if not isinstance(inv, list):
                return h([])
            rows = []
            for i in inv:
                if not isinstance(i, dict) or i.get("completed"):
                    continue
                rows.append(
                    {
                        "id": i.get("id") or i.get("_id"),
                        "node": i.get("node"),
                        "atk": i.get("attackingFaction"),
                        "def": i.get("defendingFaction"),
                        "exp": i.get("expiry"),
                    }
                )
            return h(sorted(rows, key=lambda x: str(x.get("id") or x.get("node") or "")))

        if base == "void_trader":
            v = ws.get("voidTrader")
            if not isinstance(v, dict):
                return h({})
            return h({"active": v.get("active"), "loc": v.get("location"), "exp": v.get("expiry")})

        if base == "daily_deals":
            deals = g(ws, "dailyDeals", default=[])
            if not isinstance(deals, list):
                return h([])
            rows = []
            for d in deals:
                if not isinstance(d, dict):
                    continue
                rows.append({"item": d.get("item"), "price": d.get("salePrice") or d.get("originalPrice"), "exp": d.get("expiry")})
            return h(sorted(rows, key=lambda x: str(x.get("item") or "")))

        if base == "sortie":
            s = ws.get("sortie")
            if not isinstance(s, dict):
                return h({})
            variants = s.get("variants") if isinstance(s.get("variants"), list) else []
            rows = []
            for v in variants:
                if not isinstance(v, dict):
                    continue
                rows.append({"node": v.get("node"), "type": v.get("missionType"), "mod": v.get("modifier")})
            return h({"boss": s.get("boss"), "exp": s.get("expiry"), "v": rows})

        if base == "archon":
            hunt = ws.get("liteSortie") or ws.get("archonHunt")
            if not isinstance(hunt, dict):
                return h({})
            missions = hunt.get("missions") or hunt.get("variants")
            if not isinstance(missions, list):
                missions = []
            rows = []
            for m in missions:
                if not isinstance(m, dict):
                    continue
                rows.append({"node": m.get("node") or m.get("location"), "type": m.get("missionType")})
            return h({"boss": hunt.get("boss") or hunt.get("bossName"), "exp": hunt.get("expiry"), "m": rows})

        if base == "arbitration":
            arb = ws.get("arbitration")
            if not isinstance(arb, dict):
                return h({})
            return h({"node": arb.get("node"), "type": arb.get("type") or arb.get("missionType"), "exp": arb.get("expiry")})

        if base == "steel_path":
            sp = ws.get("steelPath") or ws.get("steelPathOffering")
            if not isinstance(sp, dict):
                return h({})
            return h({"rot": sp.get("rotation") or sp.get("name"), "exp": sp.get("expiry")})

        if base == "cycles":
            zone = (filters.get("zone") or "").lower()
            if zone:
                k = {
                    "earth": "earthCycle",
                    "cetus": "cetusCycle",
                    "vallis": "vallisCycle",
                    "cambion": "cambionCycle",
                    "zariman": "zarimanCycle",
                }.get(zone, "cetusCycle")
                v = ws.get(k)
                if not isinstance(v, dict):
                    return h({})
                st = v.get("state")
                if not isinstance(st, str) or not st:
                    if "isDay" in v:
                        st = "day" if bool(v.get("isDay")) else "night"
                    elif "isWarm" in v:
                        st = "warm" if bool(v.get("isWarm")) else "cold"
                return h({"k": k, "state": st, "exp": v.get("expiry")})

            keys = ["earthCycle", "cetusCycle", "vallisCycle", "cambionCycle", "zarimanCycle", "duvalierCycle"]
            rows = []
            for k in keys:
                v = ws.get(k)
                if isinstance(v, dict):
                    rows.append({"k": k, "state": v.get("state") or v.get("id"), "exp": v.get("expiry")})
            return h(rows)

        if base == "duviri":
            d = ws.get("duvalierCycle")
            if not isinstance(d, dict):
                return h({})
            return h({"state": d.get("state") or d.get("id"), "exp": d.get("expiry")})

        if base == "nightwave":
            n = ws.get("seasonInfo") or ws.get("nightwave")
            if not isinstance(n, dict):
                return h({})
            return h({"tag": n.get("tag") or n.get("season"), "exp": n.get("expiry")})

        return h({})

    async def _on_worldstate_updated(self, payload: dict) -> None:
        ws = payload.get("new")
        if not isinstance(ws, dict):
            return
        ws2 = dict(ws)
        ws2.update(self._build_cycles_ws())

        async with self._sub_lock:
            data = self._sub_store.load()

        items = data.get("items")
        if not isinstance(items, list) or not items:
            return

        all_topics: set[str] = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            topics = it.get("topics")
            if isinstance(topics, dict):
                all_topics.update([k for k in topics.keys() if isinstance(k, str)])
        if not all_topics:
            return

        topic_payload: dict[str, tuple[str, str, str]] = {}
        for t in sorted(all_topics):
            title, text = self._topic_text(t, ws2)
            sig = self._topic_sig(t, ws2)
            topic_payload[t] = (title, text, sig)

        to_send: list[tuple[str, str | None, str | None, str, str, str, str | None]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            umo = it.get("umo")
            uid = it.get("uid")
            platform = it.get("platform")
            topics = it.get("topics")
            if not isinstance(umo, str) or not umo or not isinstance(topics, dict):
                continue
            if uid is not None and not isinstance(uid, str):
                uid = None
            if platform is not None and not isinstance(platform, str):
                platform = None
            for t, meta in topics.items():
                if not isinstance(t, str) or t not in topic_payload or not isinstance(meta, dict):
                    continue
                if not self._topic_should_notify(t, ws2):
                    continue
                last_sig = meta.get("last_sig")
                title, text, sig = topic_payload[t]
                if last_sig != sig:
                    to_send.append((umo, platform, uid, t, title, text, sig))

        if not to_send:
            return

        sent: list[tuple[str, str | None, str, str]] = []
        for umo, platform, uid, topic, title, text, sig in to_send:
            ok = await self._push_to_subscriber(umo, platform=platform, user_id=uid, title=title, text=text)
            if ok:
                sent.append((umo, uid, topic, sig))

        if not sent:
            return

        async with self._sub_lock:
            data2 = self._sub_store.load()
            changed = False
            for umo, uid, topic, sig in sent:
                changed = self._sub_store.set_last_sig(data2, umo=umo, uid=uid, topic=topic, sig=sig) or changed
            if changed:
                self._sub_store.save(data2)

    def _normalize_fissure_kind(self, kind: str | None) -> str:
        k = (kind or "normal").strip().lower()
        if k in {"normal", "n", "普通", "普通裂隙", "裂隙", "裂缝"}:
            return "normal"
        if k in {"steel", "sp", "钢铁", "钢铁之路", "钢铁裂隙", "钢铁裂缝"}:
            return "steel"
        if k in {"storm", "voidstorm", "railjack", "风暴", "虚空风暴", "九重天", "九重天裂隙", "九重天裂缝"}:
            return "storm"
        return "normal"

    # Back-compat shortcuts
    @afilter.command("wf_refresh", alias={"wf更新", "wf刷新"})
    async def wf_refresh_compat(self, event: AstrMessageEvent):
        await self.mgr.refresh_all_once()
        yield event.plain_result("ok")

    @afilter.command("wf_stop", alias={"wf停止"})
    async def wf_stop_compat(self, event: AstrMessageEvent):
        if self._sub_tick_task and not self._sub_tick_task.done():
            self._sub_tick_task.cancel()
        await self.mgr.stop_async()
        yield event.plain_result("stopped")

    # /wf group: align with NyxBot-style keywords as subcommands
    @afilter.command_group("wf", alias={"战甲", "星际战甲", "warframe"})
    def wf_group(self):
        pass

    @wf_group.command("停止", alias={"stop", "关闭", "停", "shutdown"})
    async def wf_stop(self, event: AstrMessageEvent):
        if self._sub_tick_task and not self._sub_tick_task.done():
            self._sub_tick_task.cancel()
        await self.mgr.stop_async()
        yield event.plain_result("stopped")

    @wf_group.command("清理图片缓存", alias={"清图", "清理图片", "清理缓存图片", "clear_images", "clear_image_cache"})
    async def wf_clear_images(self, event: AstrMessageEvent):
        try:
            folder = self.img_dir
            if not folder.exists():
                yield event.plain_result("图片缓存目录不存在（无需清理）")
                return
            files = [p for p in folder.glob("*.png") if p.is_file()]
            if not files:
                yield event.plain_result("图片缓存为空（无需清理）")
                return
            removed = 0
            for p in files:
                try:
                    p.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
            yield event.plain_result(f"已清理图片缓存：{removed} 张")
        except Exception:
            logger.exception("clear image cache failed")
            yield event.plain_result("清理失败（请查看控制台日志）")

    @wf_group.command("帮助", alias={"help", "h", "菜单", "指令", "命令"})
    async def wf_help(self, event: AstrMessageEvent):
        msg = (
            "wf 指令：\n"
            "- /wf 更新 (refresh/update)\n"
            "- /wf 状态 (status/info)\n"
            "- /wf 警报 (alerts)\n"
            "- /wf 突击 (sortie)\n"
            "- /wf 执刑官猎杀 (archon)\n"
            "- /wf 奸商 (void/baro)\n"
            "- /wf 仲裁 (arbitration)\n"
            "- /wf 每日特惠 (daily)\n"
            "- /wf 入侵 (invasions)\n"
            "- /wf 裂隙/裂缝 (fissure)\n"
            "- /wf 钢铁裂隙/钢铁裂缝 (steel fissure)\n"
            "- /wf 九重天/九重天裂隙 (void storms)\n"
            "- /wf 钢铁奖励 (steel path)\n"
            "- /wf 平原/福尔图娜/魔胎之境/扎里曼 (cycles)\n"
            "- /wf 轮换/双衍王境 (duviri)\n"
            "- /wf 电波 (nightwave)\n"
            "- /wf 订阅 <项目> (subscribe)\n"
            "- /wf 取消订阅 <项目|全部> (unsubscribe)\n"
            "- /wf 订阅列表 (list)\n"
            "- /wf 清理图片缓存 (clear image cache)\n"
            f"image_mode={self.img_cfg.enabled} cache_images={self.img_cfg.cache_images}"
        )
        async for r in self._send_text_or_image(event, title="/wf 帮助", text=msg):
            yield r

    @wf_group.command("更新", alias={"refresh", "update", "刷新"})
    async def wf_refresh(self, event: AstrMessageEvent):
        await self.mgr.refresh_all_once()
        sol = build_nodes_map(self.mgr.get_mirror_cached("solnodes"))
        if not sol:
            yield event.plain_result("all systems online（提示：solnodes 未加载，节点可能显示为 SolNodeXXX；可用 /wf 调试 solnodes 查看）")
            return
        yield event.plain_result("all systems online")

    @wf_group.command("状态", alias={"status", "info"})
    async def wf_status(self, event: AstrMessageEvent):
        meta = self.mgr.get_worldstate_meta()
        if not meta:
            yield event.plain_result("no cache yet, use /wf 更新")
            return
        msg = "worldstate: status={status} fetched_at={fetched_at}".format(
            status=meta.get("status"), fetched_at=meta.get("fetched_at")
        )
        yield event.plain_result(msg)

    @wf_group.command("调试", alias={"debug"})
    async def wf_debug(self, event: AstrMessageEvent, kind: str = "worldstate"):
        k = (kind or "worldstate").strip().lower()
        if k in {"worldstate", "ws"}:
            ws = self.mgr.get_worldstate_cached()
            if not isinstance(ws, dict):
                yield event.plain_result("worldstate unavailable, use /wf 更新")
                return
            keys = sorted([str(x) for x in ws.keys()])
            msg = "worldstate keys:\n" + "\n".join(keys[:80])
            async for r in self._send_text_or_image(event, title="调试", text=msg):
                yield r
            return

        if k in {"nodes", "solnodes", "mirrors"}:
            nodes_raw = self.mgr.get_mirror_cached("nodes")
            sol_raw = self.mgr.get_mirror_cached("solnodes")
            nodes_map = build_nodes_map(nodes_raw)
            sol_map = build_nodes_map(sol_raw)
            merged = dict(nodes_map)
            merged.update(sol_map)
            sample_keys = ["SolNode26", "SolNode103", "SolNode147", "SettlementNode3"]
            lines = [
                f"mirrors.nodes={'ok' if nodes_raw is not None else 'missing'} size={len(nodes_map)}",
                f"mirrors.solnodes={'ok' if sol_raw is not None else 'missing'} size={len(sol_map)}",
                "sample:",
            ]
            for sk in sample_keys:
                lines.append(f"- {sk} -> {merged.get(sk)}")
            async for r in self._send_text_or_image(event, title="调试", text="\n".join(lines)):
                yield r
            return

        yield event.plain_result("用法：/wf 调试 worldstate | /wf 调试 solnodes")

    @wf_group.command("警报", alias={"alerts", "alert"})
    async def wf_alerts(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_alerts(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="警报", text=msg):
            yield r

    @wf_group.command("入侵", alias={"invasions", "invasion"})
    async def wf_invasions(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_invasions(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="入侵", text=msg):
            yield r

    @wf_group.command("裂隙", alias={"fissure", "fissures", "裂缝"})
    async def wf_fissure(self, event: AstrMessageEvent, kind: str = "normal"):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        k = self._normalize_fissure_kind(kind)
        msg = format_fissures(ws, nodes_map=nodes, kind=k)
        async for r in self._send_text_or_image(event, title=f"裂隙({k})", text=msg):
            yield r

    @wf_group.command("钢铁裂隙", alias={"钢铁裂缝", "steel_fissure", "steel"})
    async def wf_fissure_steel(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_fissures(ws, nodes_map=nodes, kind="steel")
        async for r in self._send_text_or_image(event, title="钢铁裂隙", text=msg):
            yield r

    @wf_group.command("九重天裂隙", alias={"九重天裂缝", "九重天", "voidstorm", "storm", "railjack"})
    async def wf_fissure_storm(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_fissures(ws, nodes_map=nodes, kind="storm")
        async for r in self._send_text_or_image(event, title="九重天裂隙", text=msg):
            yield r

    @wf_group.command("奸商", alias={"void", "baro", "虚空商人"})
    async def wf_void(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_void_trader(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="奸商", text=msg):
            yield r

    @wf_group.command("每日特惠", alias={"特惠", "daily", "dailydeals"})
    async def wf_daily_deals(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        msg = format_daily_deals(ws)
        async for r in self._send_text_or_image(event, title="每日特惠", text=msg):
            yield r

    @wf_group.command("突击", alias={"sortie"})
    async def wf_sortie(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_sortie(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="突击", text=msg):
            yield r

    @wf_group.command("执刑官猎杀", alias={"猎杀", "执行官", "执政官", "执刑官", "archon"})
    async def wf_archon(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_archon_hunt(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="执刑官猎杀", text=msg):
            yield r

    @wf_group.command("仲裁", alias={"arbitration"})
    async def wf_arbitration(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = self._build_nodes_map()
        msg = format_arbitration(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="仲裁", text=msg):
            yield r

    @wf_group.command("钢铁奖励", alias={"steel_path", "steelpath", "steelreward"})
    async def wf_steel_path(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        msg = format_steel_path(ws)
        async for r in self._send_text_or_image(event, title="钢铁奖励", text=msg):
            yield r

    @wf_group.command("平原", alias={"夜灵平原", "夜灵平野", "福尔图娜", "魔胎之境", "扎里曼"})
    async def wf_cycles(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            ws = {}
        ws2 = dict(ws)
        ws2.update(self._build_cycles_ws())
        msg = format_cycles(ws2)
        async for r in self._send_text_or_image(event, title="循环", text=msg):
            yield r

    @wf_group.command("轮换", alias={"双衍王境", "duviri"})
    async def wf_duviri(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            ws = {}
        ws2 = dict(ws)
        ws2.update(self._build_cycles_ws())
        msg = format_duviri_cycle(ws2)
        async for r in self._send_text_or_image(event, title="轮换", text=msg):
            yield r

    @wf_group.command("电波", alias={"nightwave"})
    async def wf_nightwave(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        msg = format_nightwave(ws)
        async for r in self._send_text_or_image(event, title="电波", text=msg):
            yield r

    @wf_group.command("订阅", alias={"subscribe", "关注"})
    async def wf_subscribe(self, event: AstrMessageEvent, topic: str = "", a1: str = "", a2: str = "", a3: str = ""):
        self._maybe_start_background()
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())
        uname = event.get_sender_name()
        platform_name = None
        get_platform_name = getattr(event, "get_platform_name", None)
        if callable(get_platform_name):
            try:
                platform_name = str(get_platform_name())
            except Exception:
                platform_name = None

        if not topic.strip():
            async with self._sub_lock:
                data = self._sub_store.load()
                entries = self._sub_store.list_entries(data)
            cur = next((e for e in entries if e.unified_msg_origin == umo and e.user_id == uid), None)
            legacy = next((e for e in entries if e.unified_msg_origin == umo and e.user_id is None), None)
            cur_topics = sorted(list(cur.topics.keys())) if cur else []
            legacy_topics = sorted(list(legacy.topics.keys())) if legacy else []
            lines = [
                "用法：/wf 订阅 <项目> [过滤]",
                "可选项目：警报 / 入侵 / 裂隙(裂缝) / 奸商 / 每日特惠 / 突击 / 执刑官猎杀 / 仲裁 / 钢铁奖励 / 平原(循环) / 轮换 / 电波",
                "裂缝过滤示例：",
                "- /wf 订阅 裂缝 钢铁 防御",
                "- /wf 订阅 裂缝 九重天",
                "- /wf 订阅 裂缝 古纪 捕获",
                "平原过滤示例：",
                "- /wf 订阅 夜灵平原 夜晚",
                "- /wf 订阅 平原 夜晚",
                "- /wf 订阅 夜灵平原 夜晚 10  (提前10分钟预提醒)",
                "取消订阅：",
                "- /wf 取消订阅 <项目>  (同样支持过滤，如：/wf 取消订阅 裂缝 钢铁 防御)",
                "- /wf 取消订阅 全部",
                "查看列表：/wf 订阅列表",
                f"当前用户订阅：{', '.join([self._topic_label(t) for t in cur_topics]) or '-'}",
            ]
            if legacy_topics:
                lines.append(f"旧版(仅会话)订阅：{', '.join([self._topic_label(t) for t in legacy_topics])}")
            msg = "\n".join(lines)
            async for r in self._send_text_or_image(event, title="订阅", text=msg):
                yield r
            return

        t = self._normalize_sub_topic_args(topic, a1, a2, a3)
        if t is None:
            yield event.plain_result("未知订阅项，先用 /wf 订阅 查看可选项目")
            return

        ws = await self._ensure_worldstate()
        title = self._topic_label(t)
        text = ""
        sig: str | None = None
        if ws is not None:
            title, text = self._topic_text(t, ws)
            sig = self._topic_sig(t, ws)

        async with self._sub_lock:
            data = self._sub_store.load()
            added = self._sub_store.upsert_topic(data, umo=umo, uid=uid, topic=t, user_name=uname, platform=platform_name)
            if sig is not None:
                self._sub_store.set_last_sig(data, umo=umo, uid=uid, topic=t, sig=sig)
            self._sub_store.save(data)

        if added:
            yield event.plain_result(f"已订阅：{title}")
        else:
            yield event.plain_result(f"已在订阅列表：{title}")

        if ws is not None and text:
            async for r in self._send_text_or_image(event, title=title, text=text):
                yield r
        else:
            yield event.plain_result("worldstate unavailable, use /wf 更新")

    @wf_group.command("取消订阅", alias={"unsubscribe", "退订"})
    async def wf_unsubscribe(self, event: AstrMessageEvent, topic: str = "全部", a1: str = "", a2: str = "", a3: str = ""):
        self._maybe_start_background()
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())
        s = (topic or "").strip()
        if not s or s in {"全部", "all", "All"}:
            async with self._sub_lock:
                data = self._sub_store.load()
                changed = self._sub_store.clear_umo(data, umo=umo, uid=uid, include_legacy=True)
                if changed:
                    self._sub_store.save(data)
            yield event.plain_result("已取消本会话全部订阅" if changed else "本会话暂无订阅")
            return

        t = self._normalize_sub_topic_args(s, a1, a2, a3)
        if t is None:
            yield event.plain_result("未知订阅项，先用 /wf 订阅 查看可选项目")
            return

        async with self._sub_lock:
            data = self._sub_store.load()
            changed = self._sub_store.remove_topic(data, umo=umo, uid=uid, topic=t)
            if changed:
                self._sub_store.save(data)

        yield event.plain_result(f"已取消订阅：{self._topic_label(t)}" if changed else f"未订阅：{self._topic_label(t)}")

    @wf_group.command("订阅列表", alias={"subscriptions", "subs", "list"})
    async def wf_subscriptions(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())
        async with self._sub_lock:
            data = self._sub_store.load()
            entries = self._sub_store.list_entries(data)
        bound = next((e for e in entries if e.unified_msg_origin == umo and e.user_id == uid), None)
        legacy = next((e for e in entries if e.unified_msg_origin == umo and e.user_id is None), None)
        bound_topics = sorted(list(bound.topics.keys())) if bound else []
        legacy_topics = sorted(list(legacy.topics.keys())) if legacy else []
        lines = [f"当前用户订阅：{', '.join([self._topic_label(t) for t in bound_topics]) or '-'}"]
        if legacy_topics:
            lines.append(f"旧版(仅会话)订阅：{', '.join([self._topic_label(t) for t in legacy_topics])}")
        msg = "\n".join(lines)
        async for r in self._send_text_or_image(event, title="订阅列表", text=msg):
            yield r
