/**
 * UI strings for ComfyUI_QBB_LoadHighlight.
 * Locale follows ComfyUI setting: Comfy.Locale (zh* => Chinese, otherwise English).
 */

export function getComfyLocale() {
  try {
    const fromManager = appLocaleFromExtensionManager();
    if (fromManager) return fromManager;
  } catch (_) {}

  try {
    const fromUi = window.app?.ui?.settings?.getSettingValue?.("Comfy.Locale");
    if (fromUi) return String(fromUi);
  } catch (_) {}

  try {
    const raw = localStorage.getItem("Comfy.Settings") || localStorage.getItem("Comfy.Locale");
    if (raw) {
      if (raw.startsWith("{")) {
        const parsed = JSON.parse(raw);
        if (parsed?.["Comfy.Locale"]) return String(parsed["Comfy.Locale"]);
      } else if (raw.length < 16) {
        return String(raw);
      }
    }
  } catch (_) {}

  return navigator.language || "en";
}

function appLocaleFromExtensionManager() {
  const app = window.app;
  const get = app?.extensionManager?.setting?.get;
  if (typeof get === "function") {
    const v = get.call(app.extensionManager.setting, "Comfy.Locale");
    if (v) return String(v);
  }
  return null;
}

export function isChineseLocale(locale = getComfyLocale()) {
  const loc = String(locale || "en").toLowerCase().replace("_", "-");
  return loc === "zh" || loc.startsWith("zh-");
}

const ZH = {
  menuIssues: (n) => `插件加载问题 (${n})`,
  menuReport: "插件加载报告",
  menuTooltip: "查看自定义节点加载报告",
  titleOk: "本次启动没有插件报错",
  subOk: "所有已跟踪的自定义节点包均正常加载（未检测到完全失败或部分节点异常）。",
  titleAttention: (bits, n) => (bits.length ? bits.join("，") : `有 ${n} 个插件需要注意`),
  bitFailed: (n) => `${n} 个完全失败`,
  bitPartial: (n) => `${n} 个部分节点异常`,
  subAttention:
    "完全失败：整个插件包未能导入。部分异常：插件包本身加载成功，但内部部分节点/模块导入失败。详情也已打印在终端。",
  badgeFailed: "完全失败",
  badgePartial: "部分失败",
  reasonPartial: "内部节点加载异常",
  reasonFailed: "IMPORT FAILED",
  viewPartial: "查看内部异常详情",
  viewTraceback: "查看 Traceback",
  exportLog: "导出完整日志",
  exportDeps: "导出环境依赖",
  copyList: "复制所有问题",
  copyOne: "复制这个问题",
  copied: "已复制",
  copyFailed: "复制失败",
  gotIt: "知道了",
  exportLogTitle: "导出完整日志",
  exportDepsTitle: "导出环境依赖",
  exportLogHint: "将写入 ComfyUI 所在电脑的本地路径（不是浏览器下载目录）。",
  exportDepsHint:
    "导出当前 ComfyUI 正在使用的 Python 环境依赖（含 pip freeze、Torch/CUDA 等信息），写入本机路径。",
  exportDir: "导出目录",
  exportFilename: "文件名",
  exportCancel: "取消",
  exportConfirm: "确认导出",
  exportWorking: "导出中...",
  needDir: "请填写导出目录",
  needFilename: "请填写文件名",
  exportSuccess: (path) => `导出成功：\n${path}`,
  exportFailed: (msg) => `导出失败：${msg}`,
  cannotFetch: "无法获取加载报告，请确认插件已正确加载。",
  dirPlaceholder: "例如 C:\\Users\\QT\\Desktop",
};

const EN = {
  menuIssues: (n) => `Load Issues (${n})`,
  menuReport: "Load Report",
  menuTooltip: "Open custom node load report",
  titleOk: "No plugin load errors this startup",
  subOk: "All tracked custom node packages loaded successfully (no hard failures or partial issues detected).",
  titleAttention: (bits, n) => (bits.length ? bits.join(", ") : `${n} plugin(s) need attention`),
  bitFailed: (n) => `${n} hard failure(s)`,
  bitPartial: (n) => `${n} partial node issue(s)`,
  subAttention:
    "Hard failure: the whole package failed to import. Partial: the package loaded, but some internal nodes/modules failed. Details are also printed in the terminal.",
  badgeFailed: "FAILED",
  badgePartial: "PARTIAL",
  reasonPartial: "Internal node/module load issues",
  reasonFailed: "IMPORT FAILED",
  viewPartial: "View internal issue details",
  viewTraceback: "View Traceback",
  exportLog: "Export Full Log",
  exportDeps: "Export Environment Deps",
  copyList: "Copy All Issues",
  copyOne: "Copy This Issue",
  copied: "Copied",
  copyFailed: "Copy failed",
  gotIt: "OK",
  exportLogTitle: "Export Full Log",
  exportDepsTitle: "Export Environment Dependencies",
  exportLogHint: "Writes to a local path on the ComfyUI machine (not the browser download folder).",
  exportDepsHint:
    "Exports the Python environment currently used by ComfyUI (pip freeze, Torch/CUDA, etc.) to a local path.",
  exportDir: "Export Directory",
  exportFilename: "File Name",
  exportCancel: "Cancel",
  exportConfirm: "Confirm Export",
  exportWorking: "Exporting...",
  needDir: "Please enter an export directory",
  needFilename: "Please enter a file name",
  exportSuccess: (path) => `Exported successfully:\n${path}`,
  exportFailed: (msg) => `Export failed: ${msg}`,
  cannotFetch: "Unable to fetch the load report. Please confirm the extension loaded correctly.",
  dirPlaceholder: "e.g. C:\\Users\\QT\\Desktop",
};

export function t() {
  return isChineseLocale() ? ZH : EN;
}
