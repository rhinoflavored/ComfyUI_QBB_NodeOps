// Page instructions describe current capabilities, not development history.
const copy = {
  overview: [
    ['查看插件和模型的占用空间、文件时间与工作流引用记录，筛选不再需要的资源。','Review plugin and model sizes, file dates and workflow references to find resources to remove.'],
    ['清理前请核对常用工作流。没有引用记录不代表从未使用；引用记录也不代表工作流执行成功。本地文件时间可能因复制或解压而改变，不能单独作为删除依据。','Check your workflows before cleanup. Missing references do not prove a resource is unused, and recorded references do not prove successful execution. Copying or extraction can change file dates.'],
    ['删除所选资源会将其移入回收站。请确认选择范围，重要文件建议先备份。','Selected resources are moved to the recycle bin. Review your selection and back up important files.']],
  plugins: [
    ['查看插件加载状态、错误信息和可用更新，定位需要处理的插件。','Inspect plugin loading status, errors and available updates.'],
    ['排查时先查看具体错误，再检查依赖或插件版本。更新可能影响已有工作流，建议先保存版本记录；修改后重启 ComfyUI 验证。','Read the error before changing dependencies or versions. Updates may affect workflows; save a version record first and restart ComfyUI to verify changes.']],
  instances: [
    ['选择版本记录、创建新实例或插件搬家。这三个功能相互独立，无需按顺序操作。','Choose version records, instance creation or plugin migration. These are independent features, not sequential steps.'],
    ['版本记录用于保存和比较环境信息；创建新实例用于安装独立环境；插件搬家用于在已有实例之间迁移插件。','Records save and compare environment information; creation installs a separate environment; migration transfers plugins between existing instances.']],
  snapshots: [
    ['恢复面向当前实例：先检查差异与删除项，确认后准备材料，再单独确认停机、执行和重启。任何阻碍均禁止执行；最终统一 Diff 为零差异、零未知才显示恢复完成。关闭面板后可重开查看任务，断线不自动重发操作。旧记录保留读取、对比和导出，不进入新实例恢复。','Restore targets this instance: review differences and removals, confirm preparation, then separately authorize stop, execution and restart. Blockers prevent execution. Completion requires a final unified Diff with no differences or unknowns. Reopen the panel to reconnect; lost responses never replay mutations. Legacy records retain reading, comparison and export, not new-instance recovery.'],
    ['范围固定为核心、插件、Python 包状态和 extra_model_paths.yaml。模型、输入输出、工作流、user 和外部资源不纳入；NodeOps 自身受保护。白名单配置可能包含密钥，分享前请检查。','Fixed scope: core, plugins, package state and extra_model_paths.yaml. Models, inputs, outputs, workflows, user and external resources are excluded. NodeOps is protected. Allowlisted configuration may contain secrets; review before sharing.'],
    ['核心和插件按保存策略记录精确 Git 来源或保存本地文件，白名单配置保存文件内容；Python 包保存可读取的安装文件与元数据，并打包为逐项校验的 ZIP。文件内容按 SHA-256 比较，相同对象跨记录复用。','Core and plugins record exact Git sources or save local files according to the capture policy; allowlisted configuration saves file contents. Packages save readable installed files and metadata in individually verified ZIP archives. Contents are compared by SHA-256; objects are reused across records.'],
    ['来源未知不再单独阻止恢复：完整且有效的安装文件 ZIP 可直接恢复，无需寻找原始 wheel 或重新构建。备份缺口、共享文件、路径冲突和 Python 不兼容仍会阻止执行；旧记录缺少文件证据时显示信息不足。','Unknown origin alone does not block restore: complete valid installed-file ZIPs restore directly without original wheels or rebuilding. Backup gaps, shared files, path conflicts and incompatible Python still block execution. Legacy records without file evidence show incomplete comparison.'],
    ['先预览再确认保存，扫描完成后预览 30 分钟失效。请停止工作流、更新、依赖安装和文件编辑；保存前后重新核验，但不是原子快照。空间不足、对象损坏或扫描期间变化会阻止发布；失败可能留下未引用对象。','Preview before saving; previews expire 30 minutes after scanning. Pause workflows, updates, installs and edits. Before/after verification is not an atomic snapshot. Insufficient space, corrupt objects or concurrent changes prevent publication; failed attempts may leave unreferenced objects.'],
    ['JSON 只有清单；文件包包含该记录引用的实际对象，导入不会安装或恢复。对象校验通过不等于运行环境可恢复。切换保存位置不搬动或删除旧记录。Phase 1 暂停自动和操作检查点，不修改原设置。','JSON contains only the manifest; bundles include referenced objects. Import never installs or restores. Object integrity does not establish runtime recoverability. Changing storage does not move or delete history. Automatic and operation checkpoints are paused for Phase 1 without changing saved settings.'],
  ],
  'create-instance': [
    ['从官方仓库下载 ComfyUI，并安装所选 Python、PyTorch 和 CPU/CUDA 环境。请选择空目录，程序与 Python 环境会分目录存放。','Download official ComfyUI and install the selected Python, PyTorch and CPU/CUDA build in an empty directory. Application and Python files are stored separately.'],
    ['版本选项按可用安装包联动筛选，但显卡驱动及第三方插件仍可能有兼容性限制。创建期间保持联网和 ComfyUI 后台运行，关闭本插件页面不会停止任务。','Version choices are filtered by package availability; GPU drivers and plugins can still impose compatibility limits. Keep the network and ComfyUI backend running; closing this panel does not stop creation.'],
    ['创建完成后运行实例目录中的启动器。新实例会自动加入插件搬家列表，不会自动复制已有插件、模型或配置。','Run the launcher in the instance folder after creation. The instance is registered for migration without copying existing plugins, models or configuration.']],
  migration: [
    ['选择来源实例、插件和目标实例，将所选插件复制到目标，并使用目标 Python 安装及检查依赖。','Choose source, plugins and destination. Selected plugins are copied and dependencies installed and checked using the target Python.'],
    ['目标必须是已有 ComfyUI 实例。安装依赖可能影响目标环境，建议先备份；模型和 ComfyUI 核心不随插件迁移。','The destination must be an existing ComfyUI instance. Dependency installation can affect it, so back up first. Models and ComfyUI core are not transferred.'],
    ['如选择回收原插件，确认前保持当前 ComfyUI 后台运行、暂停来源工作流，不开始新任务。用不同端口启动目标实例验证插件，再返回确认回收；依赖和文件检查通过后才移入回收站。回收完成后再重启来源实例。','If recycling originals, keep this ComfyUI backend running, pause source workflows and do not start another task. Verify the target on another port, then return to confirm recycling. Dependency and file checks must pass. Restart the source only after recycling.']],
  market: [
    ['搜索和筛选插件，查看仓库信息，或通过 Git 地址下载到当前实例。分类按钮再次点击可收起标签。','Search and filter plugins, inspect repository information or download from a Git URL into the current instance. Click a category again to collapse its tags.'],
    ['插件来自第三方，下载前请确认来源可信。下载完成后检查依赖并重启 ComfyUI；已下载不代表能够正常加载。仓库数据可能受缓存或网络限制影响。','Plugins are third-party software: verify their source. Check dependencies and restart ComfyUI after downloading. A download does not guarantee successful loading; repository data may be cached or limited by network access.']],
  ai: [
    ['连接远程 AI 接口或本地 OpenAI 兼容服务，用于主动发起的 AI 分析。填写服务地址、模型名称及所需密钥，可先点击验证通信再保存。','Connect a remote AI API or local OpenAI-compatible service for user-triggered analysis. Enter the URL, model and required key; test before saving.'],
    ['本地地址指运行 ComfyUI 的电脑。远程分析会发送相关诊断内容，请注意隐私；密钥保存在本机配置文件中。通信验证会发送简短消息，可能产生 API 费用，且不会保存表单。','Local addresses refer to the ComfyUI host. Remote analysis sends diagnostic content; consider privacy. Keys are stored in a local configuration file. Testing sends a short message, may incur charges and does not save settings.']],
  network: [
    ['选择直连、本地代理或 GitHub 镜像，为联网查询和下载设置连接方式。代理地址和端口应与代理软件实际监听的配置一致。','Choose direct access, a local proxy or a GitHub mirror for network queries and downloads. Match the proxy address and port to your proxy software.'],
    ['验证通信使用当前表单访问测试地址，不会保存设置。测试成功只代表该地址可访问，不保证所有下载源可用；GitHub 镜像不适用于 Python 安装包。','Testing uses the current form without saving. Success confirms access to the test address, not every download source. GitHub mirrors do not apply to Python packages.']],
  preferences: [
    ['设置插件的自动提醒和弹窗行为。设置保存在当前实例，重启后仍然生效。','Configure automatic notifications and popups. Preferences are saved for this instance and persist across restarts.'],
    ['关闭自动提醒不会停止后台任务，也不影响从顶栏手动打开插件。','Disabling notifications does not stop background tasks or prevent opening the plugin from the toolbar.']],
  export: [
    ['导出日志或依赖信息，便于排查问题、留档和向他人求助。确认文件名及保存方式后导出。','Export logs or dependency information for troubleshooting, reference or support. Confirm the file name and save method.'],
    ['导出内容可能包含本机路径和环境信息，分享前请检查敏感内容。依赖清单不是完整环境备份。','Exports may contain local paths and environment details. Review sensitive information before sharing. A dependency list is not a full environment backup.']],
};
export function pageHelpCopy(host, chinese) {
  let key;
  if(host.matches('.qbb-lh-export-modal')){
    key=host.closest('.qbb-lh-ai-settings')?'ai':host.closest('.qbb-lh-net')?'network':host.dataset.helpPage||'export';
  }else key=host.closest('[data-active-tab]')?.dataset.activeTab;
  if(['load','updates'].includes(key))key='plugins';
  return copy[key]?.map(pair=>pair[chinese?0:1]);
}
