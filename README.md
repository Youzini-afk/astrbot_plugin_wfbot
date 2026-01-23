# astrbot_plugin_wfbot

一个 AstrBot 插件：数据获取思路（worldState / PublicExport / 多镜像数据源 / warframe.market），并提供 `/wf` 指令组（子命令以中文为主，英文为别名）与可选的图片模式。

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
  - `/wf 九重天` / `/wf 九重天裂隙`
  - `/wf 钢铁奖励`
  - `/wf 平原` / `/wf 福尔图娜` / `/wf 魔胎之境` / `/wf 扎里曼`
  - `/wf 轮换` / `/wf 双衍王境`
  - `/wf 电波`
  - `/wf 订阅 <项目>`（别名：`subscribe`、`关注`）
  - `/wf 取消订阅 <项目|全部>`（别名：`unsubscribe`、`退订`）
  - `/wf 订阅列表`（别名：`subs`、`list`）

- 图片模式（可选）
  - 开启后，`/wf` 输出会渲染成图片发送
  - 可选图片缓存（按数量保留）

- 订阅推送
  - 订阅会绑定到当前会话（使用 `unified_msg_origin`），当 worldstate 更新时会主动推送到该会话
  - 订阅会同时绑定订阅者 ID（同一群聊里不同人订阅互不影响）
  - 推送时会尝试 `@` 订阅者（取决于平台是否支持 At，以及适配器对 At 字段的实现）
  - 订阅项支持：警报 / 入侵 / 裂隙 / 钢铁裂隙 / 九重天裂隙 / 奸商 / 每日特惠 / 突击 / 执刑官猎杀 / 仲裁 / 钢铁奖励 / 平原(循环) / 轮换 / 电波
  - 推送内容会自动去重（基于数据签名），避免同一份内容重复推送
  - 裂缝订阅支持过滤（最多 3 个参数）：
    - `普通` / `钢铁` / `九重天`
    - 遗物纪元：`古纪` / `中纪` / `新纪` / `后纪` / `安魂`
    - 任务类型：`防御` / `移动防御` / `捕获` / `生存` / `歼灭` / `拦截` / `间谍` / `救援` / `破坏` / `挖掘` / `扰乱`
    - 示例：`/wf 订阅 裂缝 钢铁 防御`、`/wf 订阅 裂缝 古纪 捕获`
  - 平原/循环订阅支持按状态过滤：
    - 夜灵平原：`白天` / `夜晚`（示例：`/wf 订阅 夜灵平原 夜晚` 或 `/wf 订阅 平原 夜晚`）
    - 福尔图娜：`温暖` / `寒冷`（示例：`/wf 订阅 福尔图娜 寒冷`）
    - 预提醒：在状态切换前 N 分钟提醒（示例：`/wf 订阅 夜灵平原 夜晚 10` 或 `/wf 订阅 平原 夜晚 提前10`）

## 安装

1. 将整个插件文件夹放到：`AstrBot/data/plugins/astrbot_plugin_wfbot/`
2. 在 AstrBot 的 WebUI → 插件管理 → 重载插件
3. AstrBot 会根据本插件的 `requirements.txt` 自动安装依赖（包含 `aiohttp` 和 `Pillow`）

## 配置（WebUI）

插件根目录的 `_conf_schema.json` 会在 WebUI 自动生成配置页。

- `public_export_language`：PublicExport 语言（如 `zh` / `en`）
- `refresh.*`：各数据源刷新间隔（秒）
- `retention.*`：清理保留策略
- `render.*`：图片模式与图片缓存
- `subscriptions.cycles_default_offset_minutes`：平原/循环订阅默认提前/后置分钟数（>0 提前，<0 后置）
- `i18n.simplify_zh`：将繁体中文转换为简体（默认开启，主要用于 solNodes 等第三方数据源）

## 存储规范（对齐 AstrBot 官方）

- 小型 KV 数据（AstrBot >= 4.9.2）：可用 `put_kv_data/get_kv_data/delete_kv_data`（本插件目前主要使用文件存储）
- 大文件/缓存：存放在 `data/plugin_data/<插件名>/` 下（本插件会将数据写入 `data/plugin_data/astrbot_plugin_wfbot/warframe/`）

说明：旧版本配置字段仍保留但已隐藏（`invisible`），用于兼容历史配置。

## 致谢

本插件的数据获取与多镜像配置思路参考了 NyxBot 项目，在此对 NyxBot 的作者与贡献者表示感谢：

- NyxBot：`https://github.com/KingPrimes/NyxBot`

也感谢 Warframe 社区相关数据源与服务提供的公开接口（如 `warframestat`、`warframe.market` 等），使得插件能够稳定获取与展示游戏数据。
