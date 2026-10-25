# QBB NodeOps for ComfyUI

**在一个面板里排查插件加载、管理资源、保存环境版本和维护多个 ComfyUI 实例。**

[English](README.en.md) · [使用指南](docs/usage.md) · [更新说明](CHANGELOG.md) · [问题反馈](https://github.com/rhinoflavored/ComfyUI_QBB_NodeOps/issues)

QBB NodeOps 提供 ComfyUI 网页管理界面与启动诊断，不向工作流添加节点。界面跟随 ComfyUI 语言设置显示中文或英文。项目原名为 **ComfyUI_QBB_LoadHighlight**。

## 功能

- **插件管理**：查看正常、部分加载和失败状态、错误与 Traceback；检查 Git 更新，执行单项或批量快进更新；导出日志和依赖清单。
- **资源管理**：查看插件、模型的磁盘占用、文件时间及已记录的工作流引用，筛选后移入回收站。
- **版本记录**：预览并保存受管环境，比较当前状态，导入/导出清单和文件包，按确认计划恢复当前实例。
- **创建实例**：从官方仓库建立独立 ComfyUI，选择 Python、PyTorch 和 CPU/CUDA 组合，检查本机硬件与安装结果。
- **插件搬家**：向已有实例复制插件，用目标 Python 安装及检查依赖；验证后可确认回收来源插件。
- **插件市场**：搜索公开插件目录，按分类和标签筛选，查看仓库信息或通过 Git 地址下载。
- **可选 AI 分析**：手动连接远程或本地 OpenAI 兼容服务，分析诊断信息。

## 安装

需要可运行的 ComfyUI、**Python 3.11+**，以及供仓库下载和更新使用的系统 **Git**。主要开发与验证环境为 Windows，其他系统和插件组合需要实际验证。

在 ComfyUI 的 `custom_nodes` 目录执行：

```bash
git clone https://github.com/rhinoflavored/ComfyUI_QBB_NodeOps.git
```

使用**启动该 ComfyUI 的 Python** 安装 `requirements.txt`。Windows 便携版可在包含 `python_embeded` 和 `ComfyUI` 的目录执行：

```powershell
.\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\ComfyUI_QBB_NodeOps\requirements.txt
```

其他环境请用对应解释器执行 `-m pip install -r <插件目录>/requirements.txt`。额外依赖为 `Send2Trash`。

重启 ComfyUI，刷新浏览器，通过顶栏入口或 **QBB → NodeOps** 打开。在运行 ComfyUI 的电脑上通过 `localhost` 或 `127.0.0.1` 访问；管理接口限制本机请求。从 LoadHighlight 升级时只保留一份插件目录，避免重复加载。

## 版本记录与恢复

当前受管范围为 **ComfyUI 核心、插件、Python 包状态和 `extra_model_paths.yaml`**。模型、输入输出、工作流、`user` 和外部资源不在范围内，NodeOps 自身受保护。版本记录不等于整机备份。

1. 暂停工作流、更新、依赖安装和文件编辑，预览保存范围、排除项、备份缺口与空间需求，再确认保存。
2. 选择记录，对比当前实例。默认**宽松模式**只核对名称，同名组件跳过；需要核对版本、来源和内部文件时切换**严格模式**。
3. 核对新增、替换和删除项，检查并准备材料，再确认开始恢复。
4. 打开独立恢复窗口后，**手动关闭原 ComfyUI**，等待恢复窗口提示完成，再用原启动脚本**手动启动 ComfyUI**。
5. 查看结果；需要确认恢复后的内容时，重新执行严格对比并测试常用工作流。

本地文件与可读取的 Python 安装文件可保存为带校验索引的组件 ZIP，内容对象按 SHA-256 复用。轻量 Git 记录可能仅保存来源和提交；缺失文件、共享文件冲突及解释器不兼容仍可能阻止恢复。

恢复沿用已确认的对比计划，结果可能表示“操作已执行、未重新扫描环境”，不代表内部文件、GPU 或工作流已经验证一致。宽松模式完成也不代表版本一致。旧记录保留读取、对比和导出，恢复能力取决于实际保存的证据与材料。详见[使用指南](docs/usage.md)。

## 使用前了解

- 引用记录来自可识别的工作流提交；没有记录不代表从未使用，文件时间也不能单独作为删除依据。
- 部分加载检测依据捕获的错误信号，是启发式判断。插件更新后需重启并测试工作流。
- 新建实例与插件搬家相互独立。搬家不迁移模型或核心；安装依赖会改变目标环境。
- AI 请求仅在主动操作时发起，可能发送日志、路径和依赖信息。密钥保存在本机配置中，未加密。
- 网络支持直连、本地代理及 GitHub 镜像；镜像不替代 Python、PyPI、PyTorch 或 AI 下载源。

## 更新与反馈

在插件目录执行 `git pull --ff-only`，用 ComfyUI 的 Python 更新依赖，然后重启并刷新网页。存在本地修改时请先保留修改。

反馈请附操作步骤、ComfyUI/Python 版本和完整错误，移除密钥、令牌和私人路径。不要上传本机配置、数据库或整个快照对象库。

开发检查见[使用指南](docs/usage.md)。采用 [MIT 许可证](LICENSE)。
