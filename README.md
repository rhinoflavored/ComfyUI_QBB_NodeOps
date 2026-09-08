# QBB NodeOps for ComfyUI

> Previous name: `ComfyUI_QBB_LoadHighlight`. This release uses the new **QBB NodeOps** folder, package, API and display names.

QBB NodeOps 是一个面向 ComfyUI 自定义节点的运维工具：在启动时高亮加载过程，集中展示加载问题与更新，并提供插件搬家、环境导出、热点推送等功能。

UI 文案会跟随 ComfyUI 语言设置（`Comfy.Locale`）：中文界面显示中文，其它语言显示英文。

## Install via ComfyUI-Manager

1. Open **ComfyUI-Manager**
2. Search for **QBB NodeOps** (or `qbb-nodeops`)
3. Click **Install**, then restart ComfyUI

> If it does not appear yet, the node may still be pending Registry indexing. You can install from Git URL instead (see below).

## Install from Git

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/rhinoflavored/ComfyUI_QBB_NodeOps.git
```

Then restart ComfyUI.

Manual / junction install:

```powershell
cmd /c mklink /J "你的ComfyUI路径\custom_nodes\ComfyUI_QBB_NodeOps" "C:\Users\QT\Desktop\ComfyUI_QBB_NodeOps"
```

After restart, the terminal should show:

```text
[QBB NodeOps] Import hook installed ...
[QBB NodeOps] Hooked nodes.load_custom_node ...
```

## 功能

- 终端：每个插件加载前打印黄色高亮插件名 + 分割线
- 终端：失败时红色显示原因与 Traceback；部分失败标为 `[PARTIAL]`
- 终端：启动结束后打印 SUMMARY（成功 / 部分失败 / 完全失败）
- 插件检查：失败时自动提示，也能从 ComfyUI 顶栏随时打开；加载问题与可更新插件集中管理。
- 插件更新：检查已安装 Git 插件的远程更新；支持单项更新与一键更新（`git pull --ff-only`）。
- 网络设置：三选一使用直连、本地代理或国内镜像；用于 QBB NodeOps 的联网检查与更新请求，不会开启系统全局代理或 TUN 模式。
- 实例与插件搬家：识别多个 ComfyUI 实例，在实例间选择插件迁移；可为便携版创建轻量新环境，并让模型继续复用原目录。
- 导出：下载完整加载日志，或导出当前 Python 环境依赖（如 `pip freeze`）；由浏览器保存文件，插件不会把文件写入任意服务器路径。
- AI 设置与热点推送：可配置 OpenAI 兼容 API，用于分析加载错误与整理 ComfyUI/AI 热点。API Key 仅保存在本机插件配置中，不会回传到前端。
- UI：单条问题复制、复制所有问题，以及随 ComfyUI 语言自动显示中英文。
- 菜单分类：**QBB → NodeOps**

## Why it does not rely on folder name order

ComfyUI runs each custom node's `prestartup_script.py` before importing `nodes`.
This plugin installs an import hook there and patches `nodes.load_custom_node`, so it does **not** need a `00-` folder prefix.

## Partial failure detection

Many packs swallow per-node import errors with `try/except` and still return success.
During each pack load, this plugin captures stdout/stderr/logging and marks `partial` when strong signals appear (Traceback / ImportError / failed to load, etc.).

## 分类（QBB）

菜单路径：**QBB → NodeOps**

Future QBB plugins can use the same top-level menu:

- 菜单：`path: ["QBB", "YourPlugin"]`
- Nodes: `CATEGORY = "QBB/YourPlugin"`

## 语言

Settings → `Comfy > Locale > Language`

- Chinese (`zh` / `zh-CN` ...) → Chinese UI
- Other locales → English UI

## API（主要接口）

- `GET /qbb_nodeops/report`
- `GET /qbb_nodeops/failures`
- `GET /qbb_nodeops/network_settings`
- `POST /qbb_nodeops/network_settings`
- `GET /qbb_nodeops/updates`
- `POST /qbb_nodeops/updates/refresh`
- `POST /qbb_nodeops/updates/pull` (`{ "path": "..." }` / `{ "paths": [...] }` / `{ "all": true }`)
- `GET /qbb_nodeops/export?filename=...`
- `GET /qbb_nodeops/environment?filename=...`

## Notes

- Tracks `custom_nodes` packages only (not `comfy_extras` spam)
- Skips highlighting itself
- No extra pip dependencies
- 插件搬家会先复制并保留原插件；只有在用户明确确认且迁移成功后才会删除源插件。
- 新建便携式实例需要当前安装为 ComfyUI Portable；普通 Python 环境可以作为既有实例纳入管理与迁移。
