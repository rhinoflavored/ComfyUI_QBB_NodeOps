import { app } from "../../scripts/app.js";
import { t, getComfyLocale, isChineseLocale } from "./i18n.js";

const STYLE_ID = "qbb-nodeops-style";
const BTN_ID = "qbb-nodeops-menu-btn";
const GROUP_CLASS = "qbb-nodeops-button-group";

let cachedReport = null;
let cachedUpdates = null;
let cachedMigration = null;
let cachedHotTopics = null;
let hotTopicDays = 7;
let hotTopicQuery = "";
let cachedInstances = null;
let migrationSelection = new Set();
let migrationDestination = "";
let migrationMode = "";
let migrationDeleteSource = false;
let migrationSourceId = "current";
let migrationTargetId = "";
let migrationLoading = false;
let addInstanceFormOpen = false;
let addInstanceDraft = { mode: "new", path: "", name: "" };
let newInstanceName = "";
let startupPopupShown = false;
// Kept only for the current browser session.  Rebuilding the dialog for an
// update must not snap a user-moved/resized window back to the centre.
let modalLayout = null;
/** Successfully updated plugins kept visible as "更新成功" until page reload. */
let recentlyUpdatedItems = [];

function markPluginsUpdated(entries) {
  for (const entry of entries || []) {
    if (!entry || !entry.path) continue;
    recentlyUpdatedItems = recentlyUpdatedItems.filter((x) => x.path !== entry.path);
    recentlyUpdatedItems.push({
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

function getDoneUpdateItems(pending) {
  const pendingPaths = new Set((pending || []).map((p) => p.path));
  return recentlyUpdatedItems.filter((x) => x.path && !pendingPaths.has(x.path));
}

function ensureStyles() {
  let style = document.getElementById(STYLE_ID);
  if (!style) {
    style = document.createElement("style");
    style.id = STYLE_ID;
    document.head.appendChild(style);
  }
  style.textContent = `
    #${BTN_ID},
    .${GROUP_CLASS} button,
    .${GROUP_CLASS} .comfyui-button {
      border: 1px solid #8a6d1d !important;
      background: #3a2f0f !important;
      color: #ffe58f !important;
      font-weight: 700 !important;
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
    #${BTN_ID}.has-updates-only,
    .${GROUP_CLASS} button.has-updates-only,
    .${GROUP_CLASS} .comfyui-button.has-updates-only {
      border-color: #1d6fa5 !important;
      background: #0f2f4a !important;
      color: #91d5ff !important;
    }

    .qbb-lh-overlay {
      position: fixed;
      inset: 0;
      z-index: 100000;
      background: transparent;
      pointer-events: none;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    .qbb-lh-modal {
      position: relative;
      width: min(820px, 94vw);
      height: min(76vh, 820px);
      min-width: 440px;
      min-height: 360px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      resize: both;
      background: #111822;
      color: #e9eef7;
      border: 1px solid #293647;
      border-radius: 18px;
      box-shadow: 0 28px 80px rgba(0, 0, 0, 0.62), 0 0 0 1px rgba(255,255,255,0.025) inset;
      pointer-events: auto;
    }
    .qbb-lh-modal-header {
      flex: 0 0 auto;
      padding: 20px 22px 16px;
      border-bottom: 1px solid #273448;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      background: linear-gradient(135deg, #172235 0%, #111822 64%, #132338 100%);
      cursor: move;
      user-select: none;
    }
    .qbb-lh-modal-title {
      margin: 0;
      font-size: 19px;
      font-weight: 800;
      color: #f8fbff;
      letter-spacing: 0.02em;
      flex: 0 0 auto;
      white-space: nowrap;
    }
    .qbb-lh-chips {
      display: flex;
      flex-wrap: nowrap;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      flex: 1 1 auto;
      min-width: 0;
    }
    .qbb-lh-chip {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      box-sizing: border-box;
      font-size: 11px;
      font-weight: 750;
      padding: 5px 10px;
      border-radius: 9px;
      background: rgba(255,255,255,0.045);
      color: #cdd8e8;
      border: 1px solid rgba(255,255,255,0.08);
      white-space: nowrap;
    }
    .qbb-lh-chip.failed { background: rgba(206, 65, 76, .14); color: #ffb6bc; border-color: rgba(255, 101, 111, .22); }
    .qbb-lh-chip.partial { background: rgba(218, 157, 29, .13); color: #ffe09a; border-color: rgba(255, 193, 62, .22); }
    .qbb-lh-chip.update { background: rgba(61, 150, 232, .14); color: #a8d8ff; border-color: rgba(91, 177, 255, .22); }
    .qbb-lh-chip.ok { background: rgba(60, 168, 115, .14); color: #b8f0ca; border-color: rgba(90, 220, 145, .21); }
    .qbb-lh-chip.net { background: rgba(142, 111, 215, .13); color: #d8c9ff; border-color: rgba(171, 140, 255, .19); }

    .qbb-lh-tabs {
      flex: 0 0 auto;
      display: flex;
      align-items: center;
      gap: 0;
      padding: 10px 14px;
      border-bottom: 1px solid #273448;
      background: #101720;
    }
    .qbb-lh-tabs-left {
      display: flex;
      align-items: center;
      gap: 5px;
      flex: 1 1 auto;
      min-width: 0;
    }
    .qbb-lh-tabs-right {
      display: flex;
      align-items: center;
      flex: 0 0 auto;
      margin-left: auto;
      padding: 0;
    }
    .qbb-lh-tabs-right .qbb-lh-btn {
      padding: 6px 11px;
      font-size: 12px;
    }
    .qbb-lh-tab {
      appearance: none;
      border: 0;
      background: transparent;
      color: #8e9bad;
      font-size: 13px;
      font-weight: 750;
      padding: 8px 12px;
      cursor: pointer;
      border: 1px solid transparent;
      border-radius: 8px;
      margin: 0;
    }
    .qbb-lh-tab:hover { color: #e6efff; background: rgba(255,255,255,.04); }
    .qbb-lh-tab.active {
      color: #eaf4ff;
      border-color: rgba(83, 162, 255, .28);
      background: rgba(42, 121, 218, .18);
    }
    .qbb-lh-tab .qbb-lh-tab-count {
      display: inline-block;
      min-width: 16px;
      margin-left: 6px;
      padding: 0 5px;
      border-radius: 999px;
      font-size: 11px;
      background: rgba(255,255,255,.09);
      color: #b8c6d8;
    }
    .qbb-lh-tab.active .qbb-lh-tab-count {
      background: #2784e6;
      color: #fff;
    }
    .qbb-lh-tab.has-alert .qbb-lh-tab-count {
      background: #a61d24;
      color: #fff;
    }

    .qbb-lh-modal-body {
      flex: 1 1 auto;
      overflow: auto;
      padding: 16px 18px 10px;
      min-height: 0;
      background: #0f151e;
    }
    .qbb-lh-empty {
      padding: 48px 16px;
      text-align: center;
      color: #8394a8;
      font-size: 13px;
      line-height: 1.55;
    }
    .qbb-lh-empty strong {
      display: block;
      color: #dbe8f7;
      font-size: 15px;
      margin-bottom: 6px;
    }

    .qbb-lh-card {
      position: relative;
      background: linear-gradient(135deg, #17202c, #141c26);
      border: 1px solid #293747;
      border-radius: 12px;
      padding: 12px 14px;
      margin-bottom: 10px;
      box-shadow: 0 5px 16px rgba(0,0,0,.12);
      transition: border-color .15s ease, transform .15s ease;
    }
    .qbb-lh-card:hover { border-color: #3c5167; transform: translateY(-1px); }
    .qbb-lh-card::before {
      content: "";
      position: absolute;
      top: 12px;
      bottom: 12px;
      left: 0;
      width: 3px;
      border-radius: 0 4px 4px 0;
      background: #4d6177;
    }
    .qbb-lh-card.failed::before { background: #ed5b65; }
    .qbb-lh-card.partial::before { background: #e4ad43; }
    .qbb-lh-card.update::before { background: #5baef1; }
    .qbb-lh-card.done::before { background: #65c18b; }
    .qbb-lh-card-row {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .qbb-lh-card-name {
      flex: 1 1 auto;
      min-width: 0;
      font-size: 14px;
      font-weight: 780;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .qbb-lh-card-name.failed { color: #ff9ba2; }
    .qbb-lh-card-name.partial { color: #ffd36d; }
    .qbb-lh-card-name.update { color: #8acbff; }
    .qbb-lh-card-name.done { color: #a7ecbe; }
    .qbb-lh-badge {
      flex: 0 0 auto;
      font-size: 10px;
      font-weight: 800;
      padding: 2px 7px;
      border-radius: 999px;
    }
    .qbb-lh-badge.failed { background: rgba(205, 62, 70, .18); color: #ffb4b9; }
    .qbb-lh-badge.partial { background: rgba(213, 153, 29, .18); color: #ffe09a; }
    .qbb-lh-badge.update { background: rgba(33, 125, 208, .2); color: #a8d8ff; }
    .qbb-lh-badge.done { background: rgba(54, 154, 99, .2); color: #b7efc7; }
    .qbb-lh-card-actions {
      flex: 0 0 auto;
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }
    .qbb-lh-copy-one,
    .qbb-lh-icon-btn {
      border: 1px solid #35475a;
      border-radius: 6px;
      width: 28px;
      height: 28px;
      padding: 0;
      cursor: pointer;
      background: rgba(255,255,255,.025);
      color: #b8c7da;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .qbb-lh-copy-one:hover,
    .qbb-lh-icon-btn:hover {
      background: rgba(71, 139, 210, .16);
      color: #eaf4ff;
      border-color: #5089be;
    }
    .qbb-lh-copy-one svg,
    .qbb-lh-icon-btn svg {
      width: 14px;
      height: 14px;
      display: block;
    }
    .qbb-lh-btn-update {
      background: linear-gradient(135deg, #2787e9, #1768be);
      color: #fff;
      border: 0;
      border-radius: 6px;
      padding: 5px 10px;
      cursor: pointer;
      font-weight: 700;
      font-size: 12px;
      line-height: 1.2;
    }
    .qbb-lh-btn-update:disabled {
      opacity: 0.55;
      cursor: default;
    }
    .qbb-lh-card-meta {
      margin-top: 8px;
      font-size: 12px;
      color: #98a9bc;
    }
    .qbb-lh-card-meta summary {
      cursor: pointer;
      color: #b9c8d9;
      user-select: none;
    }
    .qbb-lh-card-meta .qbb-lh-meta-body {
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid #293747;
    }
    .qbb-lh-reason {
      font-size: 12px;
      font-weight: 650;
      margin-bottom: 6px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .qbb-lh-reason.failed { color: #ff8a8a; }
    .qbb-lh-reason.partial { color: #ffd666; }
    .qbb-lh-path,
    .qbb-lh-sha {
      font-size: 11px;
      color: #8193a8;
      word-break: break-all;
      margin-bottom: 4px;
      font-family: ui-monospace, Consolas, monospace;
    }
    .qbb-lh-link {
      font-size: 12px;
      color: #69c0ff;
      text-decoration: none;
    }
    .qbb-lh-link:hover { text-decoration: underline; }
    .qbb-lh-tb {
      width: 100%;
      box-sizing: border-box;
      max-height: 160px;
      overflow: auto;
      background: #0b1017;
      color: #ffb4b4;
      border: 1px solid #3a2020;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 11px;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-word;
      margin-top: 6px;
    }
    .qbb-lh-tb.partial {
      color: #ffe7a3;
      border-color: #5c4510;
    }
    .qbb-lh-update-status {
      font-size: 11px;
      color: #91d5ff;
      margin-top: 6px;
    }
    .qbb-lh-update-status.err { color: #ff8a8a; }

    .qbb-lh-modal-footer {
      flex: 0 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 12px 18px 16px;
      border-top: 1px solid #273448;
      background: #111822;
    }
    .qbb-lh-footer-left,
    .qbb-lh-footer-right {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .qbb-lh-footer-right { margin-left: auto; }
    .qbb-lh-btn {
      border: 0;
      border-radius: 6px;
      padding: 7px 12px;
      cursor: pointer;
      font-weight: 700;
      font-size: 12px;
    }
    .qbb-lh-btn-primary { background: #34475c; color: #f4f8fd; }
    .qbb-lh-btn-primary:hover { background: #415a73; }
    .qbb-lh-btn-ghost { background: rgba(255,255,255,.055); color: #c8d6e6; border: 1px solid #304052; }
    .qbb-lh-btn-ghost:hover { background: rgba(72, 130, 190, .17); border-color: #456b90; }
    .qbb-lh-btn-accent { background: linear-gradient(135deg, #397ee1, #2453af); color: #fff; }
    .qbb-lh-btn-accent:hover { background: linear-gradient(135deg, #4e91ed, #2c64c6); }
    .qbb-lh-btn-update-all { background: linear-gradient(135deg, #2386e5, #1462b4); color: #fff; }
    .qbb-lh-btn-update-all:hover { background: linear-gradient(135deg, #3698f1, #1f73cb); }
    .qbb-lh-btn:disabled { opacity: 0.55; cursor: default; }

    .qbb-lh-export-overlay {
      position: fixed;
      inset: 0;
      z-index: 100010;
      background: transparent;
      pointer-events: none;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .qbb-lh-export-modal {
      width: min(560px, 94vw);
      background: #151d28;
      color: #edf4fe;
      border: 1px solid #304156;
      border-radius: 14px;
      padding: 18px 18px 14px;
      box-shadow: 0 24px 64px rgba(0,0,0,0.6);
      pointer-events: auto;
    }
    .qbb-lh-export-modal h3 {
      margin: 0 0 12px;
      font-size: 16px;
      font-weight: 800;
    }
    .qbb-lh-export-field { margin-bottom: 12px; }
    .qbb-lh-export-field label {
      display: block;
      font-size: 12px;
      color: #bbb;
      margin-bottom: 6px;
    }
    .qbb-lh-export-field input {
      width: 100%;
      box-sizing: border-box;
      background: #0c121a;
      color: #e9f1fb;
      border: 1px solid #34485e;
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
    }
    .qbb-lh-export-field input:focus {
      outline: none;
      border-color: #4c9de5;
      box-shadow: 0 0 0 3px rgba(74, 151, 227, .14);
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
    .qbb-lh-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      margin-top: 14px;
    }
    .qbb-lh-check-row {
      display: flex !important;
      flex-direction: row !important;
      align-items: center !important;
      justify-content: flex-start !important;
      gap: 8px;
      margin-bottom: 12px;
      text-align: left !important;
      width: 100%;
      box-sizing: border-box;
    }
    .qbb-lh-check-row input[type="checkbox"] {
      flex: 0 0 auto;
      margin: 0;
      width: 16px;
      height: 16px;
    }
    .qbb-lh-check-row > span {
      flex: 0 1 auto;
      display: inline;
      white-space: nowrap;
      text-align: left !important;
      color: #eee;
      font-size: 13px;
      font-weight: 600;
      line-height: 1.3;
    }
    .qbb-lh-network-choices {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin: 14px 0 16px;
    }
    .qbb-lh-network-choice {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      min-height: 42px;
      box-sizing: border-box;
      padding: 9px 10px;
      border: 1px solid #33475e;
      border-radius: 9px;
      background: rgba(255,255,255,.025);
      color: #cbd8e8;
      cursor: pointer;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .qbb-lh-network-choice:hover { border-color: #4b7198; background: rgba(65, 128, 192, .1); }
    .qbb-lh-network-choice:has(input:checked) {
      border-color: #3c8ee0;
      background: rgba(42, 125, 211, .15);
      color: #e7f3ff;
    }
    .qbb-lh-network-choice input { margin: 0; accent-color: #3f96ec; }
    .qbb-lh-field-disabled { opacity: .42; }
    .qbb-lh-field-disabled input { cursor: not-allowed; }
    .qbb-lh-migration-intro {
      margin: 0 0 14px;
      padding: 11px 12px;
      border: 1px solid rgba(88, 164, 237, .22);
      border-radius: 10px;
      background: rgba(48, 115, 184, .1);
      color: #b9d9f5;
      font-size: 12px;
      line-height: 1.55;
    }
    .qbb-lh-migration-section {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 16px 0 8px;
      color: #d9e5f3;
      font-size: 13px;
      font-weight: 750;
    }
    .qbb-lh-migration-tools { display: inline-flex; gap: 6px; }
    .qbb-lh-migration-tools button { padding: 5px 8px; font-size: 11px; }
    .qbb-lh-migration-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .qbb-lh-instance-section { margin: 2px 0 18px; }
    .qbb-lh-instance-heading { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:10px; }
    .qbb-lh-instance-heading strong { font-size:15px; color:#eaf4ff; }
    .qbb-lh-instance-cards { display:flex; gap:10px; overflow-x:auto; padding:2px 1px 8px; }
    .qbb-lh-instance-card { flex:0 0 168px; min-height:86px; text-align:left; border:1px solid #2c4056; border-radius:12px; background:#141e2b; color:#e3edf8; padding:13px; cursor:pointer; transition:border-color .15s, background .15s, transform .15s; }
    .qbb-lh-instance-card:hover { border-color:#4e86bb; background:#172638; transform:translateY(-1px); }
    .qbb-lh-instance-card.selected { border-color:#2d91ed; background:linear-gradient(135deg,#173c60,#14283d); box-shadow:0 0 0 1px rgba(71,157,240,.18) inset; }
    .qbb-lh-instance-card.add { display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; color:#91c9ff; border-style:dashed; background:#111c2a; }
    .qbb-lh-instance-card.add strong { font-size:26px; line-height:1; font-weight:400; }
    .qbb-lh-instance-card.add span { margin-top:6px; font-size:12px; font-weight:700; }
    .qbb-lh-instance-name { font-size:14px; font-weight:800; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .qbb-lh-instance-meta { margin-top:8px; color:#95aabe; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .qbb-lh-instance-form { display:grid; grid-template-columns:1fr 1fr auto; gap:10px; align-items:end; margin-top:12px; padding:14px; border:1px solid #31516e; border-radius:12px; background:#111d2b; }
    .qbb-lh-instance-form label { display:block; color:#b9cbe0; font-size:12px; font-weight:700; }
    .qbb-lh-instance-form input { box-sizing:border-box; width:100%; margin-top:6px; }
    .qbb-lh-instance-form-note { grid-column:1/-1; color:#93aabd; font-size:12px; line-height:1.45; }
    .qbb-lh-migration-step { margin:14px 0; padding:13px; border:1px solid #294057; border-radius:12px; background:#111a26; }
    .qbb-lh-feed-header { padding:18px; border:1px solid #29445f; border-radius:14px; background:linear-gradient(135deg,#13273c,#111a26); }
    .qbb-lh-feed-header h3 { margin:0 0 12px; color:#eef7ff; font-size:17px; }
    .qbb-lh-feed-search { display:flex; align-items:center; gap:8px; padding:6px 7px 6px 15px; border:1px solid #4a627d; border-radius:999px; background:#0e1620; box-shadow:0 4px 16px rgba(0,0,0,.2) inset; }
    .qbb-lh-feed-search input { min-width:0; flex:1; border:0; outline:0; background:transparent; color:#edf5ff; font:14px "Segoe UI","Microsoft YaHei",sans-serif; }
    .qbb-lh-feed-search input::placeholder { color:#8294a8; }
    .qbb-lh-feed-search button { border-radius:999px; }
    .qbb-lh-feed-summary { white-space:pre-wrap; margin:14px 0; padding:14px 16px; border-left:3px solid #4ba9ff; background:#111923; color:#dbe8f4; font:13px/1.65 "Segoe UI","Microsoft YaHei",sans-serif; }
    .qbb-lh-feed-toolbar { display:flex; align-items:center; gap:8px; margin-top:12px; }
    .qbb-lh-feed-toolbar .qbb-lh-btn.active { background:#1d6eb5; border-color:#4b9ee5; color:#fff; }
    .qbb-lh-feed-sources { display:block; margin-top:4px; }
    .qbb-lh-feed-source { display:block; width:100%; text-align:left; border:0; border-bottom:1px solid #273749; background:transparent; color:#e5f1fb; padding:16px 4px; cursor:pointer; }
    .qbb-lh-feed-source:hover { background:#142131; }
    .qbb-lh-feed-source strong { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#67b4ff; font-size:16px; text-decoration:underline; text-underline-offset:3px; }
    .qbb-lh-feed-source .qbb-lh-feed-url { display:block; margin-top:5px; color:#9aabbc; font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .qbb-lh-feed-source .qbb-lh-feed-description { display:block; margin-top:7px; color:#c3d1df; font-size:13px; line-height:1.5; }
    .qbb-lh-feed-source .qbb-lh-feed-meta { display:block; margin-top:7px; color:#7e96ac; font-size:11px; }
    .qbb-lh-migration-item {
      display: flex;
      align-items: center;
      min-width: 0;
      gap: 8px;
      min-height: 42px;
      padding: 8px 10px;
      border: 1px solid #293a4d;
      border-radius: 9px;
      background: rgba(255,255,255,.025);
      cursor: pointer;
    }
    .qbb-lh-migration-item:hover { border-color: #466b91; }
    .qbb-lh-migration-item:has(input:checked) {
      border-color: #357ec4;
      background: rgba(39, 112, 187, .14);
    }
    .qbb-lh-migration-item input { flex: 0 0 auto; margin: 0; accent-color: #3e96ec; }
    .qbb-lh-migration-name {
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: #c9d9eb;
      font-size: 12px;
      font-weight: 650;
    }
    .qbb-lh-migration-kind {
      flex: 0 0 auto;
      color: #91bce4;
      font-size: 10px;
      font-weight: 750;
      padding: 2px 6px;
      border-radius: 999px;
      background: rgba(73, 140, 203, .14);
    }
    .qbb-lh-migration-kind.local { color: #e2ca94; background: rgba(189, 143, 42, .13); }
    .qbb-lh-migration-progress {
      margin: 14px 0 0;
      padding: 11px 12px;
      border: 1px solid #2e4055;
      border-radius: 10px;
      background: rgba(0,0,0,.16);
      color: #aebed0;
      font-size: 12px;
    }
    .qbb-lh-migration-progress strong { display: block; color: #dce9f6; margin-bottom: 5px; }
    .qbb-lh-migration-progress.error strong { color: #ffabb2; }
    .qbb-lh-migration-log { max-height: 180px; overflow: auto; margin-top: 9px; white-space: pre-wrap; color: #90a5ba; font: 11px/1.45 ui-monospace, Consolas, monospace; }
    @media (max-width: 620px) {
      .qbb-lh-feed-sources { grid-template-columns:1fr; }
      .qbb-lh-feed-toolbar { flex-wrap:wrap; }
      .qbb-lh-instance-form { grid-template-columns:1fr; }
      .qbb-lh-modal { width: calc(100vw - 20px); height: min(82vh, 820px); min-width:0; min-height:300px; resize:none; border-radius: 14px; }
      .qbb-lh-modal-header { padding: 15px 14px 12px; align-items: flex-start; flex-wrap: wrap; }
      .qbb-lh-chips { justify-content: flex-start; flex-wrap: wrap; }
      .qbb-lh-tabs { padding: 8px 10px; }
      .qbb-lh-tab { padding: 8px 9px; font-size: 12px; }
      .qbb-lh-tabs-right .qbb-lh-btn { padding: 6px 8px; }
      .qbb-lh-modal-body { padding: 12px 10px 8px; }
      .qbb-lh-modal-footer { padding: 10px; align-items: flex-start; flex-wrap: wrap; }
      .qbb-lh-footer-right { margin-left: 0; }
      .qbb-lh-network-choices { grid-template-columns: 1fr; }
      .qbb-lh-migration-list { grid-template-columns: 1fr; }
    }
  `;
  document.head.appendChild(style);
}

function normalizeItems(data) {
  if (!data) return [];
  if (Array.isArray(data.attention)) return data.attention;
  return [...(data.failures || []), ...(data.partials || [])];
}

function issueCount(data) {
  return data?.attention_count || normalizeItems(data).length || 0;
}

function updateCount(data) {
  return data?.update_count || (data?.updates || []).length || 0;
}

function buttonLabel(report = cachedReport, updates = cachedUpdates) {
  const S = t();
  const issues = issueCount(report);
  const upd = updateCount(updates);
  const checking = updates && (updates.status === "idle" || updates.status === "running");
  if (issues > 0 && upd > 0) return S.menuCombined(issues, upd);
  if (issues > 0) return S.menuIssues(issues);
  if (upd > 0) return S.menuUpdates(upd);
  if (checking) return S.menuReportChecking;
  return S.menuReport;
}

function networkModeLabel(mode) {
  const S = t();
  if (mode === "proxy") return S.netModeProxy;
  if (mode === "mirror") return S.netModeMirror;
  return S.netModeDirect;
}

function showAiOutputDialog(titleText, pendingText, request) {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-export-overlay.qbb-lh-ai-output");
  if (existing) existing.remove();
  const S = t();
  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-export-overlay qbb-lh-ai-output";
  const panel = document.createElement("div");
  panel.className = "qbb-lh-export-modal";
  const title = document.createElement("h3");
  title.textContent = titleText;
  const content = document.createElement("pre");
  content.className = "qbb-lh-tb";
  content.style.whiteSpace = "pre-wrap";
  content.textContent = pendingText;
  const actions = document.createElement("div");
  actions.className = "qbb-lh-actions";
  const close = document.createElement("button");
  close.className = "qbb-lh-btn qbb-lh-btn-ghost";
  close.textContent = S.gotIt;
  close.onclick = () => overlay.remove();
  actions.appendChild(close);
  panel.appendChild(title);
  panel.appendChild(content);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const startedAt = Date.now();
  const waitingTicker = window.setInterval(() => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    content.textContent = `${pendingText} (${elapsed}s)`;
  }, 1000);
  Promise.resolve(request())
    .then((data) => { content.textContent = data.result || data.error || ""; })
    .catch((err) => { content.textContent = S.aiError(err.message || String(err)); })
    .finally(() => window.clearInterval(waitingTicker));
}

async function requestAi(path, payload) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 55000);
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, language: isChineseLocale() ? "zh" : "en" }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") throw new Error("AI request timed out after 55 seconds");
    throw err;
  } finally {
    window.clearTimeout(timeout);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function refreshHotTopics(days = hotTopicDays, query = hotTopicQuery) {
  hotTopicDays = days;
  hotTopicQuery = String(query || "").trim();
  cachedHotTopics = { status: "loading", days, query: hotTopicQuery };
  const modalOpen = document.querySelector(".qbb-lh-overlay");
  if (modalOpen) showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "hot" });
  try {
    cachedHotTopics = await requestAi("/qbb_nodeops/ai/hot_topics", { days, query: hotTopicQuery });
  } catch (err) {
    cachedHotTopics = { status: "error", days, error: err.message || String(err) };
  }
  if (document.querySelector(".qbb-lh-overlay")) {
    showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "hot" });
  }
}

function showAiSettingsDialog() {
  ensureStyles();
  document.querySelector(".qbb-lh-export-overlay.qbb-lh-ai-settings")?.remove();
  const S = t();
  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-export-overlay qbb-lh-ai-settings";
  const panel = document.createElement("div");
  panel.className = "qbb-lh-export-modal";
  const title = document.createElement("h3");
  title.textContent = S.aiSettingsTitle;
  const hint = document.createElement("div");
  hint.className = "qbb-lh-export-hint";
  hint.textContent = S.aiSettingsHint;
  const makeField = (labelText, type = "text") => {
    const field = document.createElement("div");
    field.className = "qbb-lh-export-field";
    const label = document.createElement("label");
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = type;
    field.append(label, input);
    return { field, input };
  };
  const base = makeField(S.aiBaseUrl);
  const model = makeField(S.aiModel);
  const key = makeField(S.aiApiKey, "password");
  let settingsEdited = false;
  [base.input, model.input, key.input].forEach((input) => input.addEventListener("input", () => { settingsEdited = true; }));
  key.input.autocomplete = "off";
  const saved = document.createElement("div");
  saved.className = "qbb-lh-path";
  const clearLabel = document.createElement("label");
  clearLabel.className = "qbb-lh-network-choice";
  const clearKey = document.createElement("input");
  clearKey.type = "checkbox";
  clearLabel.append(clearKey, document.createTextNode(S.aiClearKey));
  const error = document.createElement("div");
  error.className = "qbb-lh-export-error";
  const actions = document.createElement("div");
  actions.className = "qbb-lh-actions";
  const cancel = document.createElement("button");
  cancel.className = "qbb-lh-btn qbb-lh-btn-ghost";
  cancel.textContent = S.exportCancel;
  cancel.onclick = () => overlay.remove();
  const save = document.createElement("button");
  save.className = "qbb-lh-btn qbb-lh-btn-primary";
  save.textContent = S.aiSave;
  save.onclick = async () => {
    error.textContent = "";
    try {
      const data = await requestAi("/qbb_nodeops/ai/settings", {
        base_url: base.input.value.trim(), model: model.input.value.trim(), api_key: key.input.value, clear_api_key: clearKey.checked,
      });
      overlay.remove();
      alert(S.aiSaved);
      return data;
    } catch (err) { error.textContent = S.aiError(err.message || err); }
  };
  actions.append(cancel, save);
  panel.append(title, hint, base.field, model.field, key.field, saved, clearLabel, error, actions);
  overlay.appendChild(panel);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  fetch("/qbb_nodeops/ai/settings")
    .then((res) => res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`)))
    .then((data) => {
      // A slow settings request must never overwrite text the user has begun
      // entering in this modal.
      if (settingsEdited) return;
      base.input.value = data.base_url || "";
      model.input.value = data.model || "";
      saved.textContent = data.api_key_configured ? S.aiKeySaved : "";
    })
    .catch((err) => { error.textContent = S.aiError(err.message || err); });
}

function showAiDiscoveryDialog() {
  ensureStyles();
  document.querySelector(".qbb-lh-export-overlay.qbb-lh-ai-discovery")?.remove();
  const S = t();
  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-export-overlay qbb-lh-ai-discovery";
  const panel = document.createElement("div");
  panel.className = "qbb-lh-export-modal";
  const title = document.createElement("h3"); title.textContent = S.aiRecommendationTitle;
  const hint = document.createElement("div"); hint.className = "qbb-lh-export-hint"; hint.textContent = S.aiRecommendationHint;
  const input = document.createElement("textarea");
  input.className = "qbb-lh-export-field";
  input.placeholder = S.aiSearchPlaceholder;
  input.rows = 4;
  const error = document.createElement("div"); error.className = "qbb-lh-export-error";
  const actions = document.createElement("div"); actions.className = "qbb-lh-actions";
  const cancel = document.createElement("button"); cancel.className = "qbb-lh-btn qbb-lh-btn-ghost"; cancel.textContent = S.exportCancel; cancel.onclick = () => overlay.remove();
  const search = document.createElement("button"); search.className = "qbb-lh-btn qbb-lh-btn-primary"; search.textContent = S.aiSearch;
  search.onclick = () => {
    const query = input.value.trim() || "recent trending ComfyUI plugins and model projects";
    overlay.remove();
    showAiOutputDialog(S.aiResult, S.aiSearching, () => requestAi("/qbb_nodeops/ai/recommend", { query }));
  };
  actions.append(cancel, search); panel.append(title, hint, input, error, actions); overlay.appendChild(panel);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.remove(); }); document.body.appendChild(overlay); input.focus();
}

async function showNetworkSettingsDialog() {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-export-overlay.qbb-lh-net");
  if (existing) existing.remove();

  const S = t();
  let defaults = {
    proxy_enabled: false,
    proxy_host: "127.0.0.1",
    proxy_port: 7890,
    mirror_enabled: false,
    mirror_prefix: "https://ghfast.top/",
    mode: "direct",
  };
  try {
    const res = await fetch("/qbb_nodeops/network_settings");
    if (res.ok) defaults = { ...defaults, ...(await res.json()) };
  } catch (err) {
    console.warn("[QBB NodeOps] network_settings failed", err);
  }

  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-export-overlay qbb-lh-net";
  const panel = document.createElement("div");
  panel.className = "qbb-lh-export-modal";

  const title = document.createElement("h3");
  title.textContent = S.netSettingsTitle;
  const hint = document.createElement("div");
  hint.className = "qbb-lh-export-hint";
  hint.textContent = S.netSettingsHint;

  const modeLine = document.createElement("div");
  modeLine.className = "qbb-lh-path";
  modeLine.textContent = `${S.netMode}: ${networkModeLabel(defaults.mode)}`;

  const selectedMode = ["proxy", "mirror", "direct"].includes(defaults.mode)
    ? defaults.mode
    : defaults.proxy_enabled
      ? "proxy"
      : defaults.mirror_enabled
        ? "mirror"
        : "direct";

  const routeChoices = document.createElement("div");
  routeChoices.className = "qbb-lh-network-choices";

  const proxyEnableField = document.createElement("label");
  proxyEnableField.className = "qbb-lh-network-choice";
  const proxyEnable = document.createElement("input");
  proxyEnable.type = "radio";
  proxyEnable.name = "qbb-network-route";
  proxyEnable.value = "proxy";
  proxyEnable.checked = selectedMode === "proxy";
  const proxyEnableText = document.createElement("span");
  proxyEnableText.textContent = S.netChoiceProxy || S.netProxyEnable;
  proxyEnableField.appendChild(proxyEnable);
  proxyEnableField.appendChild(proxyEnableText);

  const hostField = document.createElement("div");
  hostField.className = "qbb-lh-export-field";
  const hostLabel = document.createElement("label");
  hostLabel.textContent = S.netProxyHost;
  const hostInput = document.createElement("input");
  hostInput.type = "text";
  hostInput.value = defaults.proxy_host || "127.0.0.1";
  hostField.appendChild(hostLabel);
  hostField.appendChild(hostInput);

  const portField = document.createElement("div");
  portField.className = "qbb-lh-export-field";
  const portLabel = document.createElement("label");
  portLabel.textContent = S.netProxyPort;
  const portInput = document.createElement("input");
  portInput.type = "number";
  portInput.min = "1";
  portInput.max = "65535";
  portInput.value = String(defaults.proxy_port || 7890);
  portField.appendChild(portLabel);
  portField.appendChild(portInput);

  const mirrorEnableField = document.createElement("label");
  mirrorEnableField.className = "qbb-lh-network-choice";
  const mirrorEnable = document.createElement("input");
  mirrorEnable.type = "radio";
  mirrorEnable.name = "qbb-network-route";
  mirrorEnable.value = "mirror";
  mirrorEnable.checked = selectedMode === "mirror";
  const mirrorEnableText = document.createElement("span");
  mirrorEnableText.textContent = S.netChoiceMirror || S.netMirrorEnable;
  mirrorEnableField.appendChild(mirrorEnable);
  mirrorEnableField.appendChild(mirrorEnableText);

  const directEnableField = document.createElement("label");
  directEnableField.className = "qbb-lh-network-choice";
  const directEnable = document.createElement("input");
  directEnable.type = "radio";
  directEnable.name = "qbb-network-route";
  directEnable.value = "direct";
  directEnable.checked = selectedMode === "direct";
  const directEnableText = document.createElement("span");
  directEnableText.textContent = S.netChoiceDirect || S.netModeDirect;
  directEnableField.appendChild(directEnable);
  directEnableField.appendChild(directEnableText);

  routeChoices.appendChild(proxyEnableField);
  routeChoices.appendChild(mirrorEnableField);
  routeChoices.appendChild(directEnableField);

  const mirrorField = document.createElement("div");
  mirrorField.className = "qbb-lh-export-field";
  const mirrorLabel = document.createElement("label");
  mirrorLabel.textContent = S.netMirrorUrl || S.netMirrorPrefix;
  const mirrorInput = document.createElement("input");
  mirrorInput.type = "text";
  mirrorInput.value = defaults.mirror_prefix || "https://ghfast.top/";
  mirrorField.appendChild(mirrorLabel);
  mirrorField.appendChild(mirrorInput);

  function refreshModeLine() {
    const mode = proxyEnable.checked ? "proxy" : mirrorEnable.checked ? "mirror" : "direct";
    modeLine.textContent = `${S.netMode}: ${networkModeLabel(mode)}`;
    const proxyActive = mode === "proxy";
    const mirrorActive = mode === "mirror";
    hostInput.disabled = !proxyActive;
    portInput.disabled = !proxyActive;
    mirrorInput.disabled = !mirrorActive;
    hostField.classList.toggle("qbb-lh-field-disabled", !proxyActive);
    portField.classList.toggle("qbb-lh-field-disabled", !proxyActive);
    mirrorField.classList.toggle("qbb-lh-field-disabled", !mirrorActive);
  }
  proxyEnable.addEventListener("change", refreshModeLine);
  mirrorEnable.addEventListener("change", refreshModeLine);
  directEnable.addEventListener("change", refreshModeLine);
  refreshModeLine();

  const errBox = document.createElement("div");
  errBox.className = "qbb-lh-export-error";

  const actions = document.createElement("div");
  actions.className = "qbb-lh-actions";
  actions.style.marginTop = "0";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
  cancelBtn.textContent = S.exportCancel;
  cancelBtn.onclick = () => overlay.remove();

  async function save(recheck) {
    errBox.textContent = "";
    const port = Number(portInput.value);
    if (proxyEnable.checked && (!Number.isInteger(port) || port < 1 || port > 65535)) {
      errBox.textContent = S.netPortInvalid;
      portInput.focus();
      return;
    }
    const payload = {
      proxy_enabled: proxyEnable.checked,
      proxy_host: hostInput.value.trim() || "127.0.0.1",
      proxy_port: port,
      mirror_enabled: mirrorEnable.checked,
      mirror_prefix: mirrorInput.value.trim() || "https://ghfast.top/",
    };
    try {
      const res = await fetch("/qbb_nodeops/network_settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
      overlay.remove();
      if (data.mode === "proxy" && data.restart_required) {
        alert(S.netProcessProxyRestart);
      }
      if (recheck) {
        await fetch("/qbb_nodeops/updates/refresh", { method: "POST" });
        startupPopupShown = false;
        const modalOpen = !!document.querySelector(".qbb-lh-overlay");
        if (modalOpen) {
          showAttentionModal(cachedReport || { attention: [] }, {
            status: "running",
            updates: [],
            update_count: 0,
            network_mode: data.mode,
          });
        }
        pollUpdates({ reopenIfOpen: true, autoPopup: false });
      } else {
        alert(S.netSaved);
      }
    } catch (err) {
      errBox.textContent = S.exportFailed(err.message || err);
    }
  }

  const saveOnlyBtn = document.createElement("button");
  saveOnlyBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
  saveOnlyBtn.textContent = S.netSaveOnly;
  saveOnlyBtn.onclick = () => save(false);

  const saveBtn = document.createElement("button");
  saveBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
  saveBtn.textContent = S.netSave;
  saveBtn.onclick = () => save(true);

  actions.appendChild(cancelBtn);
  actions.appendChild(saveOnlyBtn);
  actions.appendChild(saveBtn);

  panel.appendChild(title);
  panel.appendChild(hint);
  panel.appendChild(modeLine);
  panel.appendChild(routeChoices);
  panel.appendChild(hostField);
  panel.appendChild(portField);
  panel.appendChild(mirrorField);
  panel.appendChild(errBox);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  (proxyEnable.checked ? portInput : mirrorEnable.checked ? mirrorInput : proxyEnable).focus();
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
      ? "/qbb_nodeops/environment"
      : "/qbb_nodeops/export");
  const defaultsUrl =
    options.defaultsUrl || `/qbb_nodeops/export_defaults?kind=${encodeURIComponent(kind)}`;

  let defaults = {
    filename: kind === "deps" ? "comfyui-environment.txt" : "comfyui-loadhighlight.log",
  };
  try {
    const res = await fetch(defaultsUrl);
    if (res.ok) defaults = await res.json();
  } catch (err) {
    console.warn("[QBB NodeOps] export_defaults failed", err);
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
    const filename = nameInput.value.trim();
    if (!filename) {
      errBox.textContent = S.needFilename;
      nameInput.focus();
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = S.exportWorking;
    try {
      const res = await fetch(`${exportUrl}?filename=${encodeURIComponent(filename)}`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const blob = await res.blob();
      if (typeof window.showSaveFilePicker === "function") {
        const handle = await window.showSaveFilePicker({ suggestedName: filename });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      } else {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      overlay.remove();
      alert(S.exportSuccess(filename));
    } catch (err) {
      console.warn("[QBB NodeOps] export failed", err);
      errBox.textContent = S.exportFailed(err.message || err);
      saveBtn.disabled = false;
      saveBtn.textContent = S.exportConfirm;
    }
  };

  actions.appendChild(cancelBtn);
  actions.appendChild(saveBtn);

  panel.appendChild(title);
  panel.appendChild(hint);
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

function showAttentionModal(data, updatesData = cachedUpdates, options = {}) {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-overlay");
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
  if (previousModal) {
    const rect = previousModal.getBoundingClientRect();
    modalLayout = {
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

  let activeTab = ["load", "updates", "migration", "hot"].includes(prevTab) ? prevTab : null;
  if (!activeTab) activeTab = items.length ? "load" : "updates";

  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-overlay";

  const modal = document.createElement("div");
  modal.className = "qbb-lh-modal";
  if (modalLayout) {
    const width = Math.min(modalLayout.width, Math.max(320, window.innerWidth - 12));
    const height = Math.min(modalLayout.height, Math.max(260, window.innerHeight - 12));
    const left = Math.min(Math.max(6, modalLayout.left), Math.max(6, window.innerWidth - width - 6));
    const top = Math.min(Math.max(6, modalLayout.top), Math.max(6, window.innerHeight - height - 6));
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
      modalLayout = { left, top, width, height };
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
    const rect = modal.getBoundingClientRect();
    modalLayout = { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
  });
  layoutObserver.observe(modal);
  const title = document.createElement("h2");
  title.className = "qbb-lh-modal-title";
  title.textContent = S.modalTitle || S.menuReport;
  const chips = document.createElement("div");
  chips.className = "qbb-lh-chips";

  function addChip(cls, label) {
    const chip = document.createElement("span");
    chip.className = "qbb-lh-chip " + cls;
    chip.textContent = label;
    chips.appendChild(chip);
  }

  // Always show summary chips on the title row.
  addChip("failed", (S.chipFailed || S.bitFailed)(failureCount));
  addChip("partial", (S.chipPartial || S.bitPartial)(partialCount));
  addChip(
    "update",
    updatesChecking
      ? S.chipChecking || S.menuUpdatesChecking
      : (S.chipUpdates || S.bitUpdates)(updCount),
  );
  addChip(
    "net",
    (S.chipNetwork || ((m) => S.netMode + ": " + m))(
      networkModeLabel(updatesData?.network_mode || "direct"),
    ),
  );

  header.appendChild(title);
  header.appendChild(chips);

  const tabs = document.createElement("div");
  tabs.className = "qbb-lh-tabs";
  const tabsLeft = document.createElement("div");
  tabsLeft.className = "qbb-lh-tabs-left";
  const tabsRight = document.createElement("div");
  tabsRight.className = "qbb-lh-tabs-right";

  const tabLoad = document.createElement("button");
  tabLoad.type = "button";
  tabLoad.className = "qbb-lh-tab" + (items.length ? " has-alert" : "");
  tabLoad.dataset.tab = "load";
  tabLoad.innerHTML = (S.tabLoad || S.sectionLoad) + '<span class="qbb-lh-tab-count">' + items.length + "</span>";
  const tabUpdates = document.createElement("button");
  tabUpdates.type = "button";
  tabUpdates.className = "qbb-lh-tab" + (updCount ? " has-alert" : "");
  tabUpdates.dataset.tab = "updates";
  tabUpdates.innerHTML =
    (S.tabUpdates || S.sectionUpdates) +
    '<span class="qbb-lh-tab-count">' +
    (updatesChecking ? "…" : updCount) +
    "</span>";
  const tabMigration = document.createElement("button");
  tabMigration.type = "button";
  tabMigration.className = "qbb-lh-tab";
  tabMigration.dataset.tab = "migration";
  tabMigration.textContent = S.tabMigration || S.migrationTitle;
  tabsLeft.appendChild(tabLoad);
  tabsLeft.appendChild(tabUpdates);
  tabsLeft.appendChild(tabMigration);

  const tabHot = document.createElement("button");
  tabHot.type = "button";
  tabHot.className = "qbb-lh-tab";
  tabHot.dataset.tab = "hot";
  tabHot.textContent = S.aiRecommendation;
  tabsLeft.appendChild(tabHot);

  const netBtnTop = document.createElement("button");
  netBtnTop.type = "button";
  netBtnTop.className = "qbb-lh-btn qbb-lh-btn-ghost";
  netBtnTop.textContent = S.netSettings;
  netBtnTop.onclick = () => showNetworkSettingsDialog();
  tabsRight.appendChild(netBtnTop);

  const aiSettingsBtn = document.createElement("button");
  aiSettingsBtn.type = "button";
  aiSettingsBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
  aiSettingsBtn.textContent = S.aiSettings;
  aiSettingsBtn.onclick = () => showAiSettingsDialog();
  tabsRight.appendChild(aiSettingsBtn);

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

  function renderLoadTab() {
    clear(body);
    clear(footerLeft);
    clear(footerRight);

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "qbb-lh-empty";
      empty.innerHTML =
        "<strong>" +
        (S.emptyLoadTitle || S.titleOk) +
        "</strong>" +
        (S.emptyLoadSub || S.subOk);
      body.appendChild(empty);
    } else {
      for (const item of items) {
        const status = item.status || (item.success ? "partial" : "failed");
        const card = document.createElement("div");
        card.className = "qbb-lh-card " + status;

        const row = document.createElement("div");
        row.className = "qbb-lh-card-row";

        const name = document.createElement("div");
        name.className = "qbb-lh-card-name " + status;
        name.textContent = item.name || "(unknown)";
        name.title = item.name || "";

        const badge = document.createElement("span");
        badge.className = "qbb-lh-badge " + status;
        badge.textContent = status === "partial" ? S.badgePartial : S.badgeFailed;

        const actions = document.createElement("div");
        actions.className = "qbb-lh-card-actions";
        const copyOneBtn = document.createElement("button");
        copyOneBtn.className = "qbb-lh-copy-one";
        copyOneBtn.type = "button";
        copyOneBtn.title = S.copyOne;
        copyOneBtn.innerHTML = copyIconSvg();
        copyOneBtn.onclick = () => copyText(formatIssueForCopy(item, S), copyOneBtn, S);
        actions.appendChild(copyOneBtn);

        const aiBtn = document.createElement("button");
        aiBtn.className = "qbb-lh-copy-one";
        aiBtn.type = "button";
        aiBtn.title = S.aiAnalyse;
        aiBtn.textContent = "AI";
        aiBtn.onclick = () =>
          showAiOutputDialog(S.aiResult, S.aiAnalysing, () =>
            requestAi("/qbb_nodeops/ai/analyse", { issue: item }),
          );
        actions.appendChild(aiBtn);

        row.appendChild(name);
        row.appendChild(badge);
        row.appendChild(actions);
        card.appendChild(row);

        const meta = document.createElement("details");
        meta.className = "qbb-lh-card-meta";
        const summary = document.createElement("summary");
        summary.textContent = status === "partial" ? S.viewPartial : (S.viewDetails || S.viewTraceback);
        const metaBody = document.createElement("div");
        metaBody.className = "qbb-lh-meta-body";

        const reason = document.createElement("div");
        reason.className = "qbb-lh-reason " + status;
        reason.textContent = item.reason || (status === "partial" ? S.reasonPartial : S.reasonFailed);
        metaBody.appendChild(reason);

        if (item.path) {
          const pathEl = document.createElement("div");
          pathEl.className = "qbb-lh-path";
          pathEl.textContent = item.path;
          metaBody.appendChild(pathEl);
        }

        const detailText =
          (Array.isArray(item.partial_issues) && item.partial_issues.length
            ? item.partial_issues.join("\n\n")
            : "") || item.traceback || "";
        if (detailText) {
          const pre = document.createElement("pre");
          pre.className = "qbb-lh-tb " + status;
          pre.textContent = detailText;
          metaBody.appendChild(pre);
        }

        meta.appendChild(summary);
        meta.appendChild(metaBody);
        card.appendChild(meta);
        body.appendChild(card);
      }
    }

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
    depsBtn.className = "qbb-lh-btn qbb-lh-btn-accent";
    depsBtn.textContent = S.exportDeps;
    depsBtn.onclick = () => showExportDialog({ kind: "deps" });
    footerLeft.appendChild(depsBtn);

    const closeBtn = document.createElement("button");
    closeBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
    closeBtn.textContent = S.gotIt;
    closeBtn.onclick = () => overlay.remove();
    footerRight.appendChild(closeBtn);
  }

  function renderUpdatesTab() {
    clear(body);
    clear(footerLeft);
    clear(footerRight);

    const doneItems = getDoneUpdateItems(updates);

    if (updatesChecking && !updates.length && !doneItems.length) {
      const empty = document.createElement("div");
      empty.className = "qbb-lh-empty";
      empty.innerHTML =
        "<strong>" +
        (S.emptyUpdatesCheckingTitle || S.sectionUpdatesChecking) +
        "</strong>" +
        S.updateChecking;
      body.appendChild(empty);
    } else if (updatesError && !updates.length && !doneItems.length) {
      const empty = document.createElement("div");
      empty.className = "qbb-lh-empty";
      empty.innerHTML =
        "<strong>" +
        (S.emptyUpdatesErrorTitle || S.updateTitleNone) +
        "</strong>" +
        S.updateError(updatesData.message || "unknown");
      body.appendChild(empty);
    } else if (!updCount && !doneItems.length) {
      const empty = document.createElement("div");
      empty.className = "qbb-lh-empty";
      empty.innerHTML =
        "<strong>" +
        (S.emptyUpdatesTitle || S.updateTitleNone) +
        "</strong>" +
        (S.emptyUpdatesSub || S.updateSubNone);
      body.appendChild(empty);
    } else {
      if (updCount || doneItems.length) {
        const hint = document.createElement("div");
        hint.className = "qbb-lh-empty";
        hint.style.padding = "4px 4px 10px";
        hint.style.textAlign = "left";
        hint.textContent = S.updateRestartHint;
        body.appendChild(hint);
      }

      function appendUpdateCard(item, { done = false } = {}) {
        const card = document.createElement("div");
        card.className = "qbb-lh-card " + (done ? "done" : "update");

        const row = document.createElement("div");
        row.className = "qbb-lh-card-row";

        const name = document.createElement("div");
        name.className = "qbb-lh-card-name " + (done ? "done" : "update");
        name.textContent = item.name || "(unknown)";
        name.title = item.name || "";

        const badge = document.createElement("span");
        badge.className = "qbb-lh-badge " + (done ? "done" : "update");
        badge.textContent = done ? S.updateSuccess || S.updateOneDone : S.updateBadge;

        const actions = document.createElement("div");
        actions.className = "qbb-lh-card-actions";

        row.appendChild(name);
        row.appendChild(badge);
        row.appendChild(actions);
        card.appendChild(row);

        const statusEl = document.createElement("div");
        statusEl.className = "qbb-lh-update-status";
        if (done) statusEl.textContent = S.updateSuccessHint || "";
        card.appendChild(statusEl);

        const meta = document.createElement("details");
        meta.className = "qbb-lh-card-meta";
        const summary = document.createElement("summary");
        summary.textContent = S.viewUpdateDetails || S.viewDetails || S.updateOpen;
        const metaBody = document.createElement("div");
        metaBody.className = "qbb-lh-meta-body";

        const sha = document.createElement("div");
        sha.className = "qbb-lh-sha";
        const branch = item.branch ? " · " + S.updateBranch + ": " + item.branch : "";
        if (done) {
          sha.textContent =
            S.updateLocal +
            " " +
            shortSha(item.local_sha || item.remote_sha) +
            (branch || "");
        } else {
          sha.textContent =
            S.updateLocal +
            " " +
            shortSha(item.local_sha) +
            " → " +
            S.updateRemote +
            " " +
            shortSha(item.remote_sha) +
            branch;
        }
        metaBody.appendChild(sha);

        if (item.path) {
          const pathEl = document.createElement("div");
          pathEl.className = "qbb-lh-path";
          pathEl.textContent = item.path;
          metaBody.appendChild(pathEl);
        }

        if (item.remote_url) {
          const link = document.createElement("a");
          link.className = "qbb-lh-link";
          link.href = item.remote_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = S.updateOpen;
          metaBody.appendChild(link);
        }

        meta.appendChild(summary);
        meta.appendChild(metaBody);
        card.appendChild(meta);

        if (!done) {
          const oneBtn = document.createElement("button");
          oneBtn.type = "button";
          oneBtn.className = "qbb-lh-btn-update";
          oneBtn.textContent = S.updateOne;
          actions.appendChild(oneBtn);

          oneBtn.onclick = async () => {
            oneBtn.disabled = true;
            oneBtn.textContent = S.updateOneWorking;
            statusEl.classList.remove("err");
            statusEl.textContent = "";
            try {
              const result = await pullPlugins({ paths: [item.path] });
              const first = (result.results || [])[0] || {};
              if (!result.ok && first.ok === false) {
                throw new Error(first.error || result.error || "pull failed");
              }
              markPluginsUpdated([
                {
                  ...item,
                  name: first.name || item.name,
                  path: first.path || item.path,
                  local_sha: first.local_sha || item.remote_sha || item.local_sha,
                  remote_sha: first.local_sha || item.remote_sha || item.local_sha,
                  branch: first.branch || item.branch,
                },
              ]);
              cachedUpdates = result.report || (await fetchUpdates());
              applyButtonState();
              showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, {
                activeTab: "updates",
              });
            } catch (err) {
              console.warn("[QBB NodeOps] pull one failed", err);
              statusEl.classList.add("err");
              statusEl.textContent = S.updateOneFailed + ": " + (err.message || err);
              oneBtn.disabled = false;
              oneBtn.textContent = S.updateOne;
            }
          };
        }

        body.appendChild(card);
      }

      for (const item of updates) appendUpdateCard(item, { done: false });
      for (const item of doneItems) appendUpdateCard(item, { done: true });
    }

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "qbb-lh-btn qbb-lh-btn-ghost";
    refreshBtn.textContent = S.updateRefresh;
    refreshBtn.onclick = async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = S.menuUpdatesChecking;
      try {
        await fetch("/qbb_nodeops/updates/refresh", { method: "POST" });
        showAttentionModal(
          cachedReport || { attention: [] },
          {
            status: "running",
            updates: [],
            update_count: 0,
            network_mode: updatesData?.network_mode,
          },
          { activeTab: "updates" },
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
      updateAllBtn.className = "qbb-lh-btn qbb-lh-btn-update-all";
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
          const ok = result.succeeded || result.updated || 0;
          const fail = result.failed || 0;
          alert(S.updateAllResult(ok, fail));
          cachedUpdates = result.report || (await fetchUpdates());
          applyButtonState();
          showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, {
            activeTab: "updates",
          });
        } catch (err) {
          console.warn("[QBB NodeOps] pull all failed", err);
          alert(S.updateError(err.message || err));
          updateAllBtn.disabled = false;
          updateAllBtn.textContent = S.updateAll;
        }
      };
      footerRight.appendChild(updateAllBtn);
    }

    const closeBtn = document.createElement("button");
    closeBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
    closeBtn.textContent = S.gotIt;
    closeBtn.onclick = () => overlay.remove();
    footerRight.appendChild(closeBtn);
  }

  function renderHotTopicsTab() {
    clear(body);
    clear(footerLeft);
    clear(footerRight);
    const header = document.createElement("section");
    header.className = "qbb-lh-feed-header";
    const searchForm = document.createElement("form");
    searchForm.className = "qbb-lh-feed-search";
    const searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.value = hotTopicQuery;
    searchInput.placeholder = S.hotTopicsSearchPlaceholder;
    searchInput.setAttribute("aria-label", S.hotTopicsSearchPlaceholder);
    const searchButton = document.createElement("button");
    searchButton.type = "submit";
    searchButton.className = "qbb-lh-btn qbb-lh-btn-primary";
    searchButton.textContent = S.hotTopicsSearch;
    searchButton.disabled = cachedHotTopics?.status === "loading";
    searchForm.append(searchInput, searchButton);
    searchForm.onsubmit = (event) => {
      event.preventDefault();
      searchInput.blur();
      refreshHotTopics(hotTopicDays, searchInput.value);
    };
    header.appendChild(searchForm);
    body.appendChild(header);

    const toolbar = document.createElement("div");
    toolbar.className = "qbb-lh-feed-toolbar";
    for (const [days, label] of [[7, S.hotTopicsWeek], [30, S.hotTopicsMonth]]) {
      const button = document.createElement("button");
      button.className = "qbb-lh-btn qbb-lh-btn-ghost" + (hotTopicDays === days ? " active" : "");
      button.textContent = label;
      button.onclick = () => refreshHotTopics(days, hotTopicQuery);
      toolbar.appendChild(button);
    }
    const refresh = document.createElement("button");
    refresh.className = "qbb-lh-btn qbb-lh-btn-primary";
    refresh.textContent = S.hotTopicsRefresh;
    refresh.disabled = cachedHotTopics?.status === "loading";
    refresh.onclick = () => refreshHotTopics(hotTopicDays, hotTopicQuery);
    toolbar.appendChild(refresh);
    footerLeft.appendChild(toolbar);

    if (!cachedHotTopics) {
      const empty = document.createElement("div");
      empty.className = "qbb-lh-empty";
      empty.textContent = S.hotTopicsEmpty;
      body.appendChild(empty);
    } else if (cachedHotTopics.status === "loading") {
      const loading = document.createElement("div");
      loading.className = "qbb-lh-empty";
      loading.textContent = S.hotTopicsLoading;
      body.appendChild(loading);
    } else if (cachedHotTopics.status === "error") {
      const error = document.createElement("pre");
      error.className = "qbb-lh-tb failed";
      error.textContent = S.aiError(cachedHotTopics.error || "");
      body.appendChild(error);
    } else {
      const summary = document.createElement("article");
      summary.className = "qbb-lh-feed-summary";
      summary.textContent = cachedHotTopics.result || "";
      if (summary.textContent) body.appendChild(summary);
      const sourceTitle = document.createElement("h3");
      sourceTitle.textContent = hotTopicQuery ? S.hotTopicsSearchResult : S.hotTopicsSources;
      body.appendChild(sourceTitle);
      const sources = document.createElement("div");
      sources.className = "qbb-lh-feed-sources";
      for (const item of cachedHotTopics.candidates || []) {
        const card = document.createElement("button");
        card.className = "qbb-lh-feed-source";
        const name = document.createElement("strong");
        name.textContent = item.title || item.name || "GitHub project";
        const url = document.createElement("span");
        url.className = "qbb-lh-feed-url";
        url.textContent = item.url || "";
        const description = document.createElement("span");
        description.className = "qbb-lh-feed-description";
        description.textContent = item.summary || item.description || S.hotTopicsNoDescription;
        const meta = document.createElement("span");
        meta.className = "qbb-lh-feed-meta";
        meta.textContent = `★ ${item.stars || 0}  ·  ${S.hotTopicsUpdated} ${item.updated_at || ""}`;
        card.append(name, url, description, meta);
        card.onclick = () => { if (item.url) window.open(item.url, "_blank", "noopener"); };
        sources.appendChild(card);
      }
      body.appendChild(sources);
      const note = document.createElement("div");
      note.className = "qbb-lh-export-hint";
      note.textContent = S.hotTopicsNote;
      body.appendChild(note);
    }
    const close = document.createElement("button");
    close.className = "qbb-lh-btn qbb-lh-btn-primary";
    close.textContent = S.gotIt;
    close.onclick = () => overlay.remove();
    footerRight.appendChild(close);
  }

  function renderInstanceMigrationWorkspace(scan, status, running) {
    const instances = cachedInstances || [];
    const source = instances.find((item) => item.id === migrationSourceId) || instances[0];
    const instanceSection = document.createElement("section");
    instanceSection.className = "qbb-lh-instance-section";
    const heading = document.createElement("div");
    heading.className = "qbb-lh-instance-heading";
    const title = document.createElement("strong");
    title.textContent = S.instancesTitle;
    const hint = document.createElement("span");
    hint.className = "qbb-lh-export-hint";
    hint.textContent = S.instanceSource;
    heading.append(title, hint);
    const cards = document.createElement("div");
    cards.className = "qbb-lh-instance-cards";
    for (const instance of instances) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "qbb-lh-instance-card" + (source?.id === instance.id && migrationMode !== "new" ? " selected" : "");
      card.disabled = running;
      const name = document.createElement("div");
      name.className = "qbb-lh-instance-name";
      name.textContent = instance.name + (instance.current ? ` · ${S.instanceCurrent}` : "");
      const meta = document.createElement("div");
      meta.className = "qbb-lh-instance-meta";
      meta.textContent = `${instance.plugin_count || 0} plugins · ${instance.python_kind || "Python"}`;
      meta.title = instance.comfyui_root;
      card.append(name, meta);
      card.onclick = () => {
        if (source?.id === instance.id) return;
        migrationSourceId = instance.id;
        migrationTargetId = "";
        migrationMode = "";
        migrationSelection.clear();
        cachedMigration = null;
        loadMigrationData(true);
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      };
      cards.appendChild(card);
    }
    if (migrationMode === "new" && migrationDestination) {
      const targetCard = document.createElement("button");
      targetCard.type = "button";
      targetCard.className = "qbb-lh-instance-card selected";
      targetCard.disabled = running;
      const targetName = document.createElement("div");
      targetName.className = "qbb-lh-instance-name";
      targetName.textContent = (newInstanceName || migrationDestination.split(/[\\/]/).filter(Boolean).pop() || S.instanceTarget) + ` · ${S.instanceTarget}`;
      const targetMeta = document.createElement("div");
      targetMeta.className = "qbb-lh-instance-meta";
      targetMeta.textContent = migrationDestination;
      targetCard.append(targetName, targetMeta);
      targetCard.onclick = () => {
        migrationMode = "";
        migrationDestination = "";
        newInstanceName = "";
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      };
      cards.appendChild(targetCard);
    }
    const add = document.createElement("button");
    add.type = "button";
    add.className = "qbb-lh-instance-card add";
    add.disabled = running;
    add.innerHTML = "<strong>＋</strong><span>" + S.instanceAdd + "</span>";
    add.onclick = () => {
      addInstanceFormOpen = !addInstanceFormOpen;
      showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
    };
    cards.appendChild(add);
    instanceSection.append(heading, cards);
    if (addInstanceFormOpen) {
      const form = document.createElement("form");
      form.className = "qbb-lh-instance-form";
      const modeField = document.createElement("label");
      modeField.textContent = S.instanceSetupMode;
      const modeSelect = document.createElement("select");
      modeSelect.className = "qbb-lh-export-field";
      for (const [value, label] of [["new", S.instanceCreateNew], ["import", S.instanceImportExisting]]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.selected = addInstanceDraft.mode === value;
        modeSelect.appendChild(option);
      }
      modeSelect.onchange = () => {
        addInstanceDraft.mode = modeSelect.value;
        addInstanceDraft.path = "";
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      };
      modeField.appendChild(modeSelect);
      const pathField = document.createElement("label");
      pathField.textContent = addInstanceDraft.mode === "new" ? S.instanceNewPath : S.instancePath;
      const pathInput = document.createElement("input");
      pathInput.type = "text";
      pathInput.className = "qbb-lh-export-field";
      pathInput.placeholder = addInstanceDraft.mode === "new" ? "F:\\My_ComfyUI_Instance" : "E:\\ComfyUI\\ComfyUI";
      pathInput.value = addInstanceDraft.path;
      pathInput.oninput = () => { addInstanceDraft.path = pathInput.value; };
      pathField.appendChild(pathInput);
      const nameField = document.createElement("label");
      nameField.textContent = S.instanceName;
      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.className = "qbb-lh-export-field";
      nameInput.placeholder = S.instanceName;
      nameInput.value = addInstanceDraft.name;
      nameInput.oninput = () => { addInstanceDraft.name = nameInput.value; };
      nameField.appendChild(nameInput);
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.className = "qbb-lh-btn qbb-lh-btn-primary";
      submit.textContent = addInstanceDraft.mode === "new" ? S.instanceCreateNew : S.instanceImportExisting;
      submit.disabled = running;
      const note = document.createElement("div");
      note.className = "qbb-lh-instance-form-note";
      note.textContent = addInstanceDraft.mode === "new" ? S.instanceNewRuntimeNote : S.instanceRuntimeNote;
      form.append(modeField, pathField, nameField, submit, note);
      form.onsubmit = async (event) => {
        event.preventDefault();
        pathInput.blur();
        try {
          const path = addInstanceDraft.path.trim();
          if (!path) throw new Error(S.migrationNeedTarget);
          if (addInstanceDraft.mode === "new") {
            migrationMode = "new";
            migrationTargetId = "";
            migrationDestination = path;
            newInstanceName = addInstanceDraft.name.trim();
          } else {
            const instance = await addInstanceFromUI(path, addInstanceDraft.name.trim());
            migrationTargetId = instance.id;
            migrationMode = "instance";
          }
          addInstanceDraft = { mode: addInstanceDraft.mode, path: "", name: "" };
          addInstanceFormOpen = false;
          showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
        } catch (err) { alert(err.message || err); }
      };
      instanceSection.appendChild(form);
    }
    body.appendChild(instanceSection);

    const pluginsStep = document.createElement("section");
    pluginsStep.className = "qbb-lh-migration-step";
    const pluginsHeading = document.createElement("div");
    pluginsHeading.className = "qbb-lh-instance-heading";
    const pluginsTitle = document.createElement("strong");
    pluginsTitle.textContent = (source?.name || S.instanceSource) + " · " + S.migrationPlugins + " · " + S.migrationSelected(migrationSelection.size);
    const tools = document.createElement("div");
    const selectAll = document.createElement("button");
    selectAll.className = "qbb-lh-btn qbb-lh-btn-ghost";
    selectAll.textContent = S.migrationSelectAll;
    selectAll.disabled = running;
    selectAll.onclick = () => { migrationSelection = new Set((scan.plugins || []).map((item) => item.path)); showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" }); };
    const clearAll = document.createElement("button");
    clearAll.className = "qbb-lh-btn qbb-lh-btn-ghost";
    clearAll.textContent = S.migrationClearAll;
    clearAll.disabled = running;
    clearAll.onclick = () => { migrationSelection.clear(); showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" }); };
    tools.append(selectAll, clearAll);
    pluginsHeading.append(pluginsTitle, tools);
    pluginsStep.appendChild(pluginsHeading);
    const list = document.createElement("div");
    list.className = "qbb-lh-migration-list";
    for (const plugin of scan.plugins || []) {
      const option = document.createElement("label");
      option.className = "qbb-lh-migration-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = migrationSelection.has(plugin.path);
      checkbox.disabled = running;
      checkbox.onchange = () => {
        if (checkbox.checked) migrationSelection.add(plugin.path); else migrationSelection.delete(plugin.path);
        pluginsTitle.textContent = (source?.name || S.instanceSource) + " · " + S.migrationPlugins + " · " + S.migrationSelected(migrationSelection.size);
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      };
      const name = document.createElement("span");
      name.className = "qbb-lh-migration-name";
      name.textContent = plugin.name;
      name.title = plugin.path;
      const kind = document.createElement("span");
      kind.className = "qbb-lh-migration-kind" + (plugin.git ? "" : " local");
      kind.textContent = plugin.git ? S.migrationGit : S.migrationLocal;
      option.append(checkbox, name, kind);
      list.appendChild(option);
    }
    pluginsStep.appendChild(list);
    body.appendChild(pluginsStep);

    const destinationHost = document.createElement("section");
    destinationHost.className = "qbb-lh-migration-step";
    function renderDestination() {
      destinationHost.replaceChildren();
      if (!migrationSelection.size) return;
      const label = document.createElement("label");
      label.textContent = S.instanceTarget;
      const destination = document.createElement("select");
      destination.className = "qbb-lh-export-field";
      const options = [
        ["", `— ${S.instanceTarget} —`],
        ["new", S.migrationModeNew],
        ["existing", S.migrationModeExisting],
        ...instances.filter((item) => item.id !== source?.id).map((item) => [`instance:${item.id}`, item.name]),
      ];
      for (const [value, text] of options) {
        const option = document.createElement("option"); option.value = value; option.textContent = text;
        option.selected = (migrationMode === "instance" ? `instance:${migrationTargetId}` : migrationMode) === value;
        destination.appendChild(option);
      }
      destination.disabled = running;
      destination.onchange = () => {
        const value = destination.value;
        migrationDeleteSource = false;
        if (value.startsWith("instance:")) { migrationMode = "instance"; migrationTargetId = value.slice(9); migrationDestination = ""; }
        else { migrationMode = value || ""; migrationTargetId = ""; migrationDestination = value === "new" ? (scan.source_portable || "") + "-migrated" : ""; }
        renderDestination();
      };
      destinationHost.append(label, destination);
      if (migrationMode === "new" || migrationMode === "existing") {
        const path = document.createElement("input");
        path.type = "text";
        path.className = "qbb-lh-export-field";
        path.value = migrationDestination;
        path.placeholder = migrationMode === "new" ? scan.source_portable + "-migrated" : "E:\\Other_ComfyUI\\ComfyUI";
        path.disabled = running;
        path.oninput = () => { migrationDestination = path.value; };
        destinationHost.appendChild(path);
      }
      if (migrationMode === "existing" || migrationMode === "instance") {
        const remove = document.createElement("label");
        remove.className = "qbb-lh-network-choice";
        const box = document.createElement("input"); box.type = "checkbox"; box.checked = migrationDeleteSource; box.disabled = running;
        box.onchange = () => { migrationDeleteSource = box.checked; };
        remove.append(box, document.createTextNode(S.migrationDeleteSource));
        destinationHost.appendChild(remove);
      }
    }
    renderDestination();
    body.appendChild(destinationHost);
    const reload = document.createElement("button");
    reload.className = "qbb-lh-btn qbb-lh-btn-ghost";
    reload.textContent = S.migrationReload;
    reload.disabled = running;
    reload.onclick = () => loadMigrationData(true);
    footerLeft.appendChild(reload);
    const close = document.createElement("button");
    close.className = "qbb-lh-btn qbb-lh-btn-primary";
    close.textContent = S.gotIt;
    close.onclick = () => overlay.remove();
    footerRight.appendChild(close);
    if (!running && migrationSelection.size) {
      const start = document.createElement("button");
      start.className = "qbb-lh-btn qbb-lh-btn-update-all";
      start.textContent = S.migrationStart;
      start.onclick = () => startMigrationFromUI(migrationDestination.trim(), migrationMode, migrationDeleteSource);
      footerRight.prepend(start);
    }
    return true;
  }

  function renderMigrationTab() {
    clear(body);
    clear(footerLeft);
    clear(footerRight);

    const scan = cachedMigration;
    if (!scan) {
      const empty = document.createElement("div");
      empty.className = "qbb-lh-empty";
      empty.innerHTML = "<strong>" + S.migrationPreparing + "</strong>" + S.migrationIntro;
      body.appendChild(empty);
      if (!migrationLoading) loadMigrationData();
      return;
    }

    const status = scan.status || { status: "idle" };
    const running = status.status === "running";
    if (renderInstanceMigrationWorkspace(scan, status, running)) return;
    const existingMode = migrationMode === "existing";

    const instances = cachedInstances || [];
    const instanceSection = document.createElement("div");
    instanceSection.className = "qbb-lh-migration-section";
    const instanceTitle = document.createElement("span");
    instanceTitle.textContent = S.instancesTitle;
    const instanceHint = document.createElement("div");
    instanceHint.className = "qbb-lh-export-hint";
    instanceHint.textContent = S.instancesHint;
    const instanceGrid = document.createElement("div");
    instanceGrid.className = "qbb-lh-migration-list";
    for (const instance of instances) {
      const card = document.createElement("div");
      card.className = "qbb-lh-migration-item";
      const details = document.createElement("div");
      details.style.minWidth = "0";
      const name = document.createElement("div");
      name.className = "qbb-lh-migration-name";
      name.textContent = instance.name + (instance.current ? ` · ${S.instanceCurrent}` : "");
      const meta = document.createElement("div");
      meta.className = "qbb-lh-export-hint";
      meta.textContent = `${instance.python_kind || "Python"} · ${instance.plugin_count || 0} plugins`;
      meta.title = instance.comfyui_root;
      details.append(name, meta);
      const actions = document.createElement("div");
      actions.className = "qbb-lh-migration-tools";
      const source = document.createElement("button");
      source.className = "qbb-lh-btn qbb-lh-btn-ghost";
      source.textContent = migrationSourceId === instance.id ? `✓ ${S.instanceSource}` : S.instanceSource;
      source.disabled = running;
      source.onclick = () => {
        migrationSourceId = instance.id;
        if (migrationTargetId === instance.id) migrationTargetId = "";
        migrationSelection.clear();
        cachedMigration = null;
        loadMigrationData(true);
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      };
      const target = document.createElement("button");
      target.className = "qbb-lh-btn qbb-lh-btn-ghost";
      target.textContent = migrationTargetId === instance.id ? `✓ ${S.instanceTarget}` : S.instanceTarget;
      target.disabled = running || migrationSourceId === instance.id;
      target.onclick = () => {
        migrationTargetId = instance.id;
        migrationMode = "instance";
        migrationDestination = "";
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      };
      actions.append(source, target);
      if (!instance.current) {
        const remove = document.createElement("button");
        remove.className = "qbb-lh-icon-btn";
        remove.textContent = "×";
        remove.title = S.instanceRemove;
        remove.disabled = running;
        remove.onclick = async () => {
          if (!confirm(`${S.instanceRemove}: ${instance.name}?`)) return;
          const res = await fetch(`/qbb_nodeops/instances/${encodeURIComponent(instance.id)}`, { method: "DELETE" });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || data.ok === false) return alert(data.error || "Failed");
          cachedInstances = data.instances || [];
          if (migrationTargetId === instance.id) migrationTargetId = "";
          if (migrationSourceId === instance.id) migrationSourceId = "current";
          showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
        };
        actions.appendChild(remove);
      }
      card.append(details, actions);
      instanceGrid.appendChild(card);
    }
    const addField = document.createElement("div");
    addField.className = "qbb-lh-export-field";
    const addPath = document.createElement("input");
    addPath.type = "text";
    addPath.placeholder = S.instancePath;
    const addName = document.createElement("input");
    addName.type = "text";
    addName.placeholder = S.instanceName;
    const addButton = document.createElement("button");
    addButton.className = "qbb-lh-btn qbb-lh-btn-ghost";
    addButton.textContent = S.instanceAdd;
    addButton.disabled = running;
    addButton.onclick = async () => {
      try {
        const added = await addInstanceFromUI(addPath.value.trim(), addName.value.trim());
        migrationTargetId = added.id;
        migrationMode = "instance";
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      } catch (err) { alert(err.message || err); }
    };
    addField.append(addPath, addName, addButton);
    instanceSection.append(instanceTitle, instanceHint, instanceGrid, addField);
    body.appendChild(instanceSection);

    const modeChoices = document.createElement("div");
    modeChoices.className = "qbb-lh-network-choices";
    const addModeChoice = (value, label) => {
      const field = document.createElement("label");
      field.className = "qbb-lh-network-choice";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "qbb-migration-mode";
      input.checked = migrationMode === value;
      input.disabled = running;
      input.addEventListener("change", () => {
        if (!input.checked) return;
        migrationMode = value;
        migrationTargetId = "";
        migrationDeleteSource = false;
        migrationDestination = value === "new" ? (scan.source_portable || "") + "-migrated" : "";
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      });
      field.append(input, document.createTextNode(label));
      modeChoices.appendChild(field);
    };
    addModeChoice("new", S.migrationModeNew);
    addModeChoice("existing", S.migrationModeExisting);
    body.appendChild(modeChoices);

    const intro = document.createElement("div");
    intro.className = "qbb-lh-migration-intro";
    intro.textContent = migrationMode === "instance" ? S.migrationExistingHint : existingMode ? S.migrationExistingHint : S.migrationIntro + " " + S.migrationModelNote;
    body.appendChild(intro);

    const targetField = document.createElement("div");
    targetField.className = "qbb-lh-export-field";
    const targetLabel = document.createElement("label");
    targetLabel.textContent = existingMode ? S.migrationExistingTarget : S.migrationTarget;
    const targetInput = document.createElement("input");
    targetInput.type = "text";
    targetInput.placeholder = existingMode
      ? "E:\\Other_ComfyUI\\ComfyUI"
      : scan.source_portable ? scan.source_portable + "-migrated" : "D:\\ComfyUI_Migrated";
    targetInput.value = migrationDestination || "";
    targetInput.disabled = running;
    targetInput.addEventListener("input", () => {
      migrationDestination = targetInput.value;
    });
    const targetHint = document.createElement("div");
    targetHint.className = "qbb-lh-export-hint";
    targetHint.textContent = existingMode ? S.migrationExistingTargetHint : S.migrationTargetHint;
    targetField.appendChild(targetLabel);
    targetField.appendChild(targetInput);
    targetField.appendChild(targetHint);
    if (migrationMode !== "instance") body.appendChild(targetField);

    if (existingMode || migrationMode === "instance") {
      const deleteField = document.createElement("label");
      deleteField.className = "qbb-lh-network-choice";
      const deleteInput = document.createElement("input");
      deleteInput.type = "checkbox";
      deleteInput.checked = migrationDeleteSource;
      deleteInput.disabled = running;
      deleteInput.onchange = () => { migrationDeleteSource = deleteInput.checked; };
      const deleteText = document.createElement("span");
      deleteText.textContent = S.migrationDeleteSource;
      deleteField.append(deleteInput, deleteText);
      body.appendChild(deleteField);
      const deleteHint = document.createElement("div");
      deleteHint.className = "qbb-lh-export-hint";
      deleteHint.textContent = S.migrationDeleteSourceHint;
      body.appendChild(deleteHint);
    }

    const section = document.createElement("div");
    section.className = "qbb-lh-migration-section";
    const sectionTitle = document.createElement("span");
    sectionTitle.textContent = S.migrationPlugins + " · " + S.migrationSelected(migrationSelection.size);
    const sectionTools = document.createElement("div");
    sectionTools.className = "qbb-lh-migration-tools";
    const selectAll = document.createElement("button");
    selectAll.className = "qbb-lh-btn qbb-lh-btn-ghost";
    selectAll.textContent = S.migrationSelectAll;
    selectAll.disabled = running;
    selectAll.onclick = () => {
      migrationSelection = new Set((scan.plugins || []).map((p) => p.path));
      showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
    };
    const clearAll = document.createElement("button");
    clearAll.className = "qbb-lh-btn qbb-lh-btn-ghost";
    clearAll.textContent = S.migrationClearAll;
    clearAll.disabled = running;
    clearAll.onclick = () => {
      migrationSelection.clear();
      showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
    };
    sectionTools.appendChild(selectAll);
    sectionTools.appendChild(clearAll);
    section.appendChild(sectionTitle);
    section.appendChild(sectionTools);
    body.appendChild(section);

    const list = document.createElement("div");
    list.className = "qbb-lh-migration-list";
    for (const plugin of scan.plugins || []) {
      const option = document.createElement("label");
      option.className = "qbb-lh-migration-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = migrationSelection.has(plugin.path);
      checkbox.disabled = running;
      checkbox.onchange = () => {
        if (checkbox.checked) migrationSelection.add(plugin.path);
        else migrationSelection.delete(plugin.path);
        sectionTitle.textContent = S.migrationPlugins + " · " + S.migrationSelected(migrationSelection.size);
      };
      const name = document.createElement("span");
      name.className = "qbb-lh-migration-name";
      name.textContent = plugin.name;
      name.title = plugin.path;
      const kind = document.createElement("span");
      kind.className = "qbb-lh-migration-kind" + (plugin.git ? "" : " local");
      kind.textContent = plugin.git ? S.migrationGit : S.migrationLocal;
      option.appendChild(checkbox);
      option.appendChild(name);
      option.appendChild(kind);
      list.appendChild(option);
    }
    body.appendChild(list);

    if (status.status !== "idle") {
      const progress = document.createElement("div");
      progress.className = "qbb-lh-migration-progress" + (status.status === "error" ? " error" : "");
      const heading = document.createElement("strong");
      heading.textContent =
        status.status === "success"
          ? S.migrationSuccess
          : status.status === "error"
            ? S.migrationFailed
            : S.migrationWorking;
      progress.appendChild(heading);
      const detail = document.createElement("div");
      detail.textContent =
        (status.current || status.phase || "") +
        (status.total ? ` (${status.completed || 0}/${status.total})` : "") +
        (status.error ? ` — ${status.error}` : "");
      progress.appendChild(detail);
      if (status.logs?.length) {
        const log = document.createElement("pre");
        log.className = "qbb-lh-migration-log";
        log.textContent = status.logs.join("\n");
        progress.appendChild(log);
      }
      body.appendChild(progress);
    }

    const reload = document.createElement("button");
    reload.className = "qbb-lh-btn qbb-lh-btn-ghost";
    reload.textContent = S.migrationReload;
    reload.disabled = running;
    reload.onclick = () => loadMigrationData(true);
    footerLeft.appendChild(reload);

    const closeBtn = document.createElement("button");
    closeBtn.className = "qbb-lh-btn qbb-lh-btn-primary";
    closeBtn.textContent = S.gotIt;
    closeBtn.onclick = () => overlay.remove();
    footerRight.appendChild(closeBtn);

    if (!running && status.status !== "success") {
      const startBtn = document.createElement("button");
      startBtn.className = "qbb-lh-btn qbb-lh-btn-update-all";
      startBtn.textContent = S.migrationStart;
      startBtn.onclick = () => startMigrationFromUI(targetInput.value.trim(), migrationMode, migrationDeleteSource);
      footerRight.prepend(startBtn);
    }
  }

  function setTab(tab) {
    activeTab = tab;
    tabLoad.classList.toggle("active", tab === "load");
    tabUpdates.classList.toggle("active", tab === "updates");
    tabMigration.classList.toggle("active", tab === "migration");
    tabHot.classList.toggle("active", tab === "hot");
    if (tab === "load") renderLoadTab();
    else if (tab === "updates") renderUpdatesTab();
    else if (tab === "migration") renderMigrationTab();
    else {
      renderHotTopicsTab();
      // The feed opens like a browser page: load its first digest automatically,
      // but never re-fetch an already displayed result while changing tabs.
      if (!cachedHotTopics) refreshHotTopics(hotTopicDays);
    }
  }

  tabLoad.onclick = () => setTab("load");
  tabUpdates.onclick = () => setTab("updates");
  tabMigration.onclick = () => setTab("migration");
  tabHot.onclick = () => setTab("hot");

  modal.appendChild(header);
  modal.appendChild(tabs);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  setTab(activeTab);
  if (prevBodyScroll) body.scrollTop = prevBodyScroll;
  window.scrollTo(pageX, pageY);
  requestAnimationFrame(() => window.scrollTo(pageX, pageY));
}

function shortSha(sha) {
  const s = String(sha || "");
  return s.length > 7 ? s.slice(0, 7) : s;
}

async function fetchReport() {
  const res = await fetch("/qbb_nodeops/failures");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function fetchUpdates() {
  const res = await fetch("/qbb_nodeops/updates");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function pullPlugins({ paths = null, all = false } = {}) {
  const body = all ? { all: true } : { paths: paths || [] };
  const res = await fetch("/qbb_nodeops/updates/pull", {
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

async function fetchMigrationScan(sourceInstanceId = migrationSourceId) {
  const res = await fetch(`/qbb_nodeops/migration/scan?source_instance_id=${encodeURIComponent(sourceInstanceId)}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function fetchInstances() {
  const res = await fetch("/qbb_nodeops/instances");
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data.instances || [];
}

async function addInstanceFromUI(path, name) {
  const res = await fetch("/qbb_nodeops/instances", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  cachedInstances = data.instances || [];
  return data.instance;
}

async function fetchMigrationStatus() {
  const res = await fetch("/qbb_nodeops/migration/status");
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function loadMigrationData(force = false) {
  if (migrationLoading && !force) return;
  migrationLoading = true;
  try {
    const [scan, instances] = await Promise.all([fetchMigrationScan(), fetchInstances()]);
    cachedMigration = scan;
    cachedInstances = instances;
    if (migrationMode === "new" && !migrationDestination && scan.source_portable) {
      migrationDestination = scan.source_portable + "-migrated";
    }
    const validPaths = new Set((scan.plugins || []).map((p) => p.path));
    migrationSelection = new Set([...migrationSelection].filter((p) => validPaths.has(p)));
  } catch (err) {
    cachedMigration = { plugins: [], status: { status: "error", error: err.message || String(err) } };
    console.warn("[QBB NodeOps] migration scan failed", err);
  } finally {
    migrationLoading = false;
    const modalOpen = document.querySelector(".qbb-lh-overlay");
    if (modalOpen) {
      showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
    }
  }
}

async function pollMigration() {
  let previousSignature = "";
  for (let i = 0; i < 720; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const status = await fetchMigrationStatus();
      cachedMigration = { ...(cachedMigration || {}), status };
      const modalOpen = document.querySelector(".qbb-lh-overlay");
      const signature = [
        status.status,
        status.phase,
        status.current,
        status.completed,
        status.total,
        status.error,
        status.logs?.length || 0,
      ].join("|");
      if (modalOpen && signature !== previousSignature) {
        showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
      }
      previousSignature = signature;
      if (status.status !== "running") return status;
    } catch (err) {
      console.warn("[QBB NodeOps] migration status failed", err);
      return null;
    }
  }
  return null;
}

async function startMigrationFromUI(destinationRoot, mode = "new", deleteSource = false) {
  const S = t();
  const selected = [...migrationSelection];
  const selectedTarget = (cachedInstances || []).find((instance) => instance.id === migrationTargetId);
  const targetLabel = mode === "instance" ? (selectedTarget?.comfyui_root || "") : destinationRoot;
  if ((mode !== "instance" && !destinationRoot) || (mode === "instance" && !migrationTargetId)) {
    alert(S.migrationNeedTarget);
    return;
  }
  if (!selected.length) {
    alert(S.migrationNeedPlugins);
    return;
  }
  const confirmation =
    mode === "existing" || mode === "instance"
      ? isChineseLocale()
        ? `将把 ${selected.length} 个插件迁移到已有 ComfyUI：\n${targetLabel}\n\n${deleteSource ? "复制全部成功后会删除当前环境中的原插件。此操作不可恢复。" : "原插件将保留。"}\n\n是否继续？`
        : `Migrate ${selected.length} plugin(s) to this existing ComfyUI?\n${targetLabel}\n\n${deleteSource ? "Original plugin folders will be deleted only after all copies succeed. This cannot be undone." : "Original plugin folders will remain."}\n\nContinue?`
      : S.migrationConfirm(selected.length, destinationRoot);
  if (!confirm(confirmation)) return;

  try {
    const res = await fetch("/qbb_nodeops/migration/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        destination_root: destinationRoot,
        plugins: selected,
        mode,
        delete_source: (mode === "existing" || mode === "instance") && deleteSource,
        source_instance_id: migrationSourceId,
        target_instance_id: migrationTargetId,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    migrationDestination = destinationRoot;
    cachedMigration = { ...(cachedMigration || {}), status: data };
    showAttentionModal(cachedReport || { attention: [] }, cachedUpdates, { activeTab: "migration" });
    pollMigration();
  } catch (err) {
    alert(S.migrationFailed + ": " + (err.message || err));
  }
}

async function openReport() {
  try {
    const fresh = await fetchReport();
    cachedReport = fresh;
    try {
      cachedUpdates = await fetchUpdates();
    } catch (_) {}
    applyButtonState();
    showAttentionModal(fresh, cachedUpdates);
  } catch (err) {
    if (cachedReport) showAttentionModal(cachedReport, cachedUpdates);
    else alert(t().cannotFetch);
    console.warn("[QBB NodeOps] reopen failed", err);
  }
}

async function pollUpdates({ reopenIfOpen = true, autoPopup = true } = {}) {
  let lastStatus = cachedUpdates?.status || "";
  for (let i = 0; i < 90; i++) {
    try {
      const data = await fetchUpdates();
      cachedUpdates = data;
      applyButtonState();

      const modal = document.querySelector(".qbb-lh-overlay");
      const modalOpen = !!modal;
      const activeModalTab = modal?.querySelector(".qbb-lh-tab.active")?.dataset?.tab || "";
      const foregroundDialogOpen = !!document.querySelector(".qbb-lh-export-overlay");
      const finished = data.status === "done" || data.status === "error";
      // Only rebuild modal when check finishes (not every 2s while running),
      // otherwise recreating the overlay jumps the page scroll to top.
      // Never replace an open Migration tab in the background: rebuilding the
      // modal destroys the destination input while the user is typing.
      if (reopenIfOpen && modalOpen && !foregroundDialogOpen && activeModalTab !== "migration" && finished && lastStatus !== data.status) {
        showAttentionModal(cachedReport || { attention: [] }, data);
      }
      lastStatus = data.status || "";

      if (finished) {
        const issues = issueCount(cachedReport);
        const upd = updateCount(data);
        if (autoPopup && !startupPopupShown && (issues > 0 || upd > 0) && !modalOpen) {
          startupPopupShown = true;
          showAttentionModal(cachedReport || { attention: [] }, data);
        } else if (!startupPopupShown) {
          startupPopupShown = true;
        }
        return data;
      }
    } catch (err) {
      console.warn("[QBB NodeOps] poll updates failed", err);
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  return cachedUpdates;
}

function applyButtonState() {
  const S = t();
  const issues = issueCount(cachedReport);
  const upd = updateCount(cachedUpdates);
  const label = buttonLabel(cachedReport, cachedUpdates);
  const nodes = [
    document.getElementById(BTN_ID),
    ...document.querySelectorAll("." + GROUP_CLASS + " button, ." + GROUP_CLASS + " .comfyui-button"),
  ].filter(Boolean);

  for (const el of nodes) {
    el.textContent = label;
    el.classList.toggle("has-issues", issues > 0);
    el.classList.toggle("has-updates-only", issues === 0 && upd > 0);
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
  const existingGroup = document.querySelector("." + GROUP_CLASS);
  if (existingGroup) {
    mountGroupOutermost(existingGroup);
    applyButtonState();
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
          content: buttonLabel(),
          tooltip: t().menuTooltip,
          classList: "comfyui-button",
          action: () => openReport(),
        });
        button.element.id = BTN_ID;
        const group = new ComfyButtonGroup(button);
        group.element.classList.add(GROUP_CLASS);
        mountGroupOutermost(group.element);
        applyButtonState();
        return true;
      }

      const btn = document.createElement("button");
      btn.id = BTN_ID;
      btn.type = "button";
      btn.textContent = buttonLabel();
      btn.addEventListener("click", () => openReport());
      const wrap = document.createElement("div");
      wrap.className = "comfyui-button-group " + GROUP_CLASS;
      wrap.appendChild(btn);
      mountGroupOutermost(wrap);
      applyButtonState();
      return true;
    }
  } catch (err) {
    console.warn("[QBB NodeOps] settingsGroup attach failed", err);
  }

  const host =
    document.querySelector(".comfyui-menu") ||
    document.querySelector(".comfyui-menu-right") ||
    document.querySelector(".actionbar-right") ||
    document.querySelector(".p-toolbar-group-end") ||
    document.querySelector(".p-menubar") ||
    document.querySelector("header") ||
    document.querySelector("#comfyui-body-top");

  if (host) {
    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = buttonLabel();
    btn.addEventListener("click", () => openReport());
    const wrap = document.createElement("div");
    wrap.className = GROUP_CLASS;
    wrap.style.display = "inline-flex";
    wrap.style.alignItems = "center";
    wrap.style.marginLeft = "8px";
    wrap.appendChild(btn);
    host.appendChild(wrap);
    applyButtonState();
    return true;
  }

  if (attempt < 40) {
    setTimeout(() => attachToTopMenu(attempt + 1), 400);
  } else {
    // Last resort: fixed floating button so UI is never missing
    if (!document.getElementById(BTN_ID)) {
      const btn = document.createElement("button");
      btn.id = BTN_ID;
      btn.type = "button";
      btn.textContent = buttonLabel();
      btn.style.cssText =
        "position:fixed;top:12px;right:72px;z-index:100001;border:1px solid #8a6d1d;background:#3a2f0f;color:#ffe58f;font-weight:800;font-size:12px;padding:6px 12px;border-radius:6px;cursor:pointer;";
      btn.addEventListener("click", () => openReport());
      document.body.appendChild(btn);
      applyButtonState();
      console.warn("[QBB NodeOps] attached floating fallback button");
      return true;
    }
  }
  return false;
}

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
      cachedReport = data;
      await attachToTopMenu();
      applyButtonState();
      if (normalizeItems(data).length) {
        startupPopupShown = true;
        showAttentionModal(data, cachedUpdates);
      }
    } catch (err) {
      cachedReport = { attention_count: 0, failures: [], partials: [], attention: [] };
      await attachToTopMenu();
      console.warn("[QBB NodeOps] failed to load report", err);
    }

    pollUpdates({ autoPopup: true });
  },
});
