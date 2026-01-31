# astrbot_plugin_wfbot

An AstrBot plugin that combines worldState / PublicExport / multi-mirror sources / warframe.market, provides the `/wf` command group (Chinese subcommands with English aliases), and an optional image rendering mode.

**English** | [中文](README.md)

## Quick Navigation

- [Features](#features)
- [Data Sources and Future Direction](#data-sources-and-future-direction)
- [Installation](#installation)
- [How to verify @ mentions](#how-to-verify--mentions)
- [Configuration (WebUI)](#configuration-webui)
- [Local Completion Script (warframestat root)](#local-completion-script-warframestat-root)
- [Storage](#storage)
- [Changelog](#changelog)
- [Acknowledgements](#acknowledgements)

## Features

- `/wf` command group (Chinese primary, English aliases)
  - `/wf 帮助` (aliases: `help`, `菜单`, `指令`, `命令`)
  - `/wf 更新` (aliases: `refresh`, `update`, `刷新`)
  - `/wf 状态` (aliases: `status`, `info`)
  - `/wf 警报` (aliases: `alerts`)
  - `/wf 突击` (alias: `sortie`)
  - `/wf 执刑官猎杀` (aliases: `archon`, `猎杀`, `执行官/执政官/执刑官`)
  - `/wf 奸商` (aliases: `void`, `baro`, `虚空商人`)
  - `/wf 仲裁` (alias: `arbitration`)
  - `/wf 每日特惠` (aliases: `daily`, `特惠`)
  - `/wf 入侵` (alias: `invasions`)
  - `/wf 裂隙` / `/wf 裂缝` (alias: `fissure`)
  - `/wf 钢铁裂隙` / `/wf 钢铁裂缝`
  - `/wf 九重天` / `/wf 九重天裂隙` (Railjack void fissures: official worldstate does not include mission type; tier only; mission type is experimental and may be missing/inaccurate.)
  - `/wf 钢铁奖励`
  - `/wf 平原` / `/wf 福尔图娜` / `/wf 魔胎之境` / `/wf 扎里曼`
  - `/wf 轮换` / `/wf 双衍王境`
  - `/wf 电波`
  - `/wf 订阅 <item>` (aliases: `subscribe`, `关注`)
  - `/wf订阅 <item>` (flat alias for `/wf 订阅`)
  - `/wf 取消订阅 <item|all>` (aliases: `unsubscribe`, `退订`)
  - `/wf 订阅列表` (aliases: `subs`, `list`)
  - `/wf 订阅测试 <item|all>` (aliases: `测试订阅`, `推送测试`)
  - `/wf 清理图片缓存` (aliases: `清图`, `clear_image_cache`)

- Image rendering mode (optional)
  - When enabled, `/wf` output is rendered as an image
  - Optional image cache (keep N images)

- Data fetching & compatibility
  - Cycles fallback to cached worldstate on failure
  - Proxy fallback when direct request fails (if `proxy_url` or system proxy is set)

- Subscriptions & push
  - Subscriptions bind to session (`unified_msg_origin`); pushes are sent on worldstate updates
  - Also binds subscriber ID (per-user in the same group)
  - `/wf 取消订阅 全部` clears only current user's subscriptions
  - Attempts to `@` mention the subscriber if supported by the platform
  - Supported subscription items: alerts / invasions / fissures / steel fissures / railjack fissures / void trader / daily deals / sortie / archon hunt / arbitration / steel path rewards / cycles / duviri / nightwave
  - Notification policy modes:
    - `change` (default)
    - `new_only`
  - Per-type notification override in WebUI (see Configuration)
  - Fissure subscription filters (up to 3 params):
    - `普通` / `钢铁` / `九重天`
    - Planet (e.g. `天王星`, `火星`, `地球`, `虚空`)
    - Relic tiers: `古纪` / `中纪` / `前纪` / `后纪` / `安魂`
    - Mission types: `防御` / `移动防御` / `捕获` / `生存` / `歼灭` / `拦截` / `间谍` / `救援` / `破坏` / `挖掘` / `扰乱`
    - Examples: `/wf 订阅 裂缝 钢铁 防御`, `/wf 订阅 裂缝 古纪 捕获`
    - Example: `/wf 订阅 裂缝 钢铁 天王星 防御`
  - Plains/cycle subscription filters:
    - Plains of Eidolon: `白天` / `夜晚`
    - Orb Vallis: `温暖` / `寒冷`
    - Pre-alert: remind N minutes before state change

- Admin commands
  - `/wf 管理 推送 开|关|状态`: global push switch
  - `/wf 管理 订阅开关 开|关|状态`: enable/disable subscription entry
  - `/wf 管理 清理图片缓存`
  - `/wf 管理 删除订阅 全部|本群|<QQ>`
  - `/wf 管理 订阅 <QQ> <item> [filters]`
  - AstrBot admins still work; group-level admins can be configured in WebUI

## Data Sources and Future Direction

Currently integrated:
- Official worldstate (dynamic state)
- WarframeStat.us (structured worldstate + cycles + partial completion)
- Official Public Export (static data)
- warframe.market (basic market data)
- Local warframestat root file (manual completion for arbitration/railjack)

Future optional extensions:
- Riven prices: riven.market / Semlar / WarframeData
- Builds & tier lists: Overframe (needs crawling + cache/limit)
- Wiki deep data: Warframe Wiki (MediaWiki API / Lua modules)
- Database enhancement: WFCD/warframe-items

Ideas:
- **Arbitration/Railjack**: use warframestat root when official worldstate is incomplete.
- **Multi-source fallback**: avoid single-point failures.
- **Cache & rate limiting**: for market/overframe/wiki.
- **Module toggles**: reduce unnecessary requests.

## Installation

- Install from AstrBot plugin market (search `astrbot_plugin_wfbot`)
- Or manual:
1. Place the plugin folder under: `AstrBot/data/plugins/astrbot_plugin_wfbot/`
2. In AstrBot WebUI → Plugin Manager → Reload
3. Dependencies are auto-installed from `requirements.txt` (includes `aiohttp` and `Pillow`)

## How to verify @ mentions

On QQ (`aiocqhttp`), the plugin uses CQ `@`; other platforms will gracefully degrade to plain text.

A quick test using cycle pre-alert:

1. Subscribe: `/wf 订阅 夜灵平原 夜晚 提前1`
2. Wait for the change to night (or set `subscriptions.cycles_default_offset_minutes = 1`)
3. Confirm you received an `@` push

## Configuration (WebUI)

The `_conf_schema.json` in the plugin root is used to generate the WebUI config page.

- `public_export_language`: PublicExport language (e.g. `zh` / `en`)
- `refresh.*`: refresh intervals (seconds)
- `http.*`: network & proxy (`proxy_url` supported)
  - `no_proxy_suffixes` defaults to `warframe.com` and `warframestat.us`
- `retention.*`: cleanup policies
- `render.*`: image rendering & cache settings
- `subscriptions.cycles_default_offset_minutes`: cycle pre-alert offset (>0 pre, <0 post)
- `subscriptions.notify_mode_default`: default notify mode (`change` / `new_only`)
- `subscriptions.notify_modes.<type>`: per-type override (e.g. `alerts` / `fissures` / `void_trader`)
- `i18n.simplify_zh`: convert Traditional -> Simplified (default on)
- `store_user_name`: store nickname in subscriptions
- `store_platform`: store platform info in subscriptions
- `store_group_id`: store group ID (disabling may affect @)
- `sources.*`: data source strategy
  - `sources.warframestat_base_url`: warframestat base URL
  - `sources.warframestat_mirror_urls`: mirror list
  - `sources.warframestat_root_file`: local warframestat root JSON
- `log.enabled`: master log switch
- `log.main_enabled`
- `log.manager_enabled`
- `log.http_enabled`
- `log.public_export_enabled`
- `log.cache_enabled`
- `log.subscription_enabled`
- `log.cycle_enabled`
- `log.cycle_fetch_failures`
- `log.cycle_fetch_success`
- Legacy top-level `log_*` still works but is hidden (migrate to `log.*`)
- `admin.group_admins`: per-group admins (key=group_id, value=list of QQ IDs)
- `admin.session_admins`: per-session admins (advanced)
- List format: `[{ "group_id": "123456", "admins": ["111","222"] }]`

## Local Completion Script (warframestat root)

When warframestat is not reachable, you can fetch and store a local root file for completion:

```bash
python scripts/fetch_warframestat_root.py
```

Common options:

```bash
python scripts/fetch_warframestat_root.py --lang zh --out worldstate/warframestat_root.json
python scripts/fetch_warframestat_root.py --base https://your-mirror-domain
python scripts/fetch_warframestat_root.py --mirror https://mirror1 --mirror https://mirror2
```

The output path can be specified via `sources.warframestat_root_file` (defaults to `worldstate/warframestat_root.json`).

## Storage

- Small KV data (AstrBot >= 4.9.2): `put_kv_data/get_kv_data/delete_kv_data` (this plugin mainly uses file storage)
- Large files/cache: `data/plugin_data/<plugin>/` (writes to `data/plugin_data/astrbot_plugin_wfbot/warframe/`)
- Subscription storage contains user info (UID/group/nick); you can disable with `store_*` options

Legacy config keys are kept (hidden) for compatibility.

## Changelog

- 2026-02-01
  - Moved log config to `log.*` with master switch and module toggles (legacy `log_*` compatible).
  - Added subscription privacy toggles (`store_user_name`/`store_platform`/`store_group_id`).
  - PublicExport manifest download retry + empty file protection.
  - Background loop error log throttling (60s).
  - Tried to fix railjack fissure mission type; still missing in official worldstate.
  - Added `sources.*` strategy (warframestat endpoints/root completion + cache).
  - Missing module data now returns clear “source unavailable” messages.
  - Added warframestat base/mirror config and local completion file support.
  - Added local fetch script: `scripts/fetch_warframestat_root.py`.

- 2026-01-31
  - Simplified Chinese fallback: `zhconv` when OpenCC is unavailable.
  - Simplify PublicExport item names during cache.
  - Added dependency: `zhconv`.
  - Fissure subscriptions support planet filter (e.g. “Uranus”).
  - Cycle fallback fills Earth cycle.
  - Proxy fallback for direct requests.
  - Default `no_proxy_suffixes` includes `warframestat.us`.

## Acknowledgements

Data/mirror design inspired by NyxBot:
- NyxBot: `https://github.com/KingPrimes/NyxBot`

Thanks to the Warframe community data providers (e.g. `warframestat`, `warframe.market`) for making these integrations possible.
