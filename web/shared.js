import { t, isChineseLocale } from "./i18n.js";

export const BTN_ID = "qbb-nodeops-menu-btn";
export const GROUP_CLASS = "qbb-nodeops-button-group";

export const state = {
  cachedReport: null,
  cachedUpdates: null,
  cachedMigration: null,
  cachedInstances: null,
  migrationSelection: new Set(),
  migrationDestination: "",
  migrationMode: "",
  migrationDeleteSource: false,
  migrationSourceId: "current",
  migrationTargetId: "",
  migrationLoading: false,
  migrationStep: 1,
  migrationWatchingJob: false,
  migrationResult: null,
  migrationConnectionError: '',
  migrationScanError: '',
  migrationDismissedJob: '',
  addInstanceFormOpen: false,
  addInstanceDraft: { mode: "new", path: "", name: "" },
  startupPopupShown: false,
  modalLayout: null,
  modalRestoreBounds: null,
  recentlyUpdatedItems: [],
};

export const hooks = {
  showAttentionModal: null,
  pollUpdates: null,
  applyButtonState: null,
  openReport: null,
};

export function resetMigrationDraft() {
  state.migrationSelection = new Set();
  state.migrationDestination = "";
  state.migrationMode = "";
  state.migrationDeleteSource = false;
  state.migrationTargetId = "";
  state.migrationStep = 1;
  state.addInstanceFormOpen = false;
  state.migrationWatchingJob = false;
}

export function trUI(zh, en) { return isChineseLocale() ? zh : en; }

export function markPluginsUpdated(entries) {
  for (const entry of entries || []) {
    if (!entry || !entry.path) continue;
    state.recentlyUpdatedItems = state.recentlyUpdatedItems.filter((x) => x.path !== entry.path);
    state.recentlyUpdatedItems.push({
      name: entry.name || "",
      path: entry.path,
      remote_url: entry.remote_url || "",
      local_sha: entry.local_sha || entry.local_sha_before || "",
      remote_sha: entry.remote_sha || entry.local_sha || "",
      branch: entry.branch || "",
      done: true,
    });
  }
}
export function getDoneUpdateItems(pending) {
  const pendingPaths = new Set((pending || []).map((p) => p.path));
  return state.recentlyUpdatedItems.filter((x) => x.path && !pendingPaths.has(x.path));
}
export function normalizeItems(data) {
  if (!data) return [];
  if (Array.isArray(data.attention)) return data.attention;
  return [...(data.failures || []), ...(data.partials || [])];
}
export function issueCount(data) {
  return data?.attention_count || normalizeItems(data).length || 0;
}
export function updateCount(data) {
  return data?.update_count || (data?.updates || []).length || 0;
}
export function updateDisplay(data) {
  if (!data || data.status === "idle") return "—";
  if (data.status === "running") return "…";
  const count = updateCount(data);
  if (data.status === "error" || (data.error_count > 0 && !count)) return trUI("检测失败", "Check failed");
  if (data.status === "partial" || data.error_count > 0) return count + "+";
  return String(count);
}
export function buttonLabel() {
  return '🔧 NodeOps';
}
export function networkModeLabel(mode) {
  const S = t();
  if (mode === "proxy") return S.netModeProxy;
  if (mode === "mirror") return S.netModeMirror;
  return S.netModeDirect;
}
export function formatIssueForCopy(item, S) {
  const status = item.status || (item.success ? "partial" : "failed");
  const statusLabel = status === "partial" ? S.badgePartial : S.badgeFailed;
  const reason = item.reason || (status === "partial" ? S.reasonPartial : S.reasonFailed);
  const detailText =
    (Array.isArray(item.partial_issues) && item.partial_issues.length
      ? item.partial_issues.join("\n\n")
      : "") || item.traceback || "";

  const lines = [
    "ComfyUI custom node load issue",
    `Plugin: ${item.name || "(unknown)"}`,
    `Status: ${statusLabel} (${status})`,
    `Path: ${item.path || ""}`,
    `Reason: ${reason}`,
  ];
  if (detailText) {
    lines.push("Details:");
    lines.push(detailText);
  }
  lines.push("");
  lines.push("Please help diagnose this ComfyUI custom node loading problem.");
  return lines.join("\n");
}
export function formatAllIssuesForCopy(items, S) {
  return items.map((item) => formatIssueForCopy(item, S)).join("\n\n" + "=".repeat(60) + "\n\n");
}
export async function copyText(text, button, S) {
  const oldHtml = button.innerHTML;
  const oldTitle = button.title;
  try {
    await navigator.clipboard.writeText(text);
    button.title = S.copied;
    button.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>`;
    setTimeout(() => {
      button.innerHTML = oldHtml;
      button.title = oldTitle;
    }, 1200);
  } catch {
    button.title = S.copyFailed;
    setTimeout(() => {
      button.innerHTML = oldHtml;
      button.title = oldTitle;
    }, 1200);
  }
}
export function copyIconSvg() {
  // Overlapping squares copy icon
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="9" y="9" width="11" height="11" rx="2"></rect>
  <path d="M5 15V7a2 2 0 0 1 2-2h8"></path>
</svg>`;
}
export function shortSha(sha) {
  const s = String(sha || "");
  return s.length > 7 ? s.slice(0, 7) : s;
}
