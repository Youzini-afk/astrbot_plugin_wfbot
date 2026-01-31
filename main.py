from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api.all import *  # type: ignore
from .wf_logging import debug as log_debug
from .wf_logging import exception as log_exception
from .wf_logging import warning as log_warning
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
from .wf_logging import configure as configure_logging


PLUGIN_ID = "astrbot_plugin_wfbot"


class WarframeDatasourcePlugin(Star):
    @staticmethod
    def _parse_admin_mapping(raw, *, key_field: str) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                key = str(k).strip()
                if not key:
                    continue
                mapping[key] = list(WarframeDatasourcePlugin._normalize_admin_list(v))
            return mapping
        if isinstance(raw, list):
            for it in raw:
                if not isinstance(it, dict):
                    continue
                key = str(it.get(key_field) or "").strip()
                if not key:
                    continue
                admins = it.get("admins") or it.get("users") or it.get("uids")
                mapping[key] = list(WarframeDatasourcePlugin._normalize_admin_list(admins))
        return mapping

    @staticmethod
    def _normalize_admin_list(value) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, list):
            return {str(v) for v in value if str(v).strip()}
        if isinstance(value, (str, int)):
            s = str(value).strip()
            return {s} if s else set()
        return set()

    @staticmethod
    def _tail_segments(path: str, n: int) -> str | None:
        parts = [p for p in str(path).split("/") if p]
        if not parts:
            return None
        if len(parts) <= n:
            return "/".join(parts)
        return "/".join(parts[-n:])

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = self._resolve_plugin_data_dir() / "warframe"

        refresh_cfg = self._get_cfg_section("refresh")
        retention_cfg = self._get_cfg_section("retention")
        render_cfg = self._get_cfg_section("render")
        subs_cfg = self._get_cfg_section("subscriptions")
        http_cfg = self._get_cfg_section("http")
        log_cfg = self._get_cfg_section("log")
        admin_cfg = self._get_cfg_section("admin")
        i18n_cfg = self._get_cfg_section("i18n")

        wf_cfg = self._build_wf_config(data_dir, refresh_cfg, retention_cfg, http_cfg, log_cfg)
        configure_logging(
            enabled=wf_cfg.log_enabled,
            cycle_enabled=wf_cfg.log_cycle_enabled,
            http_enabled=wf_cfg.log_http_enabled,
            public_export_enabled=wf_cfg.log_public_export_enabled,
            subscription_enabled=wf_cfg.log_subscription_enabled,
            cache_enabled=wf_cfg.log_cache_enabled,
            manager_enabled=wf_cfg.log_manager_enabled,
            main_enabled=wf_cfg.log_main_enabled,
            cycle_failures=wf_cfg.log_cycle_fetch_failures,
            cycle_success=wf_cfg.log_cycle_fetch_success,
        )
        self.img_cfg = self._build_image_config(render_cfg)
        self.img_dir = data_dir / "images"

        self.ds = WarframeDataSource.create(wf_cfg, plugin_id=PLUGIN_ID)
        self.mgr = self.ds.create_manager()

        self._init_subscription_config(subs_cfg)
        self._simplify_zh = bool(i18n_cfg.get("simplify_zh", True))
        self._init_admin_config(admin_cfg)

        self._sub_lock = asyncio.Lock()
        self._sub_store = SubscriptionStore(data_dir / "subscriptions.json")
        self.mgr.events.on("worldstate.updated", self._on_worldstate_updated)
        self._sub_tick_task: asyncio.Task | None = None
        self._cycle_mem: dict[str, dict[str, float | str]] = {}
        self._push_enabled: bool = True
        self._subscribe_enabled: bool = True
        self._extras_lock = asyncio.Lock()
        self._extras_cache: dict[str, dict] = {}
        self._extras_cache_at: float | None = None
        self._extras_cache_ttl = 120.0
        self._translation_cache: dict[str, str] = {}
        self._translation_cache_at: float | None = None
        self._translation_cache_ttl = 1800.0
        self._pex_translation_cache: dict[str, str] = {}
        self._pex_translation_cache_at: float | None = None
        self._pex_translation_cache_ttl = 21600.0
        self._nodes_map_cache: dict[str, str] | None = None
        self._nodes_map_cache_at: float | None = None
        self._nodes_map_cache_ttl = 600.0

        self._task: asyncio.Task | None = None
        try:
            self._task = asyncio.create_task(self._start_background())
        except RuntimeError:
            self._task = None

    if hasattr(afilter, "on_astrbot_loaded"):
        @afilter.on_astrbot_loaded()
        async def _on_loaded(self, *args, **kwargs):
            getter = getattr(self, "get_kv_data", None)
            if callable(getter):
                try:
                    val = await getter("wf_push_enabled", True)
                    self._push_enabled = bool(val)
                except Exception:
                    log_debug("load kv wf_push_enabled failed", exc_info=True, category="main")
                try:
                    val = await getter("wf_subscribe_enabled", True)
                    self._subscribe_enabled = bool(val)
                except Exception:
                    log_debug("load kv wf_subscribe_enabled failed", exc_info=True, category="main")
            self._maybe_start_background()

    def _get_cfg_section(self, key: str) -> dict:
        val = self.config.get(key, {})
        return val if isinstance(val, dict) else {}

    def _build_wf_config(self, data_dir: Path, refresh_cfg: dict, retention_cfg: dict, http_cfg: dict, log_cfg: dict) -> WarframeConfig:
        max_req_time = http_cfg.get("max_request_time_seconds", 30)
        try:
            max_req_time_f: float | None = float(max_req_time)
            if max_req_time_f <= 0:
                max_req_time_f = None
        except Exception:
            max_req_time_f = 30.0

        no_proxy_suffixes_raw = http_cfg.get("no_proxy_suffixes", ["warframe.com", "warframestat.us"])
        if isinstance(no_proxy_suffixes_raw, list):
            no_proxy_suffixes = tuple(str(x).strip() for x in no_proxy_suffixes_raw if str(x).strip())
        else:
            no_proxy_suffixes = ("warframe.com",)
        proxy_url = str(http_cfg.get("proxy_url", "")).strip()
        if not proxy_url:
            proxy_url = None

        # log.* with fallback to legacy top-level keys for compatibility
        def _as_bool(value, default: bool) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                s = value.strip().lower()
                if s in {"1", "true", "yes", "on"}:
                    return True
                if s in {"0", "false", "no", "off"}:
                    return False
                return default
            return bool(value)

        def _log_get(key: str, default: bool) -> bool:
            if isinstance(log_cfg, dict) and key in log_cfg:
                return _as_bool(log_cfg.get(key), default)
            return _as_bool(self.config.get(f"log_{key}", default), default)

        return WarframeConfig(
            data_dir=data_dir,
            public_export_language=str(self.config.get("public_export_language", "zh")),
            worldstate_refresh_interval=float(refresh_cfg.get("worldstate", self.config.get("worldstate_refresh_interval", 600))),
            public_export_refresh_interval=float(refresh_cfg.get("public_export", self.config.get("public_export_refresh_interval", 21600))),
            mirrors_refresh_interval=float(refresh_cfg.get("mirrors", self.config.get("mirrors_refresh_interval", 21600))),
            market_bootstrap_refresh_interval=float(refresh_cfg.get("market_bootstrap", self.config.get("market_bootstrap_refresh_interval", 86400))),
            cleanup_retention_days=int(retention_cfg.get("cleanup_days", self.config.get("cleanup_retention_days", 14))),
            keep_worldstate_snapshots=int(retention_cfg.get("keep_worldstate_snapshots", self.config.get("keep_worldstate_snapshots", 24))),
            keep_mirror_snapshots=int(retention_cfg.get("keep_mirror_snapshots", self.config.get("keep_mirror_snapshots", 10))),
            http_max_request_time_seconds=max_req_time_f,
            http_no_proxy_enabled=bool(http_cfg.get("no_proxy_enabled", True)),
            http_no_proxy_suffixes=no_proxy_suffixes,
            http_proxy_url=proxy_url,
            log_enabled=_log_get("enabled", True),
            log_cycle_enabled=_log_get("cycle_enabled", True),
            log_http_enabled=_log_get("http_enabled", True),
            log_public_export_enabled=_log_get("public_export_enabled", True),
            log_subscription_enabled=_log_get("subscription_enabled", True),
            log_cache_enabled=_log_get("cache_enabled", True),
            log_manager_enabled=_log_get("manager_enabled", True),
            log_main_enabled=_log_get("main_enabled", True),
            log_cycle_fetch_failures=_log_get("cycle_fetch_failures", True),
            log_cycle_fetch_success=_log_get("cycle_fetch_success", True),
        )

    def _build_image_config(self, render_cfg: dict) -> ImageRenderConfig:
        return ImageRenderConfig(
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

    def _init_subscription_config(self, subs_cfg: dict) -> None:
        self._cycles_default_offset_seconds = int(subs_cfg.get("cycles_default_offset_minutes", 0)) * 60
        self._notify_mode_default = str(subs_cfg.get("notify_mode_default", "change") or "change").lower()
        notify_modes = subs_cfg.get("notify_modes", {})
        self._notify_mode_overrides = notify_modes if isinstance(notify_modes, dict) else {}

    def _init_admin_config(self, admin_cfg: dict) -> None:
        self._group_admins = self._parse_admin_mapping(admin_cfg.get("group_admins", {}), key_field="group_id")
        self._session_admins = self._parse_admin_mapping(admin_cfg.get("session_admins", {}), key_field="session")
    def _resolve_plugin_data_dir(self) -> Path:
        # Prefer AstrBot standard tools (review requirement).
        try:
            from astrbot.api.star import StarTools  # type: ignore

            return Path(StarTools.get_data_dir(PLUGIN_ID))
        except Exception as e:
            log_debug("StarTools.get_data_dir failed: %s", e, category="main")

        # Fallback: official path helper (older docs): data/plugin_data/<plugin_name>/
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path  # type: ignore

            plugin_name = str(getattr(self, "name", PLUGIN_ID) or PLUGIN_ID)
            return Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
        except Exception as e:
            log_debug("get_astrbot_data_path fallback failed: %s", e, category="main")
        log_warning("fallback to plugin-local data dir", category="main")
        return Path(__file__).resolve().parent / ".plugin_data"

    async def _send_text_or_image(self, event: AstrMessageEvent, *, title: str, text: str):
        text = self._normalize_output_text(text)
        title = self._normalize_output_text(title)
        if not self.img_cfg.enabled:
            yield event.chain_result([Comp.Plain(text)])
            return

        path = await get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            yield event.chain_result([Comp.Plain(text)])
            return

        yield event.chain_result([Comp.Image.fromFileSystem(str(path))])

    async def _push_plain_text(self, unified_msg_origin: str, text: str) -> bool:
        text = self._normalize_output_text(text)
        try:
            from astrbot.api.event import MessageChain

            await self.context.send_message(unified_msg_origin, MessageChain().message(text))
            return True
        except Exception:
            try:
                await self.context.send_message(unified_msg_origin, [Comp.Plain(text)])
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

    async def _push_with_mention(
        self,
        unified_msg_origin: str,
        *,
        platform: str | None,
        user_id: str | None,
        comps: list,
        fallback_text: str | None = None,
    ) -> bool:
        if fallback_text is not None:
            fallback_text = self._normalize_output_text(fallback_text)
        base = list(comps)
        if user_id:
            at = self._try_build_at(user_id)
            if at is not None:
                with_at = [at] + base
                try:
                    await self.context.send_message(unified_msg_origin, with_at)
                    return True
                except Exception:
                    pass

        try:
            await self.context.send_message(unified_msg_origin, base)
            return True
        except Exception:
            pass

        if fallback_text:
            text = fallback_text
            try:
                from astrbot.api.event import MessageChain

                await self.context.send_message(unified_msg_origin, MessageChain().message(text))
                return True
            except Exception:
                log_debug("send_message fallback failed", exc_info=True, category="main")
        return False

    async def _push_text_or_image(self, unified_msg_origin: str, *, title: str, text: str) -> bool:
        text = self._normalize_output_text(text)
        title = self._normalize_output_text(title)
        if not self.img_cfg.enabled:
            return await self._push_plain_text(unified_msg_origin, text)

        path = await get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            return await self._push_plain_text(unified_msg_origin, text)

        try:
            await self.context.send_message(unified_msg_origin, [Comp.Image.fromFileSystem(str(path))])
            return True
        except Exception:
            return await self._push_plain_text(unified_msg_origin, text)

    async def _push_to_subscriber(
        self,
        unified_msg_origin: str,
        *,
        platform: str | None,
        user_id: str | None,
        group_id: str | None,
        title: str,
        text: str,
    ) -> bool:
        if not self._push_enabled:
            return False
        text = self._normalize_output_text(text)
        title = self._normalize_output_text(title)
        if platform == "aiocqhttp" and user_id and group_id:
            ok = await self._send_aiocqhttp_at_message(group_id=group_id, user_id=user_id, text=text)
            if ok:
                return True
        if not self.img_cfg.enabled:
            return await self._push_with_mention(
                unified_msg_origin, platform=platform, user_id=user_id, comps=[Comp.Plain(text)], fallback_text=text
            )

        path = await get_or_render_png(text=text, title=title, out_dir=self.img_dir, cfg=self.img_cfg, key_prefix="wf")
        if path is None:
            return await self._push_with_mention(
                unified_msg_origin, platform=platform, user_id=user_id, comps=[Comp.Plain(text)], fallback_text=text
            )
        return await self._push_with_mention(
            unified_msg_origin,
            platform=platform,
            user_id=user_id,
            comps=[Comp.Image.fromFileSystem(str(path))],
            fallback_text=text,
        )

    async def _send_aiocqhttp_at_message(self, *, group_id: str, user_id: str, text: str) -> bool:
        try:
            platform = self.context.get_platform(afilter.PlatformAdapterType.AIOCQHTTP)
            if platform is None:
                return False
            client_getter = getattr(platform, "get_client", None)
            if not callable(client_getter):
                return False
            client = client_getter()
            message = [
                {"type": "at", "data": {"qq": str(user_id)}},
                {"type": "text", "data": {"text": " " + text}},
            ]
            await client.api.call_action("send_group_msg", group_id=int(group_id), message=message)
            return True
        except Exception:
            log_debug("aiocqhttp at-send failed", exc_info=True, category="main")
            return False

    def _is_custom_admin(self, event: AstrMessageEvent) -> bool:
        uid = str(event.get_sender_id())
        group_id = None
        get_group_id = getattr(event, "get_group_id", None)
        if callable(get_group_id):
            try:
                gid = get_group_id()
                if gid is not None:
                    group_id = str(gid)
            except Exception:
                group_id = None

        if group_id and isinstance(self._group_admins, dict):
            admins = set(self._group_admins.get(group_id) or [])
            if uid in admins:
                return True

        umo = getattr(event, "unified_msg_origin", None)
        if isinstance(umo, str) and isinstance(self._session_admins, dict):
            admins = set(self._session_admins.get(umo) or [])
            if uid in admins:
                return True
        return False

    def _is_astrbot_admin(self, event: AstrMessageEvent) -> bool:
        checker = getattr(event, "check_permission", None)
        if callable(checker):
            try:
                return bool(checker(afilter.PermissionType.ADMIN))
            except Exception:
                pass
        is_admin = getattr(event, "is_admin", None)
        if callable(is_admin):
            try:
                return bool(is_admin())
            except Exception:
                pass
        perm_mgr = getattr(self.context, "permission_manager", None)
        if perm_mgr is not None:
            is_admin2 = getattr(perm_mgr, "is_admin", None)
            if callable(is_admin2):
                try:
                    return bool(is_admin2(event.get_sender_id()))
                except Exception:
                    pass
        return False

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return self._is_astrbot_admin(event) or self._is_custom_admin(event)

    def _normalize_output_text(self, text: str) -> str:
        s = text or ""
        if self._simplify_zh and s:
            return to_simplified_zh(s)
        return s

    def _notify_mode_for_topic(self, topic: str) -> str:
        base, _ = self._parse_topic_id(topic)
        mode = None
        if isinstance(self._notify_mode_overrides, dict):
            mode = self._notify_mode_overrides.get(base)
        if isinstance(mode, str):
            mode = mode.lower().strip()
        if mode not in {"change", "new_only"}:
            mode = self._notify_mode_default if self._notify_mode_default in {"change", "new_only"} else "change"
        return mode

    async def _clear_image_cache(self) -> int:
        def _clear_sync(folder: Path) -> int:
            if not folder.exists():
                return -1
            files = [p for p in folder.glob("*.png") if p.is_file()]
            removed = 0
            for p in files:
                try:
                    p.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
            return removed

        return await asyncio.to_thread(_clear_sync, self.img_dir)

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
        ws = await self.mgr.get_worldstate_cached()
        if ws is None:
            await self.mgr.refresh_worldstate(snapshot=False)
            ws = await self.mgr.get_worldstate_cached()
        return ws if isinstance(ws, dict) else None

    async def _build_nodes_map(self) -> dict[str, str]:
        nodes_raw = await self.mgr.get_mirror_cached("nodes")
        sol_raw = await self.mgr.get_mirror_cached("solnodes")
        nodes = build_nodes_map(nodes_raw)
        sol = build_nodes_map(sol_raw)
        if sol:
            for k, v in sol.items():
                nodes.setdefault(k, v)
        state_map = await self._build_state_translation_map()
        if state_map:
            for k, v in state_map.items():
                nodes.setdefault(k, v)
        if self._simplify_zh and nodes:
            nodes = {k: to_simplified_zh(v) for k, v in nodes.items() if isinstance(v, str)}
        self._nodes_map_cache = dict(nodes) if nodes else {}
        self._nodes_map_cache_at = time.monotonic()
        return nodes

    def _get_nodes_map_cache(self) -> dict[str, str]:
        if self._nodes_map_cache is None:
            return {}
        if self._nodes_map_cache_at is not None:
            if (time.monotonic() - self._nodes_map_cache_at) > self._nodes_map_cache_ttl:
                return {}
        return dict(self._nodes_map_cache)

    async def _build_cycles_ws(self) -> dict[str, dict]:
        async def get(name: str) -> dict | None:
            v = await self.mgr.get_mirror_cached(name)
            if v is not None and isinstance(v, dict):
                return v
            # If cache is empty, return None (don't trigger refresh here to avoid cascading calls)
            log_debug("cycle cache miss: %s (returned: %s)", name, type(v).__name__ if v is not None else "None", category="cycle")
            return None

        # Helper to recompute cycle state based on current time and activation time
        def _recompute_cycle_state(cycle_dict: dict | None, cycle_type: str) -> dict | None:
            if not isinstance(cycle_dict, dict):
                return cycle_dict
            activation = cycle_dict.get("activation")
            if activation is None:
                return cycle_dict
            try:
                import time
                now_ts = time.time()
                activation_ts = float(activation)
                cycle_dict = dict(cycle_dict)  # Make a copy to avoid modifying cached data
                
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
                log_debug("cycle state recomputation failed for %s: %s", cycle_type, e, category="cycle")
            return cycle_dict

        out: dict[str, dict] = {}
        earth = await get("cycle_earth")
        cetus = await get("cycle_cetus")
        vallis = await get("cycle_vallis")
        cambion = await get("cycle_cambion")
        zariman = await get("cycle_zariman")
        duviri = await get("cycle_duviri")
        
        # Recompute cycle states based on current time
        earth = _recompute_cycle_state(earth, "earth")
        cetus = _recompute_cycle_state(cetus, "cetus")
        vallis = _recompute_cycle_state(vallis, "vallis")
        cambion = _recompute_cycle_state(cambion, "cambion")
        # zariman and duviri don't have simple day/night calculations, so we leave them as-is
        
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
        log_debug("_build_cycles_ws result: %d cycles loaded", len(out), category="cycle")
        return out

    def _normalize_warframestat_arbitration(self, data: dict) -> dict:
        return {
            "node": data.get("node") or data.get("nodeKey") or data.get("location") or data.get("nodeName"),
            "type": data.get("type") or data.get("typeKey") or data.get("missionType") or data.get("missionTypeKey"),
            "expiry": data.get("expiry") or data.get("endTime"),
            "activation": data.get("activation") or data.get("startTime"),
        }

    def _normalize_warframestat_steel_path(self, data: dict) -> dict:
        rotation = data.get("rotation") or data.get("items") or data.get("offers")
        current = data.get("currentReward")
        next_reward = data.get("nextReward")
        remaining = data.get("remaining")
        if isinstance(current, dict):
            current = {"name": current.get("name") or current.get("item"), "cost": current.get("cost")}
        if isinstance(next_reward, dict):
            next_reward = {"name": next_reward.get("name") or next_reward.get("item"), "cost": next_reward.get("cost")}
        out_items: list[dict] | None = None
        if isinstance(rotation, list):
            out_items = []
            for it in rotation:
                if not isinstance(it, dict):
                    continue
                name = it.get("name") or it.get("item") or it.get("storeItem") or it.get("title")
                cost = it.get("cost") or it.get("price") or it.get("shopCost")
                out_items.append({"name": name, "cost": cost})
        return {
            "expiry": data.get("expiry") or data.get("endTime"),
            "rotation": out_items if out_items is not None else rotation,
            "currentReward": current,
            "nextReward": next_reward,
            "remaining": remaining,
        }

    def _normalize_warframestat_void_trader(self, data: dict) -> dict:
        inventory = data.get("inventory") or data.get("items") or data.get("manifest")
        return {
            "location": data.get("location") or data.get("node"),
            "activation": data.get("activation"),
            "expiry": data.get("expiry"),
            "character": data.get("character"),
            "inventory": inventory if isinstance(inventory, list) else [],
        }

    def _normalize_warframestat_sortie(self, data: dict) -> dict:
        variants = data.get("variants")
        out_vars: list[dict] | None = None
        if isinstance(variants, list):
            out_vars = []
            for v in variants:
                if not isinstance(v, dict):
                    continue
                out_vars.append(
                    {
                        "node": v.get("node") or v.get("nodeKey"),
                        "missionType": v.get("missionType") or v.get("missionTypeKey") or v.get("type") or v.get("typeKey"),
                        "modifier": v.get("modifier") or v.get("modifierDescription"),
                    }
                )
        return {
            "boss": data.get("boss") or data.get("bossName"),
            "expiry": data.get("expiry") or data.get("endTime"),
            "variants": out_vars if out_vars is not None else variants,
        }

    def _normalize_warframestat_archon(self, data: dict) -> dict:
        missions = data.get("missions") or data.get("variants")
        out_m: list[dict] | None = None
        if isinstance(missions, list):
            out_m = []
            for m in missions:
                if not isinstance(m, dict):
                    continue
                out_m.append(
                    {
                        "node": m.get("node") or m.get("nodeKey") or m.get("location"),
                        "missionType": m.get("type") or m.get("typeKey") or m.get("missionType") or m.get("missionTypeKey"),
                    }
                )
        return {
            "boss": data.get("boss") or data.get("bossName"),
            "expiry": data.get("expiry") or data.get("endTime"),
            "missions": out_m if out_m is not None else missions,
        }

    async def _fetch_warframestat_json(self, endpoint: str) -> dict | None:
        lang = str(self.config.get("public_export_language", "zh") or "zh")
        urls = [
            f"https://api.warframestat.us/pc/{endpoint}?language={lang}",
            f"https://r.jina.ai/http://api.warframestat.us/pc/{endpoint}?language={lang}",
            f"https://r.jina.ai/https://api.warframestat.us/pc/{endpoint}?language={lang}",
        ]
        for url in urls:
            try:
                resp = await self.ds.http.get(url)
            except Exception:
                continue
            if not (200 <= resp.status < 400):
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        log_debug("warframestat fetch failed: %s", endpoint, exc_info=True, category="http")
        return None

    async def _get_extras_cache(self) -> dict[str, dict]:
        now = time.monotonic()
        if self._extras_cache_at is not None and (now - self._extras_cache_at) <= self._extras_cache_ttl:
            return dict(self._extras_cache)
        async with self._extras_lock:
            now = time.monotonic()
            if self._extras_cache_at is not None and (now - self._extras_cache_at) <= self._extras_cache_ttl:
                return dict(self._extras_cache)
            extras: dict[str, dict] = {}
            arb = await self._fetch_warframestat_json("arbitration")
            if isinstance(arb, dict):
                extras["arbitration"] = self._normalize_warframestat_arbitration(arb)
            sp = await self._fetch_warframestat_json("steelPath")
            if isinstance(sp, dict):
                extras["steelPathOffering"] = self._normalize_warframestat_steel_path(sp)
            vt = await self._fetch_warframestat_json("voidTrader")
            if isinstance(vt, dict):
                extras["voidTrader"] = self._normalize_warframestat_void_trader(vt)
            so = await self._fetch_warframestat_json("sortie")
            if isinstance(so, dict):
                extras["sortie"] = self._normalize_warframestat_sortie(so)
            ah = await self._fetch_warframestat_json("archonHunt")
            if isinstance(ah, dict):
                extras["archonHunt"] = self._normalize_warframestat_archon(ah)
            self._extras_cache = extras
            self._extras_cache_at = time.monotonic()
            return dict(extras)

    async def _merge_extras_ws(self, ws: dict) -> dict:
        need_arb = not isinstance(ws.get("arbitration"), dict)
        sp = ws.get("steelPathOffering") or ws.get("steelPath")
        need_sp = not isinstance(sp, dict) or not (sp.get("rotation") or sp.get("currentReward") or sp.get("nextReward"))
        vt = ws.get("voidTrader")
        inv = vt.get("inventory") if isinstance(vt, dict) else None
        if not isinstance(inv, list):
            inv = vt.get("manifest") if isinstance(vt, dict) else None
        has_names = False
        if isinstance(inv, list):
            for it in inv:
                if not isinstance(it, dict):
                    continue
                if it.get("item") or it.get("name"):
                    has_names = True
                    break
        need_vt = not isinstance(vt, dict) or not isinstance(inv, list) or not has_names
        sortie = ws.get("sortie")
        need_sortie = (
            not isinstance(sortie, dict)
            or not isinstance(sortie.get("variants"), list)
            or not isinstance(sortie.get("boss"), str)
            or str(sortie.get("boss")).startswith("SORTIE_")
        )
        archon = ws.get("liteSortie") or ws.get("archonHunt")
        need_archon = (
            not isinstance(archon, dict)
            or not isinstance(archon.get("missions") or archon.get("variants"), list)
            or not isinstance(archon.get("boss") or archon.get("bossName"), str)
            or str(archon.get("boss") or archon.get("bossName")).startswith("SORTIE_")
        )
        if not need_arb and not need_sp and not need_vt and not need_sortie and not need_archon:
            return ws
        extras = await self._get_extras_cache()
        if not extras:
            return ws
        out = dict(ws)
        if need_arb and isinstance(extras.get("arbitration"), dict):
            out["arbitration"] = extras["arbitration"]
        if need_sp and isinstance(extras.get("steelPathOffering"), dict):
            out["steelPathOffering"] = extras["steelPathOffering"]
        if need_vt and isinstance(extras.get("voidTrader"), dict):
            merged = dict(out.get("voidTrader") or {})
            merged.update(extras["voidTrader"])
            out["voidTrader"] = merged
        if need_sortie and isinstance(extras.get("sortie"), dict):
            out["sortie"] = extras["sortie"]
        if need_archon and isinstance(extras.get("archonHunt"), dict):
            out["archonHunt"] = extras["archonHunt"]
        return out

    async def _build_state_translation_map(self) -> dict[str, str]:
        now = time.monotonic()
        if self._translation_cache_at is not None and (now - self._translation_cache_at) <= self._translation_cache_ttl:
            return dict(self._translation_cache)
        raw = await self.mgr.get_mirror_cached("state_translation")
        mapping: dict[str, str] = {}
        if isinstance(raw, list):
            for it in raw:
                if not isinstance(it, dict):
                    continue
                key = it.get("uniqueName")
                name = it.get("name")
                if isinstance(key, str) and key and isinstance(name, str) and name:
                    if self._simplify_zh:
                        name = to_simplified_zh(name)
                    mapping[key] = name
                    for n in (1, 2, 3, 4):
                        suffix = self._tail_segments(key, n)
                        if suffix and suffix not in mapping:
                            mapping[suffix] = name
        elif isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str):
                    mapping[k] = to_simplified_zh(v) if self._simplify_zh else v
        if mapping:
            self._translation_cache = mapping
            self._translation_cache_at = time.monotonic()
        return dict(mapping)

    async def _build_public_export_translation_map(self) -> dict[str, str]:
        now = time.monotonic()
        if self._pex_translation_cache_at is not None and (now - self._pex_translation_cache_at) <= self._pex_translation_cache_ttl:
            return dict(self._pex_translation_cache)

        lang = str(self.config.get("public_export_language", "zh") or "zh")
        sources = [
            ("ExportCustoms", "ExportCustoms"),
            ("ExportDrones", "ExportDrones"),
            ("ExportFlavour", "ExportFlavour"),
            ("ExportGear", "ExportGear"),
            ("ExportKeys", "ExportKeys"),
            ("ExportRelicArcane", "ExportRelicArcane"),
            ("ExportResources", "ExportResources"),
            ("ExportSentinels", "ExportSentinels"),
            ("ExportSortieRewards", "ExportOther"),
            ("ExportUpgrades", "ExportUpgrades"),
            ("ExportWarframes", "ExportWarframes"),
            ("ExportWeapons", "ExportWeapons"),
        ]

        def build_sync() -> dict[str, str]:
            mapping: dict[str, str] = {}
            for base, key in sources:
                filename = f"{base}_{lang}.json"
                path = self.ds.public_export.export_path(filename)
                if not path.exists():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    data = json.loads(text)
                except Exception:
                    continue
                arr = data.get(key)
                if not isinstance(arr, list):
                    continue
                for it in arr:
                    if not isinstance(it, dict):
                        continue
                    unique = it.get("uniqueName") or it.get("itemUniqueName") or it.get("Item")
                    name = it.get("name") or it.get("itemName") or it.get("productName") or it.get("fullName") or it.get("title")
                    if not isinstance(unique, str) or not unique or not isinstance(name, str) or not name:
                        continue
                    if self._simplify_zh:
                        name = to_simplified_zh(name)
                    if unique not in mapping:
                        mapping[unique] = name
                    for n in (3, 4):
                        suffix = WarframeDatasourcePlugin._tail_segments(unique, n)
                        if suffix and suffix not in mapping:
                            mapping[suffix] = name
            return mapping

        mapping = await asyncio.to_thread(build_sync)
        if mapping:
            self._pex_translation_cache = mapping
            self._pex_translation_cache_at = time.monotonic()
        return dict(mapping)

    async def _build_item_translation_map(self) -> dict[str, str]:
        state_map = await self._build_state_translation_map()
        export_map = await self._build_public_export_translation_map()
        merged = dict(export_map)
        merged.update(state_map)
        return merged

    def _translate_label(self, raw: str, mapping: dict[str, str]) -> str:
        if not raw:
            return raw
        if raw in mapping:
            return mapping[raw]
        for n in (3, 4):
            suffix = self._tail_segments(raw, n)
            if suffix and suffix in mapping:
                return mapping[suffix]
        return raw

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

    def _normalize_planet_token(self, raw: str) -> str | None:
        s = self._norm_token(raw)
        if not s:
            return None
        mapping = {
            "mercury": {"mercury", "水星"},
            "venus": {"venus", "金星"},
            "earth": {"earth", "地球"},
            "mars": {"mars", "火星"},
            "phobos": {"phobos", "火卫一"},
            "deimos": {"deimos", "火卫二", "殁世", "殁世幽都", "魔胎之境", "cambion"},
            "ceres": {"ceres", "谷神星"},
            "jupiter": {"jupiter", "木星"},
            "europa": {"europa", "木卫二"},
            "saturn": {"saturn", "土星"},
            "uranus": {"uranus", "天王星"},
            "neptune": {"neptune", "海王星"},
            "pluto": {"pluto", "冥王星"},
            "eris": {"eris", "阋神星"},
            "sedna": {"sedna", "塞德娜"},
            "lua": {"lua", "月球"},
            "void": {"void", "虚空"},
            "zariman": {"zariman", "扎里曼"},
            "duviri": {"duviri", "双衍王境", "轮换"},
        }
        for key, names in mapping.items():
            if s in {self._norm_token(x) for x in names}:
                return key
        return None

    def _planet_label(self, key: str) -> str:
        return {
            "mercury": "水星",
            "venus": "金星",
            "earth": "地球",
            "mars": "火星",
            "phobos": "火卫一",
            "deimos": "火卫二",
            "ceres": "谷神星",
            "jupiter": "木星",
            "europa": "木卫二",
            "saturn": "土星",
            "uranus": "天王星",
            "neptune": "海王星",
            "pluto": "冥王星",
            "eris": "阋神星",
            "sedna": "塞德娜",
            "lua": "月球",
            "void": "虚空",
            "zariman": "扎里曼",
            "duviri": "双衍王境",
        }.get(key, key)

    def _node_planet_key(self, node: str | None, nodes_map: dict[str, str] | None) -> str | None:
        raw = str(node or "").strip()
        candidates = []
        if nodes_map and raw in nodes_map:
            candidates.append(nodes_map.get(raw))
        candidates.append(raw)
        for cand in candidates:
            if not isinstance(cand, str) or not cand.strip():
                continue
            s = cand.strip()
            if "(" in s and ")" in s:
                inner = s.rsplit("(", 1)[1].split(")", 1)[0].strip()
                key = self._normalize_planet_token(inner)
                if key:
                    return key
            if "/" in s:
                inner = s.split("/", 1)[0].strip()
                key = self._normalize_planet_token(inner)
                if key:
                    return key
            if " - " in s:
                inner = s.split(" - ", 1)[0].strip()
                key = self._normalize_planet_token(inner)
                if key:
                    return key
            key = self._normalize_planet_token(s)
            if key:
                return key
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

    def _normalize_sub_topic_non_fissure(self, t0: str, args: list[str], *, topic_raw: str) -> str | None:
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
        if t0 in {"轮换", "双衍王境", "duviri"}:
            return "duviri"
        if t0 in {"电波", "nightwave"}:
            return "nightwave"

        if t0 in {"平原", "循环", "cycles", "cetus", "vallis", "cambion", "zariman", "夜灵平原", "夜灵平野", "地球", "福尔图娜", "魔胎之境", "扎里曼"}:
            base = "cycles"
            zone = self._normalize_cycle_zone_token(topic_raw) or "cetus"
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

        return None

    def _normalize_sub_topic_fissures(self, t0: str, args: list[str], *, topic_raw: str) -> str | None:
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
                planet = self._normalize_planet_token(a)
                if planet:
                    filters["planet"] = planet
                    continue
                mt = self._normalize_mission_type_token(a)
                if mt:
                    filters["mission"] = mt
                    continue
                return None
            return self._make_topic_id(base, filters)

        kind = self._normalize_fissure_kind_token(topic_raw)
        if kind:
            filters = {"kind": kind}
            for a in args:
                tier = self._normalize_relic_tier_token(a)
                if tier:
                    filters["tier"] = tier
                    continue
                planet = self._normalize_planet_token(a)
                if planet:
                    filters["planet"] = planet
                    continue
                mt = self._normalize_mission_type_token(a)
                if mt:
                    filters["mission"] = mt
                    continue
                return None
            return self._make_topic_id("fissures", filters)

        return None

    def _normalize_sub_topic_args(self, topic: str | None, *mods: str) -> str | None:
        t0 = self._norm_token(topic or "")
        args = [m for m in mods if (m or "").strip()]
        if not t0:
            return None

        non_f = self._normalize_sub_topic_non_fissure(t0, args, topic_raw=str(topic or ""))
        if non_f is not None:
            return non_f

        fiss = self._normalize_sub_topic_fissures(t0, args, topic_raw=str(topic or ""))
        if fiss is not None:
            return fiss

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
        planet = (filters.get("planet") or "").lower()
        if planet:
            extra.append(self._planet_label(planet))
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

    async def _topic_text(self, topic: str, ws: dict) -> tuple[str, str]:
        nodes = await self._build_nodes_map()
        base, filters = self._parse_topic_id(topic)

        if base in {"arbitration", "steel_path", "sortie", "archon"}:
            ws = await self._merge_extras_ws(ws)

        if base == "alerts":
            return "警报", format_alerts(ws, nodes_map=nodes)
        if base == "invasions":
            return "入侵", format_invasions(ws, nodes_map=nodes)
        if base == "fissures":
            kind = (filters.get("kind") or "normal").lower()
            tier = (filters.get("tier") or "").lower() or None
            mission = (filters.get("mission") or "").lower() or None
            planet = (filters.get("planet") or "").lower() or None
            title = self._topic_label(topic)
            msg = self._format_fissures_filtered(ws, nodes_map=nodes, kind=kind, tier=tier, mission=mission, planet=planet)
            return title, msg
        if base == "void_trader":
            ws = await self._merge_extras_ws(ws)
            items_map = await self._build_item_translation_map()
            return "奸商", format_void_trader(ws, nodes_map=nodes, items_map=items_map)
        if base == "daily_deals":
            items_map = await self._build_item_translation_map()
            return "每日特惠", format_daily_deals(ws, items_map=items_map)
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
            if "earthCycle" not in ws2 and "cetusCycle" not in ws2:
                ws2.update(await self._build_cycles_ws())
            msg = self._format_cycles_filtered(ws2, zone=zone, desired_state=desired)
            return title, msg
        if base == "duviri":
            ws2 = dict(ws)
            if "duviriCycle" not in ws2 and "duvalierCycle" not in ws2:
                ws2.update(await self._build_cycles_ws())
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
        planet: str | None,
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

            if planet:
                if not nodes_map:
                    log_debug("nodes map missing; skip planet filter for fissures display", category="main")
                    planet = None
                else:
                    node_key = m.get("node") or m.get("location") or ""
                    p_key = self._node_planet_key(node_key, nodes_map)
                    if not p_key or p_key != planet.lower():
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
        if planet:
            filter_bits.append(self._planet_label(planet))
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
            log_debug("cycle data missing for %s: obj=%s, ws keys=%s", key, obj, list(ws.keys()), category="cycle")
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
        
        def fmt_delta() -> str:
            # Try to use timeLeft first (WarframeStat pre-formatted string)
            tl = obj.get("timeLeft")
            if isinstance(tl, str) and tl.strip():
                return str(tl).strip()
            # Fallback: compute from expiry timestamp
            exp_raw = obj.get("expiry") or obj.get("endTime") or obj.get("expiryDate") or obj.get("expiration")
            exp_ts = self._parse_expiry_ts(exp_raw)
            if exp_ts is None:
                return "-"
            now_ts = datetime.now(timezone.utc).timestamp()
            delta = exp_ts - now_ts
            if delta <= 0:
                return "已结束"
            minutes = int(delta // 60)
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
        eta_str = fmt_delta()

        if not desired_state:
            return f"{title}｜当前：{cur}｜⏳{eta_str}"

        des = state_label(zone, desired_state)
        if st == desired_state:
            return f"{title}｜当前：{cur}（已达成）"

        return f"{title}｜当前：{cur}｜距离{des}：⏳{eta_str}"

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
            if s.isdigit():
                v = float(s)
                if v > 1e12:
                    v = v / 1000.0
                return v
            s = s.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
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

        exp_ts = self._parse_expiry_ts(obj.get("expiry") or obj.get("endTime") or obj.get("expiryDate") or obj.get("expiration"))
        # If expiry parsing failed, try to fallback (though pre-reminder without expiry won't work well)
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
                log_exception("wf subscription tick loop error", category="subscription")
            await asyncio.sleep(30.0)

    async def _run_pre_reminders_once(self) -> None:
        ws = await self.mgr.get_worldstate_cached()
        if not isinstance(ws, dict):
            ws = {}
        ws = dict(ws)
        ws.update(await self._build_cycles_ws())

        async with self._sub_lock:
            data = await self._sub_store.load()

        items = data.get("items")
        if not isinstance(items, list) or not items:
            return

        to_send: list[tuple[str, str | None, str | None, str | None, str, str, str]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            umo = it.get("umo")
            uid = it.get("uid")
            platform = it.get("platform")
            group_id = it.get("group_id")
            topics = it.get("topics")
            if not isinstance(umo, str) or not umo or not isinstance(topics, dict):
                continue
            if uid is not None and not isinstance(uid, str):
                uid = None
            if platform is not None and not isinstance(platform, str):
                platform = None
            if group_id is not None and not isinstance(group_id, str):
                group_id = None
            for t, meta in topics.items():
                if not isinstance(t, str) or not isinstance(meta, dict):
                    continue
                pr = self._cycles_pre_reminder(t, ws)
                if pr is None:
                    continue
                title, text, pre_sig = pr
                if meta.get("last_pre_sig") == pre_sig:
                    continue
                to_send.append((umo, platform, uid, group_id, t, title, text))

        if not to_send:
            return

        sent: list[tuple[str, str | None, str, str]] = []
        for umo, platform, uid, group_id, topic, title, text in to_send:
            ok = await self._push_to_subscriber(umo, platform=platform, user_id=uid, group_id=group_id, title=title, text=text)
            if ok:
                sent.append((umo, uid, topic, hashlib.sha256(text.encode("utf-8")).hexdigest()))

        if not sent:
            return

        async with self._sub_lock:
            data2 = await self._sub_store.load()
            changed = False
            for umo, uid, topic, _ in sent:
                pr = self._cycles_pre_reminder(topic, ws)
                if pr is None:
                    continue
                _, _, pre_sig = pr
                changed = self._sub_store.set_last_pre_sig(data2, umo=umo, uid=uid, topic=topic, sig=pre_sig) or changed
            if changed:
                await self._sub_store.save(data2)

    def _fissure_ids_for_topic(self, topic: str, ws: dict) -> list[str]:
        base, filters = self._parse_topic_id(topic)
        if base != "fissures":
            return []
        kind = (filters.get("kind") or "normal").lower()
        tier = (filters.get("tier") or "").lower() or None
        mission = (filters.get("mission") or "").lower() or None
        planet = (filters.get("planet") or "").lower() or None
        nodes_map = self._get_nodes_map_cache()
        return self._collect_fissure_ids(ws, kind=kind, tier=tier, mission=mission, planet=planet, nodes_map=nodes_map)

    def _alert_ids_for_topic(self, ws: dict) -> list[str]:
        alerts = ws.get("alerts", [])
        if not isinstance(alerts, list):
            return []
        ids: list[str] = []
        for a in alerts:
            if not isinstance(a, dict):
                continue
            aid = a.get("id") or a.get("_id")
            if not aid:
                mi = a.get("missionInfo") or {}
                loc = mi.get("location") or mi.get("locationKey") or a.get("location") or ""
                mt = mi.get("missionType") or mi.get("missionTypeKey") or a.get("missionType") or ""
                exp = a.get("expiry") or a.get("endTime") or ""
                aid = f"{loc}|{mt}|{exp}"
            ids.append(str(aid))
        return ids

    def _invasion_ids_for_topic(self, ws: dict) -> list[str]:
        inv = ws.get("invasions", [])
        if not isinstance(inv, list):
            return []
        ids: list[str] = []
        for i in inv:
            if not isinstance(i, dict) or i.get("completed"):
                continue
            iid = i.get("id") or i.get("_id")
            if not iid:
                node = i.get("node") or ""
                atk = i.get("attackingFaction") or ""
                deff = i.get("defendingFaction") or ""
                exp = i.get("expiry") or ""
                iid = f"{node}|{atk}|{deff}|{exp}"
            ids.append(str(iid))
        return ids

    def _daily_deal_ids_for_topic(self, ws: dict) -> list[str]:
        deals = ws.get("dailyDeals", [])
        if not isinstance(deals, list):
            return []
        ids: list[str] = []
        for d in deals:
            if not isinstance(d, dict):
                continue
            did = d.get("id") or d.get("_id")
            if not did:
                item = d.get("item") or d.get("uniqueName") or d.get("itemType") or ""
                price = d.get("salePrice") or d.get("originalPrice") or ""
                exp = d.get("expiry") or ""
                did = f"{item}|{price}|{exp}"
            ids.append(str(did))
        return ids

    def _arbitration_ids_for_topic(self, ws: dict) -> list[str]:
        arb = ws.get("arbitration")
        if not isinstance(arb, dict):
            return []
        aid = arb.get("id") or arb.get("_id")
        if not aid:
            node = arb.get("node") or arb.get("location") or arb.get("nodeName") or arb.get("nodeKey") or ""
            mtype = arb.get("type") or arb.get("missionType") or ""
            exp = arb.get("expiry") or arb.get("endTime") or ""
            aid = f"{node}|{mtype}|{exp}"
        return [str(aid)] if aid else []

    def _void_trader_ids_for_topic(self, ws: dict) -> list[str]:
        vt = ws.get("voidTrader")
        if not isinstance(vt, dict):
            return []
        ids: list[str] = []
        base = vt.get("active")
        loc = vt.get("location") or ""
        exp = vt.get("expiry") or vt.get("endTime") or ""
        if base is not None or loc or exp:
            ids.append(f"status={base}|loc={loc}|exp={exp}")
        inv = vt.get("inventory")
        if not isinstance(inv, list):
            inv = vt.get("manifest") if isinstance(vt.get("manifest"), list) else []
        for it in inv:
            if not isinstance(it, dict):
                continue
            name = it.get("item") or it.get("name") or it.get("uniqueName") or it.get("itemName") or it.get("itemType")
            ducats = it.get("ducats") or it.get("ducatCost") or it.get("dukat")
            credits = it.get("credits") or it.get("creditCost") or it.get("cost")
            if not name:
                continue
            ids.append(f"{name}|{ducats}|{credits}")
        return ids

    def _steel_path_ids_for_topic(self, ws: dict) -> list[str]:
        sp = ws.get("steelPathOffering") or ws.get("steelPath")
        if not isinstance(sp, dict):
            return []
        ids: list[str] = []
        exp = sp.get("expiry") or sp.get("endTime") or ""
        current = sp.get("currentReward")
        if isinstance(current, dict):
            name = current.get("name") or current.get("item") or current.get("uniqueName")
            cost = current.get("cost")
            if name:
                ids.append(f"cur:{name}|{cost}|{exp}")
        elif isinstance(current, str) and current:
            ids.append(f"cur:{current}|{exp}")
        next_reward = sp.get("nextReward")
        if isinstance(next_reward, dict):
            name = next_reward.get("name") or next_reward.get("item") or next_reward.get("uniqueName")
            cost = next_reward.get("cost")
            if name:
                ids.append(f"next:{name}|{cost}|{exp}")
        elif isinstance(next_reward, str) and next_reward:
            ids.append(f"next:{next_reward}|{exp}")
        rotation = sp.get("rotation")
        if isinstance(rotation, list):
            for it in rotation:
                if not isinstance(it, dict):
                    continue
                name = it.get("name") or it.get("item") or it.get("uniqueName")
                cost = it.get("cost")
                if name:
                    ids.append(f"rot:{name}|{cost}")
        return ids

    def _topic_ids_for_topic(self, topic: str, ws: dict) -> list[str] | None:
        base, _ = self._parse_topic_id(topic)
        if base == "fissures":
            return self._fissure_ids_for_topic(topic, ws)
        if base == "alerts":
            return self._alert_ids_for_topic(ws)
        if base == "invasions":
            return self._invasion_ids_for_topic(ws)
        if base == "daily_deals":
            return self._daily_deal_ids_for_topic(ws)
        if base == "arbitration":
            return self._arbitration_ids_for_topic(ws)
        if base == "void_trader":
            return self._void_trader_ids_for_topic(ws)
        if base == "steel_path":
            return self._steel_path_ids_for_topic(ws)
        return None

    def _collect_fissure_ids(
        self,
        ws: dict,
        *,
        kind: str,
        tier: str | None,
        mission: str | None,
        planet: str | None,
        nodes_map: dict[str, str] | None,
    ) -> list[str]:
        fiss = ws.get("voidStorms" if kind == "storm" else "activeMissions", [])
        if not isinstance(fiss, list):
            return []
        ids: list[str] = []
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
            if planet:
                if not nodes_map:
                    # Keep ids unfiltered when we can't resolve node->planet.
                    planet = None
                else:
                    node_key = m.get("node") or m.get("location") or ""
                    p_key = self._node_planet_key(node_key, nodes_map)
                    if not p_key or p_key != planet.lower():
                        continue
            fid = m.get("id") or m.get("_id")
            if not fid:
                node = m.get("node") or m.get("location") or ""
                tier_code = m.get("modifier") or m.get("tier") or ""
                mt_code = m.get("missionType") or m.get("MissionType") or ""
                exp = m.get("expiry") or m.get("endTime") or ""
                fid = f"{node}|{tier_code}|{mt_code}|{exp}"
            ids.append(str(fid))
        return ids

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
            inv = v.get("inventory") or v.get("manifest") or []
            rows = []
            if isinstance(inv, list):
                for it in inv:
                    if not isinstance(it, dict):
                        continue
                    name = it.get("item") or it.get("name") or it.get("uniqueName") or it.get("ItemType")
                    rows.append({"name": name, "ducats": it.get("ducats"), "credits": it.get("credits")})
            return h({"active": v.get("active"), "loc": v.get("location"), "exp": v.get("expiry"), "items": rows})

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
            return h(
                {
                    "rot": sp.get("rotation") or sp.get("name"),
                    "cur": sp.get("currentReward"),
                    "next": sp.get("nextReward"),
                    "exp": sp.get("expiry"),
                }
            )

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
        ws2.update(await self._build_cycles_ws())

        async with self._sub_lock:
            data = await self._sub_store.load()

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

        need_extras = False
        for t in all_topics:
            base, _ = self._parse_topic_id(t)
            if base in {"arbitration", "steel_path", "void_trader", "sortie", "archon"}:
                need_extras = True
                break
        if need_extras:
            ws2 = await self._merge_extras_ws(ws2)

        ws_snapshot = copy.deepcopy(ws2)

        topic_payload: dict[str, tuple[str, str, str]] = {}
        topic_ids: dict[str, list[str]] = {}
        for t in sorted(all_topics):
            title, text = await self._topic_text(t, ws_snapshot)
            sig = self._topic_sig(t, ws_snapshot)
            topic_payload[t] = (title, text, sig)
            if self._notify_mode_for_topic(t) == "new_only":
                ids = self._topic_ids_for_topic(t, ws_snapshot)
                if ids is not None:
                    topic_ids[t] = ids

        to_send: list[tuple[str, str | None, str | None, str | None, str, str, str, str | None, list[str] | None]] = []
        pending_ids_updates: list[tuple[str, str | None, str, list[str]]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            umo = it.get("umo")
            uid = it.get("uid")
            platform = it.get("platform")
            group_id = it.get("group_id")
            topics = it.get("topics")
            if not isinstance(umo, str) or not umo or not isinstance(topics, dict):
                continue
            if uid is not None and not isinstance(uid, str):
                uid = None
            if platform is not None and not isinstance(platform, str):
                platform = None
            if group_id is not None and not isinstance(group_id, str):
                group_id = None
            for t, meta in topics.items():
                if not isinstance(t, str) or t not in topic_payload or not isinstance(meta, dict):
                    continue
                if not self._topic_should_notify(t, ws_snapshot):
                    continue
                title, text, sig = topic_payload[t]
                ids = topic_ids.get(t) if self._notify_mode_for_topic(t) == "new_only" else None
                if ids is not None:
                    last_ids = meta.get("last_ids")
                    if not isinstance(last_ids, list):
                        if ids:
                            pending_ids_updates.append((umo, uid, t, ids))
                        continue
                    last_set = set(last_ids)
                    new_ids = [fid for fid in ids if fid not in last_set]
                    if not new_ids:
                        if set(ids) != last_set:
                            pending_ids_updates.append((umo, uid, t, ids))
                        continue
                    to_send.append((umo, platform, uid, group_id, t, title, text, sig, ids))
                    continue
                last_sig = meta.get("last_sig")
                if last_sig != sig:
                    to_send.append((umo, platform, uid, group_id, t, title, text, sig, None))

        if not to_send and not pending_ids_updates:
            return

        sent: list[tuple[str, str | None, str, str | None, list[str] | None]] = []
        for umo, platform, uid, group_id, topic, title, text, sig, ids in to_send:
            ok = await self._push_to_subscriber(umo, platform=platform, user_id=uid, group_id=group_id, title=title, text=text)
            if ok:
                sent.append((umo, uid, topic, sig, ids))

        async with self._sub_lock:
            data2 = await self._sub_store.load()
            changed = False
            for umo, uid, topic, sig, ids in sent:
                if sig is not None:
                    changed = self._sub_store.set_last_sig(data2, umo=umo, uid=uid, topic=topic, sig=sig) or changed
                if ids is not None:
                    changed = self._sub_store.set_last_ids(data2, umo=umo, uid=uid, topic=topic, ids=ids) or changed
            for umo, uid, topic, ids in pending_ids_updates:
                changed = self._sub_store.set_last_ids(data2, umo=umo, uid=uid, topic=topic, ids=ids) or changed
            if changed:
                await self._sub_store.save(data2)

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
            removed = await self._clear_image_cache()
            if removed < 0:
                yield event.plain_result("图片缓存目录不存在（无需清理）")
                return
            if removed == 0:
                yield event.plain_result("图片缓存为空（无需清理）")
                return
            yield event.plain_result(f"已清理图片缓存：{removed} 张")
        except Exception:
            log_exception("clear image cache failed", category="main")
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
            "- /wf 订阅测试 <项目|全部> (test)\n"
            "- /wf 清理图片缓存 (clear image cache)\n"
            "- /wf 管理 推送 开|关|状态\n"
            "- /wf 管理 清理图片缓存\n"
            "- /wf 管理 删除订阅 全部|本群|<QQ>\n"
            "- /wf 管理 订阅 <QQ> <项目> [过滤]\n"
            f"image_mode={self.img_cfg.enabled} cache_images={self.img_cfg.cache_images}"
        )
        async for r in self._send_text_or_image(event, title="/wf 帮助", text=msg):
            yield r

    @wf_group.command("更新", alias={"refresh", "update", "刷新"})
    async def wf_refresh(self, event: AstrMessageEvent):
        await self.mgr.refresh_all_once()
        sol = build_nodes_map(await self.mgr.get_mirror_cached("solnodes"))
        if not sol:
            yield event.plain_result("all systems online（提示：solnodes 未加载，节点可能显示为 SolNodeXXX；可用 /wf 调试 solnodes 查看）")
            return
        yield event.plain_result("all systems online")

    @wf_group.command("状态", alias={"status", "info"})
    async def wf_status(self, event: AstrMessageEvent):
        meta = await self.mgr.get_worldstate_meta()
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
            ws = await self.mgr.get_worldstate_cached()
            if not isinstance(ws, dict):
                yield event.plain_result("worldstate unavailable, use /wf 更新")
                return
            keys = sorted([str(x) for x in ws.keys()])
            msg = "worldstate keys:\n" + "\n".join(keys[:80])
            async for r in self._send_text_or_image(event, title="调试", text=msg):
                yield r
            return

        if k in {"nodes", "solnodes", "mirrors"}:
            nodes_raw = await self.mgr.get_mirror_cached("nodes")
            sol_raw = await self.mgr.get_mirror_cached("solnodes")
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
        nodes = await self._build_nodes_map()
        msg = format_alerts(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="警报", text=msg):
            yield r

    @wf_group.command("入侵", alias={"invasions", "invasion"})
    async def wf_invasions(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = await self._build_nodes_map()
        msg = format_invasions(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="入侵", text=msg):
            yield r

    @wf_group.command("裂隙", alias={"fissure", "fissures", "裂缝"})
    async def wf_fissure(self, event: AstrMessageEvent, kind: str = "normal"):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = await self._build_nodes_map()
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
        nodes = await self._build_nodes_map()
        msg = format_fissures(ws, nodes_map=nodes, kind="steel")
        async for r in self._send_text_or_image(event, title="钢铁裂隙", text=msg):
            yield r

    @wf_group.command("九重天裂隙", alias={"九重天裂缝", "九重天", "voidstorm", "storm", "railjack"})
    async def wf_fissure_storm(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        nodes = await self._build_nodes_map()
        msg = format_fissures(ws, nodes_map=nodes, kind="storm")
        async for r in self._send_text_or_image(event, title="九重天裂隙", text=msg):
            yield r

    @wf_group.command("奸商", alias={"void", "baro", "虚空商人"})
    async def wf_void(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate() or {}
        ws = await self._merge_extras_ws(ws)
        nodes = await self._build_nodes_map()
        items_map = await self._build_item_translation_map()
        msg = format_void_trader(ws, nodes_map=nodes, items_map=items_map)
        async for r in self._send_text_or_image(event, title="奸商", text=msg):
            yield r

    @wf_group.command("每日特惠", alias={"特惠", "daily", "dailydeals"})
    async def wf_daily_deals(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        items_map = await self._build_item_translation_map()
        msg = format_daily_deals(ws, items_map=items_map)
        async for r in self._send_text_or_image(event, title="每日特惠", text=msg):
            yield r

    @wf_group.command("突击", alias={"sortie"})
    async def wf_sortie(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        ws = await self._merge_extras_ws(ws)
        nodes = await self._build_nodes_map()
        msg = format_sortie(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="突击", text=msg):
            yield r

    @wf_group.command("执刑官猎杀", alias={"猎杀", "执行官", "执政官", "执刑官", "archon"})
    async def wf_archon(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            yield event.plain_result("worldstate unavailable, use /wf 更新")
            return
        ws = await self._merge_extras_ws(ws)
        nodes = await self._build_nodes_map()
        msg = format_archon_hunt(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="执刑官猎杀", text=msg):
            yield r

    @wf_group.command("仲裁", alias={"arbitration"})
    async def wf_arbitration(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate() or {}
        ws = await self._merge_extras_ws(ws)
        nodes = await self._build_nodes_map()
        msg = format_arbitration(ws, nodes_map=nodes)
        async for r in self._send_text_or_image(event, title="仲裁", text=msg):
            yield r

    @wf_group.command("钢铁奖励", alias={"steel_path", "steelpath", "steelreward"})
    async def wf_steel_path(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate() or {}
        ws = await self._merge_extras_ws(ws)
        msg = format_steel_path(ws)
        async for r in self._send_text_or_image(event, title="钢铁奖励", text=msg):
            yield r

    @wf_group.command("平原", alias={"夜灵平原", "夜灵平野", "福尔图娜", "魔胎之境", "扎里曼"})
    async def wf_cycles(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            ws = {}
        ws2 = dict(ws)
        ws2.update(await self._build_cycles_ws())
        msg = format_cycles(ws2)
        async for r in self._send_text_or_image(event, title="循环", text=msg):
            yield r

    @wf_group.command("轮换", alias={"双衍王境", "duviri"})
    async def wf_duviri(self, event: AstrMessageEvent):
        ws = await self._ensure_worldstate()
        if ws is None:
            ws = {}
        ws2 = dict(ws)
        ws2.update(await self._build_cycles_ws())
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
    async def wf_subscribe(self, event: AstrMessageEvent, topic: str = "", filter1: str = "", filter2: str = "", filter3: str = ""):
        try:
            self._maybe_start_background()
            if not self._subscribe_enabled and not self._is_admin(event):
                yield event.plain_result("订阅功能已暂时关闭，请联系管理员")
                return
            umo = event.unified_msg_origin
            uid = str(event.get_sender_id())
            uname = event.get_sender_name()
            if uname is not None and not isinstance(uname, str):
                uname = str(uname)
            platform_name = None
            group_id = None
            get_platform_name = getattr(event, "get_platform_name", None)
            if callable(get_platform_name):
                try:
                    platform_name = str(get_platform_name())
                except Exception:
                    platform_name = None
            get_group_id = getattr(event, "get_group_id", None)
            if callable(get_group_id):
                try:
                    gid = get_group_id()
                    if gid is not None:
                        group_id = str(gid)
                except Exception:
                    group_id = None

            if not topic.strip():
                async with self._sub_lock:
                    data = await self._sub_store.load()
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
                    "- /wf 订阅 裂缝 钢铁 天王星 防御",
                    "平原过滤示例：",
                    "- /wf 订阅 夜灵平原 夜晚",
                    "- /wf 订阅 平原 夜晚",
                    "- /wf 订阅 夜灵平原 夜晚 10  (提前10分钟预提醒)",
                    "取消订阅：",
                    "- /wf 取消订阅 <项目>  (同样支持过滤，如：/wf 取消订阅 裂缝 钢铁 防御)",
                    "- /wf 取消订阅 全部（仅清除本人订阅）",
                    "查看列表：/wf 订阅列表",
                    f"当前用户订阅：{', '.join([self._topic_label(t) for t in cur_topics]) or '-'}",
                ]
                if legacy_topics:
                    lines.append(f"旧版(仅会话)订阅：{', '.join([self._topic_label(t) for t in legacy_topics])}")
                msg = "\n".join(lines)
                async for r in self._send_text_or_image(event, title="订阅", text=msg):
                    yield r
                return

            t = self._normalize_sub_topic_args(topic, filter1, filter2, filter3)
            if t is None:
                yield event.plain_result("未知订阅项，先用 /wf 订阅 查看可选项目")
                return

            ws = await self._ensure_worldstate()
            title = self._topic_label(t)
            text = ""
            sig: str | None = None
            topic_ids: list[str] | None = None
            if ws is not None:
                title, text = await self._topic_text(t, ws)
                sig = self._topic_sig(t, ws)
                if self._notify_mode_for_topic(t) == "new_only":
                    topic_ids = self._topic_ids_for_topic(t, ws)

            async with self._sub_lock:
                data = await self._sub_store.load()
                added = self._sub_store.upsert_topic(
                    data, umo=umo, uid=uid, topic=t, user_name=uname, platform=platform_name, group_id=group_id
                )
            if sig is not None:
                self._sub_store.set_last_sig(data, umo=umo, uid=uid, topic=t, sig=sig)
            if topic_ids is not None:
                self._sub_store.set_last_ids(data, umo=umo, uid=uid, topic=t, ids=topic_ids)
            await self._sub_store.save(data)

            if added:
                yield event.plain_result(f"已订阅：{title}")
            else:
                yield event.plain_result(f"已在订阅列表：{title}")

            if ws is not None and text:
                async for r in self._send_text_or_image(event, title=title, text=text):
                    yield r
            else:
                yield event.plain_result("worldstate unavailable, use /wf 更新")
        except Exception:
            log_exception("wf_subscribe failed", category="subscription")
            yield event.plain_result("订阅处理失败，请查看控制台日志")

    @afilter.command("wf订阅", alias={"wfsub", "订阅wf"})
    async def wf_subscribe_flat(self, event: AstrMessageEvent, topic: str = "", filter1: str = "", filter2: str = "", filter3: str = ""):
        async for r in self.wf_subscribe(event, topic, filter1, filter2, filter3):
            yield r

    @wf_group.command("取消订阅", alias={"unsubscribe", "退订"})
    async def wf_unsubscribe(self, event: AstrMessageEvent, topic: str = "全部", filter1: str = "", filter2: str = "", filter3: str = ""):
        self._maybe_start_background()
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())
        s = (topic or "").strip()
        if not s or s in {"全部", "all", "All"}:
            async with self._sub_lock:
                data = await self._sub_store.load()
                changed = self._sub_store.clear_umo(data, umo=umo, uid=uid, include_legacy=False)
                if changed:
                    await self._sub_store.save(data)
            yield event.plain_result("已取消当前用户全部订阅" if changed else "当前用户暂无订阅")
            return

        t = self._normalize_sub_topic_args(s, filter1, filter2, filter3)
        if t is None:
            yield event.plain_result("未知订阅项，先用 /wf 订阅 查看可选项目")
            return

        async with self._sub_lock:
            data = await self._sub_store.load()
            changed = self._sub_store.remove_topic(data, umo=umo, uid=uid, topic=t)
            if changed:
                await self._sub_store.save(data)

        yield event.plain_result(f"已取消订阅：{self._topic_label(t)}" if changed else f"未订阅：{self._topic_label(t)}")

    @wf_group.command("订阅列表", alias={"subscriptions", "subs", "list"})
    async def wf_subscriptions(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())
        async with self._sub_lock:
            data = await self._sub_store.load()
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

    @wf_group.command("订阅测试", alias={"测试订阅", "推送测试", "test_sub", "sub_test"})
    async def wf_subscribe_test(self, event: AstrMessageEvent, topic: str = "", filter1: str = "", filter2: str = "", filter3: str = ""):
        self._maybe_start_background()
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())

        platform_name = None
        group_id = None
        get_platform_name = getattr(event, "get_platform_name", None)
        if callable(get_platform_name):
            try:
                platform_name = str(get_platform_name())
            except Exception:
                platform_name = None
        get_group_id = getattr(event, "get_group_id", None)
        if callable(get_group_id):
            try:
                gid = get_group_id()
                if gid is not None:
                    group_id = str(gid)
            except Exception:
                group_id = None

        async def send_topic(t: str) -> tuple[bool, str | None]:
            ws = await self._ensure_worldstate()
            if ws is None:
                return False, "worldstate unavailable, use /wf 更新"
            title, text = await self._topic_text(t, ws)
            ok = await self._push_to_subscriber(
                umo, platform=platform_name, user_id=uid, group_id=group_id, title=title, text=text
            )
            return ok, None

        if not topic.strip():
            async with self._sub_lock:
                data = await self._sub_store.load()
                entries = self._sub_store.list_entries(data)
            bound = next((e for e in entries if e.unified_msg_origin == umo and e.user_id == uid), None)
            if not bound or not bound.topics:
                yield event.plain_result("你还没有订阅项目，请先使用 /wf 订阅 <项目>")
                return
            first_topic = next(iter(bound.topics.keys()))
            ok, err = await send_topic(first_topic)
            if err:
                yield event.plain_result(err)
                return
            if ok:
                yield event.plain_result(f"已触发订阅测试：{self._topic_label(first_topic)}")
            else:
                yield event.plain_result("订阅测试发送失败（请查看控制台日志）")
            if platform_name == "aiocqhttp" and not group_id:
                yield event.plain_result("当前为私聊或未记录群号，@ 不会生效；请在群内重新订阅以记录群号")
            return

        if topic.strip() in {"全部", "all", "All"}:
            async with self._sub_lock:
                data = await self._sub_store.load()
                entries = self._sub_store.list_entries(data)
            bound = next((e for e in entries if e.unified_msg_origin == umo and e.user_id == uid), None)
            topics = sorted(list(bound.topics.keys())) if bound else []
            if not topics:
                yield event.plain_result("你还没有订阅项目，请先使用 /wf 订阅 <项目>")
                return
            ok_any = False
            for t in topics:
                ok, err = await send_topic(t)
                if err:
                    yield event.plain_result(err)
                    return
                ok_any = ok_any or ok
            yield event.plain_result("已触发订阅测试（全部）" if ok_any else "订阅测试发送失败（请查看控制台日志）")
            if platform_name == "aiocqhttp" and not group_id:
                yield event.plain_result("当前为私聊或未记录群号，@ 不会生效；请在群内重新订阅以记录群号")
            return

        t = self._normalize_sub_topic_args(topic, filter1, filter2, filter3)
        if t is None:
            yield event.plain_result("未知订阅项，先用 /wf 订阅 查看可选项目")
            return
        ok, err = await send_topic(t)
        if err:
            yield event.plain_result(err)
            return
        if ok:
            yield event.plain_result(f"已触发订阅测试：{self._topic_label(t)}")
        else:
            yield event.plain_result("订阅测试发送失败（请查看控制台日志）")
        if platform_name == "aiocqhttp" and not group_id:
            yield event.plain_result("当前为私聊或未记录群号，@ 不会生效；请在群内重新订阅以记录群号")

    @wf_group.command("管理", alias={"admin", "管理员"})
    async def wf_admin(
        self,
        event: AstrMessageEvent,
        action: str = "",
        arg1: str = "",
        arg2: str = "",
        arg3: str = "",
        arg4: str = "",
        arg5: str = "",
    ):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可用")
            return
        a0 = (action or "").strip()
        a1 = (arg1 or "").strip()
        a2 = (arg2 or "").strip()
        a3 = (arg3 or "").strip()
        a4 = (arg4 or "").strip()
        a5 = (arg5 or "").strip()

        if a0 in {"推送", "push"}:
            if a1 in {"开", "开启", "on", "enable", "启用"}:
                self._push_enabled = True
                setter = getattr(self, "put_kv_data", None)
                if callable(setter):
                    try:
                        await setter("wf_push_enabled", True)
                    except Exception:
                        log_debug("save kv wf_push_enabled failed", exc_info=True, category="main")
                yield event.plain_result("已开启全部推送")
                return
            if a1 in {"关", "关闭", "off", "disable", "停用"}:
                self._push_enabled = False
                setter = getattr(self, "put_kv_data", None)
                if callable(setter):
                    try:
                        await setter("wf_push_enabled", False)
                    except Exception:
                        log_debug("save kv wf_push_enabled failed", exc_info=True, category="main")
                yield event.plain_result("已关闭全部推送")
                return
            if a1 in {"状态", "status", ""}:
                yield event.plain_result(f"推送状态：{'开启' if self._push_enabled else '关闭'}")
                return
            yield event.plain_result("用法：/wf 管理 推送 开|关|状态")
            return

        if a0 in {"订阅开关", "订阅功能", "订阅权限", "subscribe_switch", "sub_switch"}:
            if a1 in {"开", "开启", "on", "enable", "启用"}:
                self._subscribe_enabled = True
                setter = getattr(self, "put_kv_data", None)
                if callable(setter):
                    try:
                        await setter("wf_subscribe_enabled", True)
                    except Exception:
                        log_debug("save kv wf_subscribe_enabled failed", exc_info=True, category="main")
                yield event.plain_result("已开启订阅功能")
                return
            if a1 in {"关", "关闭", "off", "disable", "停用"}:
                self._subscribe_enabled = False
                setter = getattr(self, "put_kv_data", None)
                if callable(setter):
                    try:
                        await setter("wf_subscribe_enabled", False)
                    except Exception:
                        log_debug("save kv wf_subscribe_enabled failed", exc_info=True, category="main")
                yield event.plain_result("已关闭订阅功能")
                return
            if a1 in {"状态", "status", ""}:
                yield event.plain_result(f"订阅功能：{'开启' if self._subscribe_enabled else '关闭'}")
                return
            yield event.plain_result("用法：/wf 管理 订阅开关 开|关|状态")
            return

        if a0 in {"清理图片缓存", "清理缓存", "清图", "clear_images"}:
            removed = await self._clear_image_cache()
            if removed < 0:
                yield event.plain_result("图片缓存目录不存在（无需清理）")
                return
            if removed == 0:
                yield event.plain_result("图片缓存为空（无需清理）")
                return
            yield event.plain_result(f"已清理图片缓存：{removed} 张")
            return

        if a0 in {"删除订阅", "清除订阅", "清订阅", "移除订阅"}:
            scope = a1 or "全部"
            async with self._sub_lock:
                data = await self._sub_store.load()
                items = data.get("items")
                if not isinstance(items, list):
                    items = []
                before = len(items)
                if scope in {"全部", "all", "All"}:
                    data["items"] = []
                elif scope in {"本群", "本会话", "当前会话", "会话"}:
                    umo = event.unified_msg_origin
                    data["items"] = [it for it in items if not (isinstance(it, dict) and it.get("umo") == umo)]
                elif scope.isdigit():
                    data["items"] = [it for it in items if not (isinstance(it, dict) and str(it.get("uid") or "") == scope)]
                else:
                    yield event.plain_result("用法：/wf 管理 删除订阅 全部|本群|<QQ>")
                    return
                after = len(data["items"])
                if after != before:
                    await self._sub_store.save(data)
                removed = before - after
            yield event.plain_result(f"已删除订阅：{removed} 条")
            return

        if a0 in {"订阅", "添加订阅", "订阅用户", "add_sub", "subscribe"}:
            target_uid = a1
            if not target_uid or not target_uid.isdigit():
                # 尝试从原始消息里抓取 @QQ
                raw = getattr(event, "message_str", "") or ""
                m = re.search(r"(\d{5,})", raw)
                if m:
                    target_uid = m.group(1)
            if not target_uid or not target_uid.isdigit():
                yield event.plain_result("用法：/wf 管理 订阅 <QQ> <项目> [过滤]")
                return
            topic = a2
            if not topic:
                yield event.plain_result("用法：/wf 管理 订阅 <QQ> <项目> [过滤]")
                return

            t = self._normalize_sub_topic_args(topic, a3, a4, a5)
            if t is None:
                yield event.plain_result("未知订阅项，先用 /wf 订阅 查看可选项目")
                return

            platform_name = None
            group_id = None
            get_platform_name = getattr(event, "get_platform_name", None)
            if callable(get_platform_name):
                try:
                    platform_name = str(get_platform_name())
                except Exception:
                    platform_name = None
            get_group_id = getattr(event, "get_group_id", None)
            if callable(get_group_id):
                try:
                    gid = get_group_id()
                    if gid is not None:
                        group_id = str(gid)
                except Exception:
                    group_id = None

            ws = await self._ensure_worldstate()
            title = self._topic_label(t)
            text = ""
            sig: str | None = None
            if ws is not None:
                title, text = await self._topic_text(t, ws)
                sig = self._topic_sig(t, ws)

            async with self._sub_lock:
                data = await self._sub_store.load()
                added = self._sub_store.upsert_topic(
                    data,
                    umo=event.unified_msg_origin,
                    uid=str(target_uid),
                    topic=t,
                    user_name=None,
                    platform=platform_name,
                    group_id=group_id,
                )
                if sig is not None:
                    self._sub_store.set_last_sig(data, umo=event.unified_msg_origin, uid=str(target_uid), topic=t, sig=sig)
                await self._sub_store.save(data)

            if added:
                yield event.plain_result(f"已为 {target_uid} 订阅：{title}")
            else:
                yield event.plain_result(f"{target_uid} 已在订阅列表：{title}")
            return

        yield event.plain_result(
            "管理员命令：\n"
            "- /wf 管理 推送 开|关|状态\n"
            "- /wf 管理 订阅开关 开|关|状态\n"
            "- /wf 管理 清理图片缓存\n"
            "- /wf 管理 删除订阅 全部|本群|<QQ>"
            "\n- /wf 管理 订阅 <QQ> <项目> [过滤]"
        )
