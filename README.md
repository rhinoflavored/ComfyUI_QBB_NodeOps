# QBB NodeOps for ComfyUI

> Previous name: `ComfyUI_QBB_LoadHighlight`. This release uses the new **QBB NodeOps** folder, package, API and display names.

启动时在终端高亮每个自定义节点的加载过程，并在浏览器弹窗提示加载失败 / 部分失败的插件。

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

## Features

- 终端：每个插件加载前打印黄色高亮插件名 + 分割线
- 终端：失败时红色显示原因与 Traceback；部分失败标为 `[PARTIAL]`
- 终端：启动结束后打印 SUMMARY（成功 / 部分失败 / 完全失败）
- UI：失败时自动弹窗；顶栏按钮可随时重开报告
- UI：启动后后台检查已安装 git 插件的远程更新（多为 GitHub），有更新则弹窗提示
- UI：顶栏「插件检查」按钮（加载问题 + 可更新列表同弹窗）；支持单项更新 / 一键全部更新（`git pull --ff-only`）
- UI：网络设置（本地 VPN 代理端口 / 无代理时国内 GitHub 镜像）加速更新检查
- UI：导出完整加载日志、导出环境依赖（pip freeze 等）
- UI：单条问题复制图标 / 复制所有问题
- UI：中英随 ComfyUI 语言切换
- 菜单分类：**QBB → LoadHighlight**

## Why it does not rely on folder name order

ComfyUI runs each custom node's `prestartup_script.py` before importing `nodes`.
This plugin installs an import hook there and patches `nodes.load_custom_node`, so it does **not** need a `00-` folder prefix.

## Partial failure detection

Many packs swallow per-node import errors with `try/except` and still return success.
During each pack load, this plugin captures stdout/stderr/logging and marks `partial` when strong signals appear (Traceback / ImportError / failed to load, etc.).

## Category (QBB)

Menu path: **QBB → LoadHighlight**

Future QBB plugins can use the same top-level menu:

- Menu: `path: ["QBB", "YourPlugin"]`
- Nodes: `CATEGORY = "QBB/YourPlugin"`

## Language

Settings → `Comfy > Locale > Language`

- Chinese (`zh` / `zh-CN` ...) → Chinese UI
- Other locales → English UI

## API

- `GET /qbb_nodeops/report`
- `GET /qbb_nodeops/failures`
- `GET /qbb_nodeops/network_settings`
- `POST /qbb_nodeops/network_settings`
- `GET /qbb_nodeops/updates`
- `POST /qbb_nodeops/updates/refresh`
- `POST /qbb_nodeops/updates/pull` (`{ "path": "..." }` / `{ "paths": [...] }` / `{ "all": true }`)
- `GET /qbb_nodeops/export_defaults?kind=log|deps`
- `POST /qbb_nodeops/export_to_path`
- `POST /qbb_nodeops/export_environment_to_path`

## Notes

- Tracks `custom_nodes` packages only (not `comfy_extras` spam)
- Skips highlighting itself
- No extra pip dependencies
