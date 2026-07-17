import { app } from "../../scripts/app.js";
import { t, getComfyLocale } from "./i18n.js";

const STYLE_ID = "qbb-loadhighlight-style";
const BTN_ID = "qbb-loadhighlight-menu-btn";
const GROUP_CLASS = "qbb-loadhighlight-button-group";

let cachedReport = null;

function ensureStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    #${BTN_ID},
    .${GROUP_CLASS} button,
    .${GROUP_CLASS} .comfyui-button {
      border: 1px solid #8a6d1d !important;
      background: #3a2f0f !important;
      color: #ffe58f !important;
      font-weight: 800 !important;
      font-size: 12px !important;
      padding: 4px 10px !important;
      border-radius: 6px !important;
      cursor: pointer;
      line-height: 1.2;
      white-space: nowrap;
    }
    #${BTN_ID}.has-issues,
    .${GROUP_CLASS} button.has-issues,
    .${GROUP_CLASS} .comfyui-button.has-issues {
      border-color: #a33 !important;
      background: #4a1818 !important;
      color: #ffb4b4 !important;
    }
    .qbb-lh-overlay {
      position: fixed;
      inset: 0;
      z-index: 100000;
      background: rgba(0, 0, 0, 0.55);
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    .qbb-lh-modal {
      position: relative;
      width: min(760px, 92vw);
      max-height: min(80vh, 900px);
      overflow: auto;
      background: #1e1e1e;
      color: #f3f3f3;
      border: 1px solid #5a1f1f;
      border-radius: 10px;
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
      padding: 20px 22px 16px;
    }
    .qbb-lh-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .qbb-lh-header-main {
      flex: 1;
      min-width: 0;
    }
    .qbb-lh-title {
      margin: 0 0 8px;
      font-size: 20px;
      font-weight: 800;
      color: #ff6b6b;
    }
    .qbb-lh-title.ok { color: #95de64; }
    .qbb-lh-sub {
      margin: 0 0 16px;
      font-size: 13px;
      color: #c8c8c8;
      line-height: 1.45;
    }
    .qbb-lh-btn-accent {
      flex: 0 0 auto;
      margin-top: 2px;
      background: #2b4acb !important;
      color: #fff !important;
    }
    .qbb-lh-item {
      border-top: 1px solid #3a3a3a;
      padding: 12px 0;
    }
    .qbb-lh-name {
      font-size: 16px;
      font-weight: 800;
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .qbb-lh-copy-one {
      border: 1px solid #555;
      border-radius: 6px;
      width: 28px;
      height: 28px;
      padding: 0;
      cursor: pointer;
      background: transparent;
      color: #c8c8c8;
      margin-left: auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
    }
    .qbb-lh-copy-one:hover {
      background: #333;
      color: #fff;
      border-color: #777;
    }
    .qbb-lh-copy-one svg {
      width: 14px;
      height: 14px;
      display: block;
    }
    .qbb-lh-name.failed { color: #ff4d4f; }
    .qbb-lh-name.partial { color: #ffc53d; }
    .qbb-lh-badge {
      display: inline-block;
      font-size: 11px;
      font-weight: 800;
      padding: 2px 8px;
      border-radius: 999px;
      margin-left: 8px;
      vertical-align: middle;
    }
    .qbb-lh-badge.failed { background: #791f1f; color: #ffb4b4; }
    .qbb-lh-badge.partial { background: #5c4510; color: #ffe58f; }
    .qbb-lh-reason {
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .qbb-lh-reason.failed { color: #ff8a8a; }
    .qbb-lh-reason.partial { color: #ffd666; }
    .qbb-lh-path {
      font-size: 12px;
      color: #9a9a9a;
      word-break: break-all;
      margin-bottom: 8px;
    }
    .qbb-lh-tb {
      width: 100%;
      box-sizing: border-box;
      max-height: 180px;
      overflow: auto;
      background: #141414;
      color: #ffb4b4;
      border: 1px solid #4a2020;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 11px;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .qbb-lh-tb.partial {
      color: #ffe7a3;
      border-color: #5c4510;
    }
    .qbb-lh-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      margin-top: 14px;
    }
    .qbb-lh-btn {
      border: 0;
      border-radius: 6px;
      padding: 8px 14px;
      cursor: pointer;
      font-weight: 700;
    }
    .qbb-lh-btn-primary { background: #ff4d4f; color: #fff; }
    .qbb-lh-btn-ghost { background: #333; color: #eee; }
    .qbb-lh-export-overlay {
      position: fixed;
      inset: 0;
      z-index: 100010;
      background: rgba(0, 0, 0, 0.65);
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .qbb-lh-export-modal {
      width: min(520px, 92vw);
      background: #1a1a1a;
      color: #f3f3f3;
      border: 1px solid #444;
      border-radius: 10px;
      padding: 18px 18px 14px;
      box-shadow: 0 16px 40px rgba(0,0,0,0.45);
    }
    .qbb-lh-export-modal h3 {
      margin: 0 0 12px;
      font-size: 16px;
      font-weight: 800;
    }
    .qbb-lh-export-field {
      margin-bottom: 12px;
    }
    .qbb-lh-export-field label {
      display: block;
      font-size: 12px;
      color: #bbb;
      margin-bottom: 6px;
    }
    .qbb-lh-export-field input {
      width: 100%;
      box-sizing: border-box;
      background: #111;
      color: #eee;
      border: 1px solid #555;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
    }
    .qbb-lh-export-hint {
      font-size: 12px;
      color: #888;
      margin: -4px 0 12px;
      line-height: 1.4;
    }
    .qbb-lh-export-error {
      color: #ff8a8a;
      font-size: 12px;
      margin-bottom: 10px;
      min-height: 16px;
    }
  `;
  document.head.appendChild(style);
}

function normalizeItems(data) {
  if (!data) return [];
  if (Array.isArray(data.attention)) return data.attention;
  return [...(data.failures || []), ...(data.partials || [])];
}

function buttonLabel(data) {
  const S = t();
  const count = data?.attention_count || normalizeItems(data).length || 0;
  return count > 0 ? S.menuIssues(count) : S.menuReport;
}

async function showExportDialog(options = {}) {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-export-overlay");
  if (existing) existing.remove();

  const S = t();
  const kind = options.kind || "log";
  const titleText = options.title || (kind === "deps" ? S.exportDepsTitle : S.exportLogTitle);
  const hintText = options.hint || (kind === "deps" ? S.exportDepsHint : S.exportLogHint);
  const exportUrl =
    options.exportUrl ||
    (kind === "deps"
      ? "/qbb_loadhighlight/export_environment_to_path"
      : "/qbb_loadhighlight/export_to_path");
  const defaultsUrl =
    options.defaultsUrl || `/qbb_loadhighlight/export_defaults?kind=${encodeURIComponent(kind)}`;

  let defaults = {
    directory: "",
    filename: kind === "deps" ? "comfyui-environment.txt" : "comfyui-loadhighlight.log",
  };
  try {
    const res = await fetch(defaultsUrl);
    if (res.ok) defaults = await res.json();
  } catch (err) {
    console.warn("[ComfyUI_QBB_LoadHighlight] export_defaults failed", err);
  }

  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-export-overlay";

  const panel = document.createElement("div");
  panel.className = "qbb-lh-export-modal";

  const title = document.createElement("h3");
  title.textContent = titleText;

  const hint = document.createElement("div");
  hint.className = "qbb-lh-export-hint";
  hint.textContent = hintText;

  const dirField = document.createElement("div");
  dirField.className = "qbb-lh-export-field";
  const dirLabel = document.createElement("label");
  dirLabel.textContent = S.exportDir;
  const dirInput = document.createElement("input");
  dirInput.type = "text";
  dirInput.value = defaults.directory || "";
  dirInput.placeholder = S.dirPlaceholder;
  dirField.appendChild(dirLabel);
  dirField.appendChild(dirInput);

  const nameField = document.createElement("div");
  nameField.className = "qbb-lh-export-field";
  const nameLabel = document.createElement("label");
  nameLabel.textContent = S.exportFilename;
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.value = defaults.filename || (kind === "deps" ? "comfyui-environment.txt" : "comfyui-loadhighlight.log");
  nameInput.placeholder = defaults.filename || "";
  nameField.appendChild(nameLabel);
  nameField.appendChild(nameInput);

  const errBox = document.createElement("div");
  errBox.className = "qbb-lh-export-error";

  const actions = document.createElement("div");
  actions.className = "qbb-lh-actions";
  actions.style.marginTop = "0";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
  cancelBtn.textContent = S.exportCancel;
  cancelBtn.onclick = () => overlay.remove();

  const saveBtn = document.createElement("button");
  saveBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
  saveBtn.textContent = S.exportConfirm;
  saveBtn.onclick = async () => {
    errBox.textContent = "";
    const directory = dirInput.value.trim();
    const filename = nameInput.value.trim();
    if (!directory) {
      errBox.textContent = S.needDir;
      dirInput.focus();
      return;
    }
    if (!filename) {
      errBox.textContent = S.needFilename;
      nameInput.focus();
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = S.exportWorking;
    try {
      const res = await fetch(exportUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ directory, filename }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      overlay.remove();
      alert(S.exportSuccess(data.path));
    } catch (err) {
      console.warn("[ComfyUI_QBB_LoadHighlight] export failed", err);
      errBox.textContent = S.exportFailed(err.message || err);
      saveBtn.disabled = false;
      saveBtn.textContent = S.exportConfirm;
    }
  };

  actions.appendChild(cancelBtn);
  actions.appendChild(saveBtn);

  panel.appendChild(title);
  panel.appendChild(hint);
  panel.appendChild(dirField);
  panel.appendChild(nameField);
  panel.appendChild(errBox);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
  dirInput.focus();
  dirInput.select();
}

function formatIssueForCopy(item, S) {
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

function formatAllIssuesForCopy(items, S) {
  return items.map((item) => formatIssueForCopy(item, S)).join("\n\n" + "=".repeat(60) + "\n\n");
}

async function copyText(text, button, S) {
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

function copyIconSvg() {
  // Overlapping squares copy icon
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="9" y="9" width="11" height="11" rx="2"></rect>
  <path d="M5 15V7a2 2 0 0 1 2-2h8"></path>
</svg>`;
}

function showAttentionModal(data) {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-overlay");
  if (existing) existing.remove();

  const S = t();
  const items = normalizeItems(data);
  const failureCount = data?.failure_count || 0;
  const partialCount = data?.partial_count || 0;

  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-overlay";
  const modal = document.createElement("div");
  modal.className = "qbb-lh-modal";

  const header = document.createElement("div");
  header.className = "qbb-lh-header";

  const headerMain = document.createElement("div");
  headerMain.className = "qbb-lh-header-main";

  const title = document.createElement("h2");
  title.className = "qbb-lh-title";
  const sub = document.createElement("p");
  sub.className = "qbb-lh-sub";

  if (!items.length) {
    title.classList.add("ok");
    title.textContent = S.titleOk;
    sub.textContent = S.subOk;
  } else {
    const bits = [];
    if (failureCount) bits.push(S.bitFailed(failureCount));
    if (partialCount) bits.push(S.bitPartial(partialCount));
    title.textContent = S.titleAttention(bits, items.length);
    sub.textContent = S.subAttention;
  }
  headerMain.appendChild(title);
  headerMain.appendChild(sub);
  header.appendChild(headerMain);
  modal.appendChild(header);

  for (const item of items) {
    const status = item.status || (item.success ? "partial" : "failed");
    const block = document.createElement("div");
    block.className = "qbb-lh-item";

    const name = document.createElement("div");
    name.className = `qbb-lh-name ${status}`;
    const nameText = document.createElement("span");
    nameText.textContent = item.name || "(unknown)";
    name.appendChild(nameText);
    const badge = document.createElement("span");
    badge.className = `qbb-lh-badge ${status}`;
    badge.textContent = status === "partial" ? S.badgePartial : S.badgeFailed;
    name.appendChild(badge);

    const copyOneBtn = document.createElement("button");
    copyOneBtn.className = "qbb-lh-copy-one";
    copyOneBtn.type = "button";
    copyOneBtn.title = S.copyOne;
    copyOneBtn.setAttribute("aria-label", S.copyOne);
    copyOneBtn.innerHTML = copyIconSvg();
    copyOneBtn.onclick = () => copyText(formatIssueForCopy(item, S), copyOneBtn, S);
    name.appendChild(copyOneBtn);

    const reason = document.createElement("div");
    reason.className = `qbb-lh-reason ${status}`;
    reason.textContent = item.reason || (status === "partial" ? S.reasonPartial : S.reasonFailed);

    const path = document.createElement("div");
    path.className = "qbb-lh-path";
    path.textContent = item.path || "";

    block.appendChild(name);
    block.appendChild(reason);
    block.appendChild(path);

    const detailText =
      (Array.isArray(item.partial_issues) && item.partial_issues.length
        ? item.partial_issues.join("\n\n")
        : "") || item.traceback || "";
    if (detailText) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = status === "partial" ? S.viewPartial : S.viewTraceback;
      summary.style.cursor = "pointer";
      summary.style.marginBottom = "6px";
      summary.style.color = "#ddd";
      const pre = document.createElement("pre");
      pre.className = `qbb-lh-tb ${status}`;
      pre.textContent = detailText;
      details.appendChild(summary);
      details.appendChild(pre);
      block.appendChild(details);
    }
    modal.appendChild(block);
  }

  const actions = document.createElement("div");
  actions.className = "qbb-lh-actions";

  const depsBtn = document.createElement("button");
  depsBtn.className = "qbb-lh-btn qbb-lh-btn-accent";
  depsBtn.textContent = S.exportDeps;
  depsBtn.onclick = () => showExportDialog({ kind: "deps" });
  actions.appendChild(depsBtn);

  const exportBtn = document.createElement("button");
  exportBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
  exportBtn.textContent = S.exportLog;
  exportBtn.onclick = () => showExportDialog({ kind: "log" });
  actions.appendChild(exportBtn);

  if (items.length) {
    const copyBtn = document.createElement("button");
    copyBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
    copyBtn.textContent = S.copyList;
    copyBtn.onclick = () => copyText(formatAllIssuesForCopy(items, S), copyBtn, S);
    actions.appendChild(copyBtn);
  }

  const closeBtn = document.createElement("button");
  closeBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
  closeBtn.textContent = S.gotIt;
  closeBtn.onclick = () => overlay.remove();
  actions.appendChild(closeBtn);
  modal.appendChild(actions);

  overlay.appendChild(modal);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
}

async function fetchReport() {
  const res = await fetch("/qbb_loadhighlight/failures");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function openReport() {
  try {
    const fresh = await fetchReport();
    cachedReport = fresh;
    applyButtonState(fresh);
    showAttentionModal(fresh);
  } catch (err) {
    if (cachedReport) showAttentionModal(cachedReport);
    else alert(t().cannotFetch);
    console.warn("[ComfyUI_QBB_LoadHighlight] reopen failed", err);
  }
}

function applyButtonState(data) {
  const S = t();
  const count = data?.attention_count || normalizeItems(data).length || 0;
  const label = buttonLabel(data);
  const nodes = [
    document.getElementById(BTN_ID),
    ...document.querySelectorAll(`.${GROUP_CLASS} button, .${GROUP_CLASS} .comfyui-button`),
  ].filter(Boolean);

  for (const el of nodes) {
    el.textContent = label;
    el.classList.toggle("has-issues", count > 0);
    el.title = S.menuTooltip;
  }
}

function mountGroupOutermost(groupEl) {
  // Put our button group at the far right of the top menu, not between other groups.
  const settingsGroup = app.menu?.settingsGroup;
  const parent = settingsGroup?.element?.parentElement;
  if (parent) {
    parent.appendChild(groupEl);
    return true;
  }
  if (settingsGroup?.element) {
    settingsGroup.element.after(groupEl);
    return true;
  }
  return false;
}

async function attachToTopMenu(attempt = 0) {
  ensureStyles();
  if (document.querySelector(`.${GROUP_CLASS}`)) {
    const existing = document.querySelector(`.${GROUP_CLASS}`);
    mountGroupOutermost(existing);
    applyButtonState(cachedReport);
    return true;
  }

  try {
    const settingsGroup = app.menu?.settingsGroup;
    if (settingsGroup?.element?.parentElement) {
      let ComfyButton;
      let ComfyButtonGroup;
      try {
        ({ ComfyButton } = await import("../../scripts/ui/components/button.js"));
        ({ ComfyButtonGroup } = await import("../../scripts/ui/components/buttonGroup.js"));
      } catch {
        ComfyButton = null;
        ComfyButtonGroup = null;
      }

      if (ComfyButton && ComfyButtonGroup) {
        const button = new ComfyButton({
          content: buttonLabel(cachedReport),
          tooltip: t().menuTooltip,
          classList: "comfyui-button",
          action: () => openReport(),
        });
        button.element.id = BTN_ID;
        const group = new ComfyButtonGroup(button);
        group.element.classList.add(GROUP_CLASS);
        mountGroupOutermost(group.element);
        applyButtonState(cachedReport);
        return true;
      }

      const btn = document.createElement("button");
      btn.id = BTN_ID;
      btn.type = "button";
      btn.textContent = buttonLabel(cachedReport);
      btn.addEventListener("click", () => openReport());
      const wrap = document.createElement("div");
      wrap.className = `comfyui-button-group ${GROUP_CLASS}`;
      wrap.appendChild(btn);
      mountGroupOutermost(wrap);
      applyButtonState(cachedReport);
      return true;
    }
  } catch (err) {
    console.warn("[ComfyUI_QBB_LoadHighlight] settingsGroup attach failed", err);
  }

  const host =
    document.querySelector(".comfyui-menu") ||
    document.querySelector(".comfyui-menu-right") ||
    document.querySelector(".actionbar-right") ||
    document.querySelector(".p-toolbar-group-end");

  if (host) {
    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = buttonLabel(cachedReport);
    btn.addEventListener("click", () => openReport());
    const wrap = document.createElement("div");
    wrap.className = GROUP_CLASS;
    wrap.appendChild(btn);
    host.appendChild(wrap);
    applyButtonState(cachedReport);
    return true;
  }

  if (attempt < 40) {
    setTimeout(() => attachToTopMenu(attempt + 1), 400);
  }
  return false;
}

app.registerExtension({
  name: "ComfyUI.QBB.LoadHighlight",
  // Avoid actionBarButtons here — it often lands in the middle of the top bar.
  commands: [
    {
      id: "QBB.LoadHighlight.ShowReport",
      label: "Load Report / 插件加载报告",
      function: () => openReport(),
    },
  ],
  menuCommands: [
    {
      // Top-level "QBB" menu so future QBB plugins can share this category
      path: ["QBB", "LoadHighlight"],
      commands: ["QBB.LoadHighlight.ShowReport"],
    },
  ],
  async setup() {
    const old = document.getElementById("qbb-loadhighlight-top-btn");
    if (old) old.remove();

    // Refresh top-bar label when ComfyUI locale changes
    let lastLocale = getComfyLocale();
    setInterval(() => {
      const now = getComfyLocale();
      if (now !== lastLocale) {
        lastLocale = now;
        applyButtonState(cachedReport);
      }
    }, 1500);

    try {
      const data = await fetchReport();
      cachedReport = data;
      await attachToTopMenu();
      applyButtonState(data);
      if (normalizeItems(data).length) showAttentionModal(data);
    } catch (err) {
      cachedReport = { attention_count: 0, failures: [], partials: [], attention: [] };
      await attachToTopMenu();
      console.warn("[ComfyUI_QBB_LoadHighlight] failed to load report", err);
    }
  },
});
