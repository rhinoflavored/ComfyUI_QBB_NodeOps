import "./http.js";
import { preloadNodeopsData } from "./http.js";
import { app } from "../../scripts/app.js";
import { preferences, loadPreferences } from "./preferences.js";
import { t, getComfyLocale } from "./i18n.js";
import { ensureStyles } from "./styles.js";
import { hooks, normalizeItems, state } from "./shared.js";
import { attachToTopMenu, applyButtonState } from "./menu.js";
import { fetchReport, openReport, pollUpdates } from "./api.js";
import { showAttentionModal } from "./workspace.js";

ensureStyles();
hooks.openReport = openReport;
hooks.showAttentionModal = showAttentionModal;

app.registerExtension({
  name: "ComfyUI.QBB.NodeOps",
  commands: [
    {
      id: "QBB.NodeOps.ShowReport",
      label: "Load Report / 插件检查",
      function: () => openReport(),
    },
  ],
  menuCommands: [
    {
      path: ["QBB", "NodeOps"],
      commands: ["QBB.NodeOps.ShowReport"],
    },
  ],
  async setup() {
    preloadNodeopsData();
    try {await loadPreferences();} catch(err) {console.warn('[QBB NodeOps] preferences unavailable; automatic popups suppressed',err);}
    const old = document.getElementById("qbb-nodeops-top-btn");
    if (old) old.remove();

    let lastLocale = getComfyLocale();
    setInterval(() => {
      const now = getComfyLocale();
      if (now !== lastLocale) {
        lastLocale = now;
        applyButtonState();
      }
    }, 1500);

    try {
      const data = await fetchReport();
      state.cachedReport = data;
      await attachToTopMenu();
      applyButtonState();
      if (preferences.popup_errors && normalizeItems(data).length) {
        state.startupPopupShown = true;
        showAttentionModal(data, state.cachedUpdates);
      }
    } catch (err) {
      state.cachedReport = { attention_count: 0, failures: [], partials: [], attention: [] };
      await attachToTopMenu();
      console.warn("[QBB NodeOps] failed to load report", err);
    }

    pollUpdates({ autoPopup: true });
  },
});
