import { previewConflicts } from "./conflicts.js";
import { t } from "./i18n.js";
import { nodeopsFetch } from "./http.js";
import { hooks, issueCount, state, updateCount } from "./shared.js";
import { preferences } from "./preferences.js";

export async function fetchReport() {
  const res = await nodeopsFetch("/qbb_nodeops/report");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
export async function fetchUpdates() {
  const res = await nodeopsFetch("/qbb_nodeops/updates");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
export async function pullPlugins({ paths = null, all = false } = {}) {
  const body = all ? { all: true } : { paths: paths || [] };
  const res = await nodeopsFetch("/qbb_nodeops/updates/pull", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.results) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}
export async function fetchMigrationScan(sourceInstanceId = state.migrationSourceId) {
  const res = await nodeopsFetch(`/qbb_nodeops/migration/scan?source_instance_id=${encodeURIComponent(sourceInstanceId)}`, {cache: 'reload'});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
export async function fetchInstances() {
  const res = await nodeopsFetch("/qbb_nodeops/instances", {cache: 'reload'});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data.instances || [];
}
export async function addInstanceFromUI(path, name) {
  const res = await nodeopsFetch("/qbb_nodeops/instances", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  state.cachedInstances = data.instances || [];
  return data.instance;
}
export async function fetchMigrationStatus() {
  const res = await nodeopsFetch("/qbb_nodeops/migration/status", {signal: AbortSignal.timeout(15000)});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  if (!data.status) throw new Error('Invalid migration status');
  return data;
}
export async function loadMigrationData(force = false) {
  if (state.migrationLoading && !force) return;
  state.migrationLoading = true;
  void pollMigration();
  try {
    const [scan, instances] = await Promise.all([fetchMigrationScan(), fetchInstances()]);
    state.cachedMigration = {...scan, status: state.cachedMigration?.status || scan.status};
    state.cachedInstances = instances;
    state.migrationScanError = '';
    const validPaths = new Set((scan.plugins || []).map((p) => p.path));
    state.migrationSelection = new Set([...state.migrationSelection].filter((p) => validPaths.has(p)));
  } catch (err) {
    state.cachedMigration = { plugins: [], ...state.cachedMigration };
    state.migrationScanError = err.message || String(err);
    console.warn("[QBB NodeOps] migration scan failed", err);
  } finally {
    state.migrationLoading = false;
    const modalOpen = document.querySelector(".qbb-lh-overlay");
    if (modalOpen) {
      hooks.showAttentionModal(state.cachedReport || { attention: [] }, state.cachedUpdates, { activeTab: modalOpen.querySelector('.qbb-lh-modal')?.dataset.activeTab || "migration" });
    }
  }
}
let migrationPoll = null;
export function pollMigration() {
  if (!migrationPoll) migrationPoll = watchMigration().finally(() => { migrationPoll = null; });
  return migrationPoll;
}
async function watchMigration() {
  let previousSignature = "";
  let failures = 0;
  while (true) {
    try {
      const status = await fetchMigrationStatus();
      failures = 0;
      state.migrationConnectionError = '';
      state.cachedMigration = { ...(state.cachedMigration || {}), status };
      const key = [status.started_at, status.source_root, status.destination_root, status.mode].join('|');
      if (status.mode !== 'new' && status.status !== 'idle' && key !== state.migrationDismissedJob) {
        state.migrationResult = {
          ...status, jobKey: key, sourceLabel: status.source_root || '',
          targetLabel: status.destination_root || '', pluginNames: status.plugin_names || [],
          startedAt: status.started_at, finishedAt: status.finished_at,
        };
      } else if (status.status === 'idle' || status.mode === 'new') {
        state.migrationResult = null;
      }
      const modalOpen = document.querySelector(".qbb-lh-overlay");
      const signature = [
        key,
        status.status,
        status.phase,
        status.current,
        status.completed,
        status.total,
        status.error,
        status.logs?.length || 0,
      ].join("|");
      if (modalOpen && signature !== previousSignature) {
        const page = modalOpen.querySelector('.qbb-lh-modal')?.dataset.activeTab;
        if (page === 'migration') hooks.showAttentionModal(state.cachedReport || { attention: [] }, state.cachedUpdates, { activeTab: page });
      }
      previousSignature = signature;
      if (status.status !== "running") return status;
    } catch (err) {
      console.warn("[QBB NodeOps] migration status failed", err);
      previousSignature = '';
      failures++;
      state.migrationConnectionError = '连接暂时中断，正在重试；后台任务状态待确认，请勿重复提交。';
      const modal = document.querySelector('.qbb-lh-overlay');
      if (modal?.querySelector('.qbb-lh-modal')?.dataset.activeTab === 'migration') {
        hooks.showAttentionModal(state.cachedReport || {attention: []}, state.cachedUpdates, {activeTab: 'migration'});
      }
    }
    await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * 2 ** Math.min(failures, 4), 10000)));
  }
}
export async function startMigrationFromUI(destinationRoot, mode = "existing", deleteSource = false) {
  const S = t();
  const selected = [...state.migrationSelection];
  if ((mode !== "instance" && !destinationRoot) || (mode === "instance" && !state.migrationTargetId)) {
    alert(S.migrationNeedTarget);
    return;
  }
  if (!selected.length) {
    alert(S.migrationNeedPlugins);
    return;
  }
  if(!["existing","instance"].includes(mode)){
    alert("请先在“创建新实例”中创建目标实例，再进行插件搬家。");
    return;
  }
  const approved = await previewConflicts({destination_root: destinationRoot, plugins: selected, mode,
    source_instance_id: state.migrationSourceId, target_instance_id: state.migrationTargetId});
  if (!approved) return;
  deleteSource = !!approved.deleteSource;
  state.migrationDeleteSource = deleteSource;

  try {
    const res = await nodeopsFetch("/qbb_nodeops/migration/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        destination_root: destinationRoot,
        plugins: selected,
        mode,
        delete_source: deleteSource,
        source_instance_id: state.migrationSourceId,
        target_instance_id: state.migrationTargetId,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    const source = (state.cachedInstances || []).find((item) => item.id === state.migrationSourceId);
    const target = (state.cachedInstances || []).find((item) => item.id === state.migrationTargetId);
    const plugins = (state.cachedMigration?.plugins || []).filter((plugin) => state.migrationSelection.has(plugin.path));
    state.migrationResult = {
      jobKey: [data.started_at, data.source_root, data.destination_root, data.mode].join('|'),
      status: data.status || "running",
      sourceLabel: source ? `${source.name} · ${source.comfyui_root}` : "",
      targetLabel: mode === "instance" ? (target ? `${target.name} · ${target.comfyui_root}` : "") : destinationRoot,
      pluginNames: plugins.map((plugin) => plugin.name),
      logs: data.logs || [],
      error: data.error || "",
      phase: data.current || data.phase || "",
      completed: data.completed || 0,
      total: data.total || 0,
      validation: data.validation || null,
    };
    state.migrationDestination = destinationRoot;
    state.migrationWatchingJob = true;
    state.cachedMigration = { ...(state.cachedMigration || {}), status: data };
    hooks.showAttentionModal(state.cachedReport || { attention: [] }, state.cachedUpdates, { activeTab: "migration" });
    pollMigration();
  } catch (err) {
    alert(S.migrationFailed + ": " + (err.message || err));
  }
}
export async function pollUpdates({ reopenIfOpen = true, autoPopup = true } = {}) {
  let lastStatus = state.cachedUpdates?.status || "";
  for (let i = 0; i < 90; i++) {
    try {
      const data = await fetchUpdates();
      state.cachedUpdates = data;
      hooks.applyButtonState();

      const modal = document.querySelector(".qbb-lh-overlay");
      const modalOpen = !!modal;
      const activeModalTab = modal?.querySelector(".qbb-lh-tab.active")?.dataset?.tab || "";
      const foregroundDialogOpen = !!document.querySelector(".qbb-lh-export-overlay");
      const finished = ["done", "partial", "error"].includes(data.status);
      // Only rebuild modal when check finishes (not every 2s while running),
      // otherwise recreating the overlay jumps the page scroll to top.
      // Never replace an open Migration tab in the background: rebuilding the
      // modal destroys the destination input while the user is typing.
      if (reopenIfOpen && modalOpen && !foregroundDialogOpen && activeModalTab !== "migration" && finished && lastStatus !== data.status) {
        hooks.showAttentionModal(state.cachedReport || { attention: [] }, data);
      }
      lastStatus = data.status || "";

      if (finished) {
        const issues = issueCount(state.cachedReport);
        const upd = updateCount(data);
        if (autoPopup && !state.startupPopupShown && ((preferences.popup_errors && issues > 0) || (preferences.popup_updates && upd > 0)) && !modalOpen) {
          state.startupPopupShown = true;
          hooks.showAttentionModal(state.cachedReport || { attention: [] }, data);
        } else if (!state.startupPopupShown) {
          state.startupPopupShown = true;
        }
        return data;
      }
    } catch (err) {
      console.warn("[QBB NodeOps] poll updates failed", err);
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  return state.cachedUpdates;
}
export async function openReport() {
  try {
    const fresh = await fetchReport();
    state.cachedReport = fresh;
    try {
      state.cachedUpdates = await fetchUpdates();
    } catch (_) {}
    hooks.applyButtonState();
    hooks.showAttentionModal(fresh, state.cachedUpdates);
  } catch (err) {
    if (state.cachedReport) hooks.showAttentionModal(state.cachedReport, state.cachedUpdates);
    else alert(t().cannotFetch);
    console.warn("[QBB NodeOps] reopen failed", err);
  }
}
