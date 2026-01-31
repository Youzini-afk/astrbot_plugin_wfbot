# astrbot_plugin_wfbot

一个 AstrBot 插件：数据获取思路（worldState / PublicExport / 多镜像数据源 / warframe.market），并提供 `/wf` 指令组（子命令以中文为主，英文为别名）与可选的图片模式。

**中文** | [English](README.en.md)

## 快速导航

- [功能](#功能)
- [数据源未来优化方向](#数据源未来优化方向)
- [安装](#安装)
- [如何验证订阅会-你](#如何验证订阅会-你)
- [配置（WebUI）](#配置webui)
- [本地补全脚本（warframestat root）](#本地补全脚本warframestat-root)
- [存储规范](#存储规范)
- [更新历史](#更新历史)
- [致谢](#致谢)

## 功能

- `/wf` 指令组（子命令以中文为主，英文为别名）
  - `/wf 帮助`（别名：`help`、`菜单`、`指令`、`命令`）
  - `/wf 更新`（别名：`refresh`、`update`、`刷新`）
  - `/wf 状态`（别名：`status`、`info`）
  - `/wf 警报`（别名：`alerts`）
  - `/wf 突击`（别名：`sortie`）
  - `/wf 执刑官猎杀`（别名：`archon`、`猎杀`、`执行官/执政官/执刑官`）
  - `/wf 奸商`（别名：`void`、`baro`、`虚空商人`）
  - `/wf 仲裁`（别名：`arbitration`）
  - `/wf 每日特惠`（别名：`daily`、`特惠`）
  - `/wf 入侵`（别名：`invasions`）
  - `/wf 裂隙` / `/wf 裂缝`（别名：`fissure`）
  - `/wf 钢铁裂隙` / `/wf 钢铁裂缝`
  - `/wf 九重天` / `/wf 九重天裂隙`（九重天裂缝，官方 worldstate 不提供任务类型字段，当前仅能显示纪元；任务类型显示为实验性推断，可能为空或不准确。）
  - `/wf 钢铁奖励`
  - `/wf 平原` / `/wf 福尔图娜` / `/wf 魔胎之境` / `/wf 扎里曼`
  - `/wf 轮换` / `/wf 双衍王境`
  - `/wf 电波`
  - `/wf 订阅 <项目>`（别名：`subscribe`、`关注`）
  - `/wf订阅 <项目>`（平铺别名，等价于 `/wf 订阅`）
  - `/wf 取消订阅 <项目|全部>`（别名：`unsubscribe`、`退订`）
  - `/wf 订阅列表`（别名：`subs`、`list`）
  - `/wf 订阅测试 <项目|全部>`（别名：`测试订阅`、`推送测试`）
  - `/wf 清理图片缓存`（别名：`清图`、`clear_image_cache`）

- 图片模式（可选）
  - 开启后，`/wf` 输出会渲染成图片发送
  - 可选图片缓存（按数量保留）

- 数据获取与兼容
  - cycles 拉取失败时可从缓存 worldstate 回退，减少空数据
  - 直连失败时可回退代理（如配置了 `proxy_url` 或系统代理）

- 订阅推送
  - 订阅会绑定到当前会话（使用 `unified_msg_origin`），当 worldstate 更新时会主动推送到该会话
  - 订阅会同时绑定订阅者 ID（同一群聊里不同人订阅互不影响）
  - `/wf 取消订阅 全部` 只清除当前用户的订阅，不影响同群其他人
  - 推送时会尝试 `@` 订阅者（取决于平台是否支持 At，以及适配器对 At 字段的实现）
  - 订阅项支持：警报 / 入侵 / 裂隙 / 钢铁裂隙 / 九重天裂隙 / 奸商 / 每日特惠 / 突击 / 执刑官猎杀 / 仲裁 / 钢铁奖励 / 平原(循环) / 轮换 / 电波
  - 通知策略支持两种模式：
    - `change`：内容变化就通知（默认）
    - `new_only`：仅出现“新条目”时通知（如新警报、新裂缝）
  - 通知策略可按类型在 WebUI 单独配置（见“配置”）
  - 裂缝订阅支持过滤（最多 3 个参数）：
    - `普通` / `钢铁` / `九重天`
    - 星球（如 `天王星` / `火星` / `地球` / `虚空` 等）
    - 遗物纪元：`古纪` / `中纪` / `前纪` / `后纪` / `安魂`
    - 任务类型：`防御` / `移动防御` / `捕获` / `生存` / `歼灭` / `拦截` / `间谍` / `救援` / `破坏` / `挖掘` / `扰乱`
    - 示例：`/wf 订阅 裂缝 钢铁 防御`、`/wf 订阅 裂缝 古纪 捕获`
    - 示例：`/wf 订阅 裂缝 钢铁 天王星 防御`
  - 平原/循环订阅支持按状态过滤：
    - 夜灵平原：`白天` / `夜晚`（示例：`/wf 订阅 夜灵平原 夜晚` 或 `/wf 订阅 平原 夜晚`）
    - 福尔图娜：`温暖` / `寒冷`（示例：`/wf 订阅 福尔图娜 寒冷`）
    - 预提醒：在状态切换前 N 分钟提醒（示例：`/wf 订阅 夜灵平原 夜晚 10` 或 `/wf 订阅 平原 夜晚 提前10`）

- 管理员命令
  - `/wf 管理 推送 开|关|状态`：全局开启/关闭推送
  - `/wf 管理 订阅开关 开|关|状态`：全局开启/关闭“订阅功能”入口
  - `/wf 管理 清理图片缓存`
  - `/wf 管理 删除订阅 全部|本群|<QQ>`：清空所有/当前会话/指定用户订阅
  - `/wf 管理 订阅 <QQ> <项目> [过滤]`：为指定用户添加订阅
  - AstrBot 管理员依旧有效；可在 WebUI 为每个群配置额外管理员（见“配置”）

## 数据源未来优化方向

当前已接入：
- 官方 worldstate（动态世界状态）
- WarframeStat.us（结构化 worldstate 与循环/部分补全）
- 官方 Public Export（静态数据）
- warframe.market（市场基础数据）
- 本地 warframestat root 文件（可用于仲裁/九重天补全）

未来可选扩展：
- Riven 价格：riven.market / Semlar / WarframeData
- 配装与排行：Overframe（需解析并做缓存/限流）
- Wiki 深度数据：Warframe Wiki (MediaWiki API / Lua 模块)
- 数据库增强：WFCD/warframe-items

优化想法：
- **仲裁/九重天**：官方 worldstate 字段不全时，使用 warframestat root worldstate 进行补全。
- **多源回退**：官方与社区数据源互为 fallback，避免单点故障导致空数据。
- **缓存与节流**：对第三方源（market/overframe/wiki）开启缓存，避免频繁访问。
- **按需开关**：按模块启用数据源，减少不必要请求与潜在限流。

## 安装
- 直接在astrbot的插件市场搜索astrbot_plugin_wfbot，点击安装即可
- 或者：
1. 将整个插件文件夹放到：`AstrBot/data/plugins/astrbot_plugin_wfbot/`
2. 在 AstrBot 的 WebUI → 插件管理 → 重载插件
3. AstrBot 会根据本插件的 `requirements.txt` 自动安装依赖（包含 `aiohttp` 和 `Pillow`）

## 如何验证订阅会 @ 你

在 QQ（`aiocqhttp`）下，本插件会优先使用 CQ 码 `@`，其它平台会优雅降级为普通消息。

建议用“平原/循环预提醒”来快速触发一次推送：

1. 先订阅：`/wf 订阅 夜灵平原 夜晚 提前1`
2. 等到夜灵平原即将切换到夜晚前 1 分钟（或者把 `subscriptions.cycles_default_offset_minutes` 设为 1，然后直接 `/wf 订阅 夜灵平原 夜晚`）
3. 观察是否收到带 `@` 的推送消息

## 配置（WebUI）

插件根目录的 `_conf_schema.json` 会在 WebUI 自动生成配置页。

- `public_export_language`：PublicExport 语言（如 `zh` / `en`）
- `refresh.*`：各数据源刷新间隔（秒）
- `http.*`：网络与代理（支持设置 `proxy_url`）
  - `no_proxy_suffixes` 默认包含 `warframe.com` 与 `warframestat.us`
- `retention.*`：清理保留策略
- `render.*`：图片模式与图片缓存（支持自定义宽度/字号/字体路径/emoji 处理方式）
- `subscriptions.cycles_default_offset_minutes`：平原/循环订阅默认提前/后置分钟数（>0 提前，<0 后置）
- `subscriptions.notify_mode_default`：订阅通知策略默认值（`change` / `new_only`）
- `subscriptions.notify_modes.<类型>`：按类型覆盖通知策略（如 `alerts` / `fissures` / `void_trader`）
- `i18n.simplify_zh`：将繁体中文转换为简体（默认开启，主要用于 solNodes 等第三方数据源）
- `store_user_name`：订阅存储是否保留用户昵称
- `store_platform`：订阅存储是否保留平台信息
- `store_group_id`：订阅存储是否保留群号（关闭可能影响 @）
- `sources.*`：数据源策略（warframestat 端点/根 worldstate 与补全缓存）
  - `sources.warframestat_base_url`：warframestat 基础地址
  - `sources.warframestat_mirror_urls`：warframestat 镜像列表（按顺序尝试）
  - `sources.warframestat_root_file`：本地 warframestat root JSON（手动补全用）
- `log.enabled`：日志总开关（关闭后屏蔽本插件所有日志）
- `log.main_enabled`：主流程日志
- `log.manager_enabled`：数据刷新/后台循环日志
- `log.http_enabled`：网络请求相关日志
- `log.public_export_enabled`：PublicExport 下载相关日志
- `log.cache_enabled`：缓存读写相关日志
- `log.subscription_enabled`：订阅存储/推送相关日志
- `log.cycle_enabled`：cycles 日志总开关
- `log.cycle_fetch_failures`：是否输出 cycles 拉取失败的 WARN 日志
- `log.cycle_fetch_success`：是否输出 cycles 拉取成功/回退的 INFO/DEBUG 日志
- 兼容：旧的顶层 `log_*` 配置仍可使用，但已隐藏，建议迁移到 `log.*`
- `admin.group_admins`：按群配置管理员（键=群号，值=QQ号列表）
- `admin.session_admins`：按会话配置管理员（高级，需 unified_msg_origin）
- 配置为列表格式：`[{ "group_id": "123456", "admins": ["111","222"] }]`

## 本地补全脚本（warframestat root）

当 warframestat 无法访问时，可手动拉取并保存到本地文件供补全使用：

```bash
python scripts/fetch_warframestat_root.py
```

常用参数：

```bash
python scripts/fetch_warframestat_root.py --lang zh --out worldstate/warframestat_root.json
python scripts/fetch_warframestat_root.py --base https://你的镜像域名
python scripts/fetch_warframestat_root.py --mirror https://镜像1 --mirror https://镜像2
```

脚本写入的文件可通过 `sources.warframestat_root_file` 指定（默认会尝试 `worldstate/warframestat_root.json`）。

## 存储规范

- 小型 KV 数据（AstrBot >= 4.9.2）：可用 `put_kv_data/get_kv_data/delete_kv_data`（本插件目前主要使用文件存储）
- 大文件/缓存：存放在 `data/plugin_data/<插件名>/` 下（本插件会将数据写入 `data/plugin_data/astrbot_plugin_wfbot/warframe/`）
- 订阅存储会包含用户信息（如 UID/群号/昵称），可通过 `store_*` 选项关闭部分字段

说明：旧版本配置字段仍保留但已隐藏（`invisible`），用于兼容历史配置。

## 更新历史

- 2026-02-01
  - 日志配置迁移到 `log.*`，新增日志总开关与子模块细分开关（兼容旧 `log_*`）。
  - 新增订阅隐私存储开关（`store_user_name`/`store_platform`/`store_group_id`）。
  - PublicExport manifest 下载增加重试与空文件保护。
  - 后台循环错误日志节流（默认 60s/次）。
  - 尝试修复/wf 九重天数据为空的问题，未能解决任务类型为空。
  - 新增数据源策略配置 `sources.*`（warframestat 子端点/根 worldstate 补全与缓存）。
  - 当对应模块数据缺失时，不再返回 “-”，而是提示“数据源不可用 / worldstate 不可用”。
  - 支持自定义 warframestat 基础地址/镜像列表与本地补全文件。
  - 增加本地补全拉取脚本：`scripts/fetch_warframestat_root.py`。

- 2026-01-31
  - 简体中文输出增强：当 OpenCC 不可用时自动回退到 `zhconv` 转换。
  - PublicExport 物品名在缓存阶段即进行简体化，减少繁体漏网。
  - 依赖补充：新增 `zhconv`。
  - 裂缝订阅支持按星球过滤（如“天王星”）。
  - cycles 回退补全地球周期，减少回退缺失。
  - 直连失败时可回退代理请求（若配置了代理/系统代理）。
  - `no_proxy_suffixes` 默认加入 `warframestat.us`。

## 致谢

本插件的数据获取与多镜像配置思路参考了 NyxBot 项目，在此对 NyxBot 的作者与贡献者表示感谢：

- NyxBot：`https://github.com/KingPrimes/NyxBot`

也感谢 Warframe 社区相关数据源与服务提供的公开接口（如 `warframestat`、`warframe.market` 等），使得插件能够稳定获取与展示游戏数据。
