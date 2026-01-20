# astrbot_plugin_wfbot

一个 AstrBot 插件：对齐 NyxBot 的数据获取思路（worldState / PublicExport / 多镜像数据源 / warframe.market），并提供 `/wf` 指令组（含中文别名）与可选的图片模式。

## 功能

- `/wf` 指令组（含中文别名）
  - `/wf help`（别名：`帮助`、`菜单`）
  - `/wf refresh`（别名：`刷新`、`更新`）
  - `/wf status`（别名：`状态`）
  - `/wf alerts`（别名：`警报`）
  - `/wf fissure [normal|steel|storm]`（别名：`裂隙`；也支持 `普通/钢铁/风暴`）
  - `/wf invasions`（别名：`入侵`）
  - `/wf cycle`（别名：`循环`、`轮换`）
  - `/wf void`（别名：`奸商`、`虚空商人`）

- 图片模式（可选）
  - 开启后，`/wf` 的输出会渲染成图片发送
  - 可选图片缓存（按数量保留）

## 安装

1. 将整个插件文件夹放到：`AstrBot/data/plugins/astrbot_plugin_wfbot/`
2. 在 AstrBot 的 WebUI → 插件管理 → 重载插件
3. 如需依赖安装，AstrBot 会根据本插件的 `requirements.txt` 自动安装（包含 `aiohttp` 和 `Pillow`）

## 配置（WebUI）

插件根目录的 `_conf_schema.json` 会在 AstrBotWebUI 自动生成配置页。

- `public_export_language`：PublicExport 语言（如 `zh` / `en`）
- `refresh.*`：各数据源刷新间隔（秒）
- `retention.*`：清理保留策略
- `render.*`：图片模式与图片缓存

说明：旧版本配置字段仍保留但已隐藏（`invisible`），用于兼容历史配置。
