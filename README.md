# ComfyUI_QBB_LoadHighlight

启动时在终端高亮每个自定义节点的加载过程，并在浏览器弹窗提示加载失败 / 部分失败的插件。

UI 文案会跟随 ComfyUI 语言设置（`Comfy.Locale`）：中文界面显示中文，其它语言显示英文。

## Install via ComfyUI-Manager

1. Open **ComfyUI-Manager**
2. Search for **QBB LoadHighlight** (or `qbb-loadhighlight`)
3. Click **Install**, then restart ComfyUI

> If it does not appear yet, the node may still be pending Registry indexing. You can install from Git URL instead (see below).

## Install from Git

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/YOUR_GITHUB_USERNAME/ComfyUI_QBB_LoadHighlight.git
```

Then restart ComfyUI.

Manual / junction install:

```powershell
cmd /c mklink /J "你的ComfyUI路径\custom_nodes\ComfyUI_QBB_LoadHighlight" "C:\Users\QT\Desktop\ComfyUI_QBB_LoadHighlight"
```

After restart, the terminal should show:

```text
[ComfyUI_QBB_LoadHighlight] Import hook installed ...
[ComfyUI_QBB_LoadHighlight] Hooked nodes.load_custom_node ...
```

## Features

- 终端：每个插件加载前打印黄色高亮插件名 + 分割线
- 终端：失败时红色显示原因与 Traceback；部分失败标为 `[PARTIAL]`
- 终端：启动结束后打印 SUMMARY（成功 / 部分失败 / 完全失败）
- UI：失败时自动弹窗；顶栏按钮可随时重开报告
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

- `GET /qbb_loadhighlight/report`
- `GET /qbb_loadhighlight/failures`
- `GET /qbb_loadhighlight/export_defaults?kind=log|deps`
- `POST /qbb_loadhighlight/export_to_path`
- `POST /qbb_loadhighlight/export_environment_to_path`

## Notes

- Tracks `custom_nodes` packages only (not `comfy_extras` spam)
- Skips highlighting itself
- No extra pip dependencies
