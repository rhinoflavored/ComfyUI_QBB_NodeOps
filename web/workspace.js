import { renderMarket } from "./market.js";
import { renderCreateInstance } from "./create-instance.js";
import { updateDisplay } from "./shared.js";
import { installPageHelp } from "./page-help.js";
import { renderSnapshots } from "./snapshots.js";
import { renderCleanup } from "./cleanup.js";
import { showPreferences } from "./preferences.js";
import { renderPluginTable, selectPluginFilter } from "./plugin-table.js";
import { renderMigration } from "./migrate.js";
import { t } from "./i18n.js";
import { ensureStyles } from "./styles.js";
import { nodeopsFetch } from "./http.js";
import {
  copyText,
  formatAllIssuesForCopy,
  formatIssueForCopy,
  hooks,
  markPluginsUpdated,
  networkModeLabel,
  normalizeItems,
  state,
  trUI,
  updateCount,
} from "./shared.js";
import {
  requestAi,
  showAiOutputDialog,
  showAiSettingsDialog,
  showExportDialog,
  showNetworkSettingsDialog,
} from "./dialogs.js";
import {
  fetchUpdates,
  fetchInstances,
  loadMigrationData,
  pollUpdates,
  pullPlugins,
} from "./api.js";

export function showAttentionModal(data, updatesData = state.cachedUpdates, options = {}) {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-overlay");
  if (existing?.classList.contains('qbb-is-minimized')) return;
  const focused = document.activeElement;
  const isTextEditor =
    focused instanceof HTMLInputElement ||
    focused instanceof HTMLTextAreaElement ||
    focused?.isContentEditable;
  const isEditingQbb =
    isTextEditor &&
    !!(existing?.contains(focused) || focused?.closest?.(".qbb-lh-export-overlay"));
  // Async update checks and scans can call this while a user is entering a
  // path, API key, model name, or search text. Removing/recreating the modal
  // at that moment destroys the focused field, so keep the current UI intact.
  if (isEditingQbb) return;
  const previousModal = existing?.querySelector(".qbb-lh-modal");
  if (!previousModal) {
    state.modalLayout = null;
    state.modalRestoreBounds = null;
  }
  if (previousModal && !state.modalRestoreBounds) {
    const rect = previousModal.getBoundingClientRect();
    state.modalLayout = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    };
  }
  const pageX = window.scrollX || window.pageXOffset || 0;
  const pageY = window.scrollY || window.pageYOffset || 0;
  const prevBodyScroll = existing?.querySelector(".qbb-lh-modal-body")?.scrollTop || 0;
  const prevTab =
    options.activeTab ||
    existing?.querySelector('.qbb-lh-modal')?.dataset.activeTab ||
    existing?.querySelector(".qbb-lh-tab.active")?.dataset?.tab ||
    null;
  if (existing) existing.remove();

  const S = t();
  const items = normalizeItems(data);
  const failureCount = data?.failure_count || items.filter((i) => (i.status || "failed") === "failed").length || 0;
  const partialCount = data?.partial_count || items.filter((i) => i.status === "partial").length || 0;
  const updates = Array.isArray(updatesData?.updates) ? updatesData.updates : [];
  const updCount = updateCount(updatesData);
  const updatesChecking =
    !updatesData || updatesData.status === "idle" || updatesData.status === "running";
  const updatesError = updatesData?.status === "error";

  let activeTab = ["instances", "create-instance", "snapshots", "overview", "plugins", "load", "updates", "migration", "market"].includes(prevTab) ? prevTab : null;
  if (activeTab === "load") { selectPluginFilter("failed"); activeTab = "plugins"; }
  if (activeTab === "updates") { selectPluginFilter("updates"); activeTab = "plugins"; }
  if (!activeTab) activeTab = 'overview';

  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-overlay";

  const modal = document.createElement("div");
  modal.className = "qbb-lh-modal qbb-workspace";
  if (state.modalLayout) {
    const width = Math.min(state.modalLayout.width, Math.max(320, window.innerWidth - 12));
    const height = Math.min(state.modalLayout.height, Math.max(260, window.innerHeight - 12));
    const left = Math.min(Math.max(6, state.modalLayout.left), Math.max(6, window.innerWidth - width - 6));
    const top = Math.min(Math.max(6, state.modalLayout.top), Math.max(6, window.innerHeight - height - 6));
    Object.assign(modal.style, {
      position: "fixed",
      left: `${left}px`,
      top: `${top}px`,
      width: `${width}px`,
      height: `${height}px`,
    });
  }

  const header = document.createElement("div");
  header.className = "qbb-lh-modal-header";

  // Modeless window behaviour: drag the title area to move; the native
  // bottom-right handle (CSS resize) changes its size. Inputs/buttons retain
  // their normal interaction and never start a drag.
  header.addEventListener("pointerdown", (event) => {
    if (state.modalRestoreBounds) return;
    if (event.button !== 0 || event.target.closest("button, input, textarea, select, a, label")) return;
    const rect = modal.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    modal.setPointerCapture?.(event.pointerId);
    event.preventDefault();
    const move = (moveEvent) => {
      const width = modal.getBoundingClientRect().width;
      const height = modal.getBoundingClientRect().height;
      const left = Math.min(Math.max(6, moveEvent.clientX - offsetX), Math.max(6, window.innerWidth - width - 6));
      const top = Math.min(Math.max(6, moveEvent.clientY - offsetY), Math.max(6, window.innerHeight - height - 6));
      Object.assign(modal.style, { position: "fixed", left: `${left}px`, top: `${top}px` });
      state.modalLayout = { left, top, width, height };
    };
    const stop = () => {
      modal.removeEventListener("pointermove", move);
      modal.removeEventListener("pointerup", stop);
      modal.removeEventListener("pointercancel", stop);
    };
    modal.addEventListener("pointermove", move);
    modal.addEventListener("pointerup", stop);
    modal.addEventListener("pointercancel", stop);
  });
  const layoutObserver = new ResizeObserver(() => {
    if (!modal.isConnected) {
      layoutObserver.disconnect();
      return;
    }
    if (state.modalRestoreBounds) return;
    const rect = modal.getBoundingClientRect();
    state.modalLayout = { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
  });
  layoutObserver.observe(modal);
  const title = document.createElement("h2");
  title.className = "qbb-lh-modal-title";
  title.textContent = S.modalTitle || S.menuReport;
  const chips = document.createElement("div");
  chips.className = "qbb-lh-chips";

  function addChip(cls, label) {
    const chip = document.createElement("button");
    chip.type = 'button';
    chip.className = "qbb-lh-chip " + cls;
    chip.textContent = label;
    chip.style.cursor = 'pointer';
    chip.title = trUI('查看对应插件','View matching plugins');
    chip.setAttribute('aria-label', label + ' · ' + chip.title);
    chip.onclick = () => {
      selectPluginFilter(cls === 'update' ? 'updates' : cls);
      setTab('plugins');
      body.scrollTop = 0;
    };
    chips.appendChild(chip);
  }

  // Always show summary chips on the title row.
  addChip("failed", (S.chipFailed || S.bitFailed)(failureCount));
  addChip("partial", (S.chipPartial || S.bitPartial)(partialCount));
  addChip(
    "update",
    updatesChecking
      ? S.chipChecking || S.menuUpdatesChecking
      : trUI("更新 ", "Updates ") + updateDisplay(updatesData),
  );
  header.appendChild(title);
  header.appendChild(chips);

  const tabs = document.createElement("div");
  tabs.className = "qbb-lh-tabs";
  const tabsLeft = document.createElement("div");
  tabsLeft.className = "qbb-lh-tabs-left";
  const tabsRight = document.createElement("div");
  tabsRight.className = "qbb-lh-tabs-right";

  const tabOverview = document.createElement("button");
  tabOverview.type = "button";
  tabOverview.className = "qbb-lh-tab";
  tabOverview.dataset.tab = "overview";
  tabOverview.textContent = trUI("资源管理", "Resources");
  const tabPlugins = document.createElement("button");
  tabPlugins.type = "button";
  tabPlugins.className = "qbb-lh-tab";
  tabPlugins.dataset.tab = "plugins";
  tabPlugins.textContent = trUI("插件管理", "Plugins");
  const tabMigration = document.createElement("button");
  tabMigration.type = "button";
  tabMigration.className = "qbb-lh-tab";
  tabMigration.dataset.tab = "migration";
  tabMigration.textContent = trUI("实例管理", "Instances");
  const tabMarket = document.createElement("button");
  tabMarket.type = "button";
  tabMarket.className = "qbb-lh-tab";
  tabMarket.dataset.tab = "market";
  tabMarket.textContent = trUI("插件市场", "Marketplace");
  tabsLeft.append(tabOverview, tabPlugins, tabMigration, tabMarket);

  const settingsBtn = document.createElement("button");
  settingsBtn.type = "button";
  settingsBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
  settingsBtn.textContent = trUI("设置", "Settings");
  settingsBtn.setAttribute("aria-haspopup", "menu");
  settingsBtn.setAttribute("aria-expanded", "false");
  settingsBtn.title = trUI("网络、AI 与通知", "Network, AI and notifications") + " · " + networkModeLabel(updatesData?.network_mode || "direct");
  let dismissSettingsMenu = null;
  function closeSettingsMenu() {
    tabsRight.querySelector(".qbb-settings-menu")?.remove();
    settingsBtn.setAttribute("aria-expanded", "false");
    if (dismissSettingsMenu) {
      document.removeEventListener("pointerdown", dismissSettingsMenu, true);
      dismissSettingsMenu = null;
    }
  }
  settingsBtn.onclick = () => {
    if (tabsRight.querySelector(".qbb-settings-menu")) { closeSettingsMenu(); return; }
    const menu = document.createElement("div");
    menu.className = "qbb-settings-menu";
    menu.setAttribute("role", "menu");
    for (const [label, open] of [
      [S.netSettings, showNetworkSettingsDialog],
      [S.aiSettings, showAiSettingsDialog],
      [trUI("通知设置", "Notifications"), showPreferences],
    ]) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "qbb-lh-btn qbb-lh-btn-ghost";
      item.setAttribute("role", "menuitem");
      item.textContent = label;
      item.onclick = () => { closeSettingsMenu(); open(); };
      menu.appendChild(item);
    }
    tabsRight.appendChild(menu);
    settingsBtn.setAttribute("aria-expanded", "true");
    dismissSettingsMenu = (event) => {
      if (event.target === settingsBtn || menu.contains(event.target)) return;
      closeSettingsMenu();
    };
    document.addEventListener("pointerdown", dismissSettingsMenu, true);
  };
  tabsRight.appendChild(settingsBtn);

  tabs.appendChild(tabsLeft);
  tabs.appendChild(tabsRight);

  const body = document.createElement("div");
  body.className = "qbb-lh-modal-body";

  const footer = document.createElement("div");
  footer.className = "qbb-lh-modal-footer";
  const footerLeft = document.createElement("div");
  footerLeft.className = "qbb-lh-footer-left";
  const footerRight = document.createElement("div");
  footerRight.className = "qbb-lh-footer-right";
  footer.appendChild(footerLeft);
  footer.appendChild(footerRight);

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function renderPluginsFooter() {
    clear(footerLeft);
    clear(footerRight);
    if (items.length) {
      const copyBtn = document.createElement("button");
      copyBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
      copyBtn.textContent = S.copyList;
      copyBtn.onclick = () => copyText(formatAllIssuesForCopy(items, S), copyBtn, S);
      footerLeft.appendChild(copyBtn);
    }
    const exportBtn = document.createElement("button");
    exportBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
    exportBtn.textContent = S.exportLog;
    exportBtn.onclick = () => showExportDialog({ kind: "log" });
    footerLeft.appendChild(exportBtn);
    const depsBtn = document.createElement("button");
    depsBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
    depsBtn.textContent = S.exportDeps;
    depsBtn.onclick = () => showExportDialog({ kind: "deps" });
    footerLeft.appendChild(depsBtn);
    const refreshBtn = document.createElement("button");
    refreshBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
    refreshBtn.textContent = S.updateRefresh;
    refreshBtn.onclick = async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = S.menuUpdatesChecking;
      try {
        await nodeopsFetch("/qbb_nodeops/updates/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        hooks.showAttentionModal(
          state.cachedReport || { attention: [] },
          { status: "running", updates: [], update_count: 0, network_mode: updatesData?.network_mode },
          { activeTab: "plugins" },
        );
        pollUpdates({ reopenIfOpen: true });
      } catch (err) {
        console.warn("[QBB NodeOps] refresh updates failed", err);
        refreshBtn.disabled = false;
        refreshBtn.textContent = S.updateRefresh;
      }
    };
    footerLeft.appendChild(refreshBtn);
    if (updCount) {
      const updateAllBtn = document.createElement("button");
      updateAllBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
      updateAllBtn.textContent = S.updateAll;
      updateAllBtn.onclick = async () => {
        if (!confirm(S.updateConfirmAll(updCount))) return;
        updateAllBtn.disabled = true;
        updateAllBtn.textContent = S.updateAllWorking;
        try {
          const pendingSnapshot = updates.slice();
          const result = await pullPlugins({ all: true });
          const okResults = (result.results || []).filter((r) => r.ok);
          markPluginsUpdated(
            okResults.map((r) => {
              const prev = pendingSnapshot.find((p) => p.path === r.path) || {};
              return {
                ...prev,
                name: r.name || prev.name,
                path: r.path || prev.path,
                local_sha: r.local_sha || prev.remote_sha || prev.local_sha,
                remote_sha: r.local_sha || prev.remote_sha || prev.local_sha,
                branch: r.branch || prev.branch,
                remote_url: prev.remote_url || "",
              };
            }),
          );
          alert(S.updateAllResult(result.succeeded || result.updated || 0, result.failed || 0));
          state.cachedUpdates = result.report || (await fetchUpdates());
          hooks.applyButtonState();
          hooks.showAttentionModal(state.cachedReport || { attention: [] }, state.cachedUpdates, { activeTab: "plugins" });
        } catch (err) {
          console.warn("[QBB NodeOps] pull all failed", err);
          alert(S.updateError(err.message || err));
          updateAllBtn.disabled = false;
          updateAllBtn.textContent = S.updateAll;
        }
      };
      footerRight.appendChild(updateAllBtn);
    }
  }

  function renderMigrationTab() {
    clear(body);
    clear(footerLeft);
    clear(footerRight);
    const scan = state.cachedMigration;
    if (!scan) {
      const empty = document.createElement('div');
      empty.className = 'qbb-lh-empty';
      empty.innerHTML = '<strong>' + S.migrationPreparing + '</strong>' + S.migrationIntro;
      body.appendChild(empty);
      if (!state.migrationLoading) loadMigrationData();
      return;
    }
    const content = document.createElement('div');
    body.append(content);
    renderMigration(content, footerLeft, footerRight);
    fetchInstances().then(instances => {
      if (!content.isConnected) return;
      state.cachedInstances = instances;
      renderMigration(content, footerLeft, footerRight);
    }).catch(error => console.warn('[QBB NodeOps] Instance refresh failed', error));
  }

  function renderInstanceNavigation() {
    const back=document.createElement('button');
    back.type='button';back.className='qbb-lh-btn qbb-instance-back';
    back.textContent=trUI('← 返回实例管理','← Back to instances');
    back.onclick=()=>setTab('instances');
    body.prepend(back);
  }

  function renderInstanceHome() {
    clear(body);clear(footerLeft);clear(footerRight);
    const home=document.createElement('section');home.className='qbb-instance-home';
    const title=document.createElement('h2');title.textContent=trUI('实例管理','Instance management');
    const subtitle=document.createElement('p');subtitle.className='qbb-instance-intro';
    subtitle.textContent=trUI('管理 ComfyUI 实例、环境版本与插件迁移','Manage ComfyUI instances, version records and plugin transfers');
    const cards=document.createElement('nav');cards.className='qbb-instance-home-cards';
    cards.setAttribute('aria-label',trUI('实例管理功能','Instance management tools'));
    let recordLink;
    for(const [key,label,description,action] of [
      ['snapshots',trUI('版本记录','Version records'),trUI('保存、比较和恢复实例历史状态','Save, compare and restore instance versions'),trUI('查看记录','View records')],
      ['create-instance',trUI('创建新实例','Create instance'),trUI('创建独立的 ComfyUI 实例','Create an independent ComfyUI instance'),trUI('创建实例','Create instance')],
      ['migration',trUI('插件搬家','Move plugins'),trUI('在实例之间迁移插件','Transfer custom nodes between instances'),trUI('开始迁移','Start transfer')],
    ]){
      const card=document.createElement('button');card.type='button';card.className='qbb-instance-home-card';
      const name=document.createElement('strong');name.textContent=label;
      const summary=document.createElement('span');summary.className='qbb-instance-card-description';summary.textContent=description;
      const link=document.createElement('span');link.className='qbb-instance-card-action';link.textContent=action+' →';
      card.append(name,summary,link);card.onclick=()=>setTab(key);cards.append(card);
      if(key==='snapshots')recordLink=link;
    }
    home.append(title,subtitle,cards);body.append(home);
    nodeopsFetch('/qbb_nodeops/version-records').then(async response=>{
      if(!response.ok)return;
      const data=await response.json();
      if(home.isConnected&&Array.isArray(data.items))recordLink.textContent=data.items.length+' '+trUI('份记录','records')+' →';
    }).catch(()=>{});
  }

  function pageHeading(title, subtitle) {
    const h = document.createElement('h2'); h.className = 'qbb-page-heading'; h.textContent = title;
    const p = document.createElement('p'); p.className = 'qbb-page-subtitle'; p.textContent = subtitle;
    body.prepend(h, p);
  }

  function renderOverview() {
    clear(body); clear(footerLeft); clear(footerRight);
    pageHeading(trUI('资源管理', 'Resources'), trUI('查看插件与模型的使用情况，筛选需要清理的资源。环境版本记录已移至「实例管理」。', 'Review plugin and model usage to find cleanup candidates. Environment records are in Instance management.'));
    const split=document.createElement('div');
    split.className='qbb-resource-split';
    const files=document.createElement('section');
    files.className='qbb-resource-panel';
    const filesTitle=document.createElement('h3');
    filesTitle.className='qbb-resource-panel-title';
    filesTitle.textContent=trUI('插件与模型','Plugins and models');
    const filesBody=document.createElement('div');
    files.append(filesTitle, filesBody);
    renderCleanup(filesBody, footerRight);
    split.append(files);
    body.append(split);
  }

  let disposePageHelp = null;
  function setTab(tab) {
    if (disposePageHelp) { disposePageHelp(); disposePageHelp = null; }
    if (tab === "load") { selectPluginFilter("failed"); tab = "plugins"; }
    if (tab === "updates") { selectPluginFilter("updates"); tab = "plugins"; }
    closeSettingsMenu();
    activeTab = tab;
    modal.dataset.activeTab = tab;
    body.classList.toggle("qbb-split-body", tab === "overview");
    tabOverview.classList.toggle("active", tab === "overview");
    tabPlugins.classList.toggle("active", tab === "plugins");
    tabMigration.classList.toggle("active", ['instances', 'migration', 'snapshots', 'create-instance'].includes(tab));
    tabMarket.classList.toggle("active", tab === "market");
    if (tab === "instances") renderInstanceHome();
    else if (tab === "overview") renderOverview();
    else if (tab === "plugins") {
      renderPluginsFooter();
      renderPluginTable(body, data, updatesData, state.recentlyUpdatedItems, {
        copyAll: () => navigator.clipboard.writeText(formatAllIssuesForCopy(items, S)),
        copy: (row) => navigator.clipboard.writeText(formatIssueForCopy(row, S)),
        analyse: (row) => showAiOutputDialog(S.aiResult, S.aiAnalysing, () => requestAi("/qbb_nodeops/ai/analyse", { issue: row })),
        update: async (row) => {
          const result = await pullPlugins({ paths: [row.path] });
          const first = result.results?.[0];
          if (!first?.ok) throw new Error(first?.error || result.error || "Update failed");
          const updated = { ...row, ...first };
          markPluginsUpdated([updated]);
          state.cachedUpdates = result.report || await fetchUpdates();
          hooks.applyButtonState();
          return updated;
        },
      }, "plugins");
    } else if (tab === "migration") {
      renderMigrationTab();
      renderInstanceNavigation();
    } else if (tab === "create-instance") {
      renderCreateInstance(body, footerLeft, footerRight);
      renderInstanceNavigation();
    } else if (tab === "snapshots") {
      renderSnapshots(body, footerLeft, footerRight);
      renderInstanceNavigation();
    }
    else renderMarket(body, footerLeft, footerRight);
    body.scrollTop=0;
    disposePageHelp = installPageHelp(body);
  }

  tabOverview.onclick = () => setTab("overview");
  tabPlugins.onclick = () => { selectPluginFilter("all"); setTab("plugins"); };
  tabMigration.onclick = () => setTab("instances");
  tabMarket.onclick = () => setTab("market");

  modal.appendChild(header);
  const windowActions = document.createElement('div'); windowActions.className='qbb-window-actions';
  const maximize = document.createElement('button');maximize.className='qbb-lh-icon-btn';maximize.textContent='□';maximize.title=trUI('最大化 / 还原','Maximize / restore');
  const applyWindowMode = () => {
    const maximized = !!state.modalRestoreBounds;
    if (maximized) Object.assign(modal.style,{position:'fixed',left:'8px',top:'8px',width:'calc(100vw - 16px)',height:'calc(100vh - 16px)',resize:'none'});
    else modal.style.resize = '';
    maximize.textContent = maximized ? '❐' : '□';
    maximize.title = maximized ? trUI('还原小窗','Restore window') : trUI('最大化','Maximize');
    maximize.setAttribute('aria-label',maximize.title);
    maximize.setAttribute('aria-pressed',String(maximized));
  };
  maximize.onclick=()=>{
    if(state.modalRestoreBounds){Object.assign(modal.style,state.modalRestoreBounds);state.modalRestoreBounds=null;}
    else {const rect=modal.getBoundingClientRect();state.modalRestoreBounds={position:'fixed',left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'};}
    applyWindowMode();
  };
  applyWindowMode();
  const dismiss=document.createElement('button');dismiss.className='qbb-lh-icon-btn';dismiss.textContent='×';dismiss.title=trUI('关闭窗口','Close window');dismiss.onclick=()=>overlay.remove();
  const minimize=document.createElement('button');minimize.className='qbb-lh-icon-btn';minimize.type='button';minimize.textContent='−';minimize.title=trUI('收到底部','Minimize to bottom');minimize.setAttribute('aria-label',minimize.title);
  const dock=document.createElement('div');dock.className='qbb-window-dock';dock.hidden=true;
  const restore=document.createElement('button');restore.type='button';restore.className='qbb-lh-btn';restore.textContent='QBB NodeOps  ↑';restore.title=trUI('展开插件窗口','Restore plugin window');restore.setAttribute('aria-label',restore.title);
  restore.onclick=()=>{overlay.classList.remove('qbb-is-minimized');dock.hidden=true;minimize.focus();};
  const dockClose=document.createElement('button');dockClose.type='button';dockClose.className='qbb-lh-icon-btn';dockClose.textContent='×';dockClose.title=dismiss.title;dockClose.setAttribute('aria-label',dismiss.title);dockClose.onclick=()=>overlay.remove();
  dock.append(restore,dockClose);
  minimize.onclick=()=>{closeSettingsMenu();overlay.classList.add('qbb-is-minimized');dock.hidden=false;restore.focus();};
  windowActions.append(minimize,maximize,dismiss);header.append(windowActions);
  modal.appendChild(tabs);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  overlay.appendChild(dock);
  document.body.appendChild(overlay);

  setTab(activeTab);
  if (prevBodyScroll) body.scrollTop = prevBodyScroll;
  window.scrollTo(pageX, pageY);
  requestAnimationFrame(() => window.scrollTo(pageX, pageY));
}

hooks.showAttentionModal = showAttentionModal;
