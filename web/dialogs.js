import { renderDiagnosis } from "./diagnosis.js";
import { installPageHelp } from "./page-help.js";
import { setupSettingsWindow } from './settings-window.js';
import { t, isChineseLocale } from "./i18n.js";
import { ensureStyles } from "./styles.js";
import { nodeopsFetch } from "./http.js";
import { hooks, networkModeLabel, state } from "./shared.js";
import { pollUpdates } from "./api.js";
import { resetRuntimeNetwork } from './runtime-data.js';

export function showAiOutputDialog(titleText, pendingText, request) {
  ensureStyles();
  const existing = document.querySelector(".qbb-lh-export-overlay.qbb-lh-ai-output");
  if (existing) existing.remove();
  const S = t();
  const overlay = document.createElement("div");
  overlay.className = "qbb-lh-export-overlay qbb-lh-ai-output";
  const panel = document.createElement("div");
  panel.className = "qbb-lh-export-modal qbb-dialog-scroll";
  const title = document.createElement("h3");
  title.textContent = titleText;
  const content = document.createElement("div");
  content.className = "qbb-dialog-body";
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
  document.body.appendChild(overlay);
  const startedAt = Date.now();
  const waitingTicker = window.setInterval(() => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    if (!overlay.isConnected) {window.clearInterval(waitingTicker); return;}
    content.textContent = `${pendingText} (${elapsed}s)`;
  }, 1000);
  Promise.resolve(request())
    .then((data) => { window.clearInterval(waitingTicker); title.textContent = data.plugin ? `${titleText} · ${data.plugin}` : titleText; renderDiagnosis(content, data, isChineseLocale()); })
    .catch((err) => { content.textContent = S.aiError(err.message || String(err)); })
    .finally(() => window.clearInterval(waitingTicker));
}
export async function requestAi(path, payload) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 55000);
  let res;
  try {
    res = await nodeopsFetch(path, {
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
export function showAiSettingsDialog() {
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
  const zh = isChineseLocale();
  const providerField = document.createElement('div');
  providerField.className = 'qbb-lh-export-field';
  const provider = document.createElement('select');
  providerField.append(provider);
  for (const [value, label] of [['remote', zh ? '远程 API' : 'Remote API'], ['local', zh ? '本地模型' : 'Local model']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = label; provider.append(option);
  }
  const localUrl = makeField(zh ? '本地模型服务地址（OpenAI 兼容）' : 'Local service URL (OpenAI compatible)');
  localUrl.input.value = 'http://127.0.0.1:11434/v1';
  const localModel = makeField(zh ? '本地服务中已加载的模型名称' : 'Model name in your local service');
  const model = makeField(S.aiModel);
  const key = makeField(S.aiApiKey, "password");
  let settingsEdited = false;
  [localUrl.input, localModel.input].forEach(input => input.addEventListener('input', () => {settingsEdited = true;}));
  [base.input, model.input, key.input].forEach((input) => input.addEventListener("input", () => { settingsEdited = true; }));
  key.input.autocomplete = "off";
  const saved = document.createElement("div");
  saved.className = "qbb-lh-path";
  const clearLabel = document.createElement("label");
  clearLabel.className = "qbb-lh-network-choice";
  const clearKey = document.createElement("input");
  clearKey.type = "checkbox";
  let keyConfigured = false;
  const updateKeyIndicator = () => {
    // Display a fixed mask without retrieving the secret or submitting the
    // mask as a replacement key. An empty value still preserves the saved key.
    key.input.placeholder = keyConfigured && !clearKey.checked ? "********" : "";
    saved.textContent = clearKey.checked
      ? (zh ? '保存后将清除原密钥' : 'The saved key will be cleared on save')
      : keyConfigured ? S.aiKeySaved : "";
  };
  clearKey.addEventListener('change', () => { settingsEdited = true; updateKeyIndicator(); });
  clearLabel.append(clearKey, document.createTextNode(S.aiClearKey));
  const updateProvider = () => {
    const local = provider.value === 'local';
    for (const field of [base.field, model.field, key.field, saved, clearLabel]) field.style.display = local ? 'none' : '';
    for (const field of [localUrl.field, localModel.field]) field.style.display = local ? '' : 'none';
    hint.textContent = local ? (zh ? '先在本机模型软件中加载已有语言模型并开启 OpenAI 兼容服务，然后填写服务地址与模型名称。此模式不需要远程 API Key，也不会自动回退到远程服务。这里连接的是运行 ComfyUI 的电脑，不能直接填写模型文件路径。' : 'Load your local language model and enable its OpenAI-compatible server. Enter its URL and model name. No remote API key or remote fallback. Local means the ComfyUI server computer; model file paths are not supported here.') : S.aiSettingsHint;
  };
  provider.onchange = () => {settingsEdited = true; updateProvider();};
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
        provider: provider.value, local_url: localUrl.input.value.trim(), local_model: localModel.input.value.trim(),
      });
      overlay.remove();
      alert(S.aiSaved);
      return data;
    } catch (err) { error.textContent = S.aiError(err.message || err); }
  };
  const test = document.createElement('button');test.className='qbb-lh-btn';test.textContent=zh?'验证通信':'Test connection';
  test.title=zh?'发送一条简短测试消息，可能产生少量 API 费用；不会保存设置。':'Sends a short message; API charges may apply. Does not save settings.';
  test.onclick=async()=>{
    test.disabled=true;error.textContent=zh?'正在验证 AI 通信…':'Testing AI connection…';
    try{const data=await requestAi('/qbb_nodeops/communication-test/ai',{base_url:base.input.value.trim(),model:model.input.value.trim(),api_key:key.input.value,clear_api_key:clearKey.checked,provider:provider.value,local_url:localUrl.input.value.trim(),local_model:localModel.input.value.trim()});error.textContent=data.message;}
    catch(err){error.textContent=String(err.message||err);}finally{test.disabled=false;}
  };
  const testNote=document.createElement('small');testNote.textContent=zh?'验证会发送一条简短测试消息，可能产生少量 API 费用，不会保存设置。':'Testing sends a short message and may incur API charges; settings are not saved.';
  error.setAttribute('role','status');panel.append(testNote);
  actions.append(test, cancel, save);
  panel.append(title, providerField, hint, localUrl.field, localModel.field, base.field, model.field, key.field, saved, clearLabel, error, actions);
  installPageHelp(panel);
  updateProvider();
  overlay.appendChild(panel);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  setupSettingsWindow(overlay, panel);
  nodeopsFetch("/qbb_nodeops/ai/settings")
    .then((res) => res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`)))
    .then((data) => {
      // A slow settings request must never overwrite text the user has begun
      // entering in this modal.
      if (settingsEdited) return;
      base.input.value = data.base_url || "";
      provider.value = data.provider || 'remote';
      localUrl.input.value = data.local_url || 'http://127.0.0.1:11434/v1';
      localModel.input.value = data.local_model || '';
      model.input.value = data.model || "";
      keyConfigured = !!data.api_key_configured;
      updateKeyIndicator();
      updateProvider();
    })
    .catch((err) => { error.textContent = S.aiError(err.message || err); });
}
export async function showNetworkSettingsDialog() {
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
    const res = await nodeopsFetch("/qbb_nodeops/network_settings");
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
    hostField.hidden = !proxyActive;
    portField.hidden = !proxyActive;
    mirrorField.hidden = !mirrorActive;
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
      const res = await nodeopsFetch("/qbb_nodeops/network_settings", {
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
      resetRuntimeNetwork();
      if (recheck) {
        await nodeopsFetch("/qbb_nodeops/updates/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        state.startupPopupShown = false;
        const modalOpen = !!document.querySelector(".qbb-lh-overlay");
        if (modalOpen) {
          hooks.showAttentionModal(state.cachedReport || { attention: [] }, {
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

  const testNetwork=document.createElement('button');testNetwork.className='qbb-lh-btn';testNetwork.textContent=isChineseLocale()?'验证通信':'Test connection';
  testNetwork.onclick=async()=>{
    testNetwork.disabled=true;errBox.textContent=isChineseLocale()?'正在验证当前网络配置（不保存）…':'Testing draft network settings…';
    try{
      const port=Number(portInput.value);if(proxyEnable.checked&&(!Number.isInteger(port)||port<1||port>65535))throw Error(S.netPortInvalid);
      const response=await nodeopsFetch('/qbb_nodeops/communication-test/network',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proxy_enabled:proxyEnable.checked,proxy_host:hostInput.value.trim()||'127.0.0.1',proxy_port:port,mirror_enabled:mirrorEnable.checked,mirror_prefix:mirrorInput.value.trim()||'https://ghfast.top/'})});
      const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);errBox.textContent=data.message;
    }catch(err){errBox.textContent=String(err.message||err);}finally{testNetwork.disabled=false;}
  };
  errBox.setAttribute('role','status');actions.appendChild(testNetwork);
  actions.appendChild(cancelBtn);
  actions.appendChild(saveOnlyBtn);
  actions.appendChild(saveBtn);

  panel.appendChild(title);
  panel.appendChild(hint);
  installPageHelp(panel);
  panel.appendChild(modeLine);
  panel.appendChild(routeChoices);
  panel.appendChild(hostField);
  panel.appendChild(portField);
  panel.appendChild(mirrorField);
  panel.appendChild(errBox);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  setupSettingsWindow(overlay, panel);
  (proxyEnable.checked ? portInput : mirrorEnable.checked ? mirrorInput : proxyEnable).focus();
}
export async function showExportDialog(options = {}) {
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
    filename: kind === "deps" ? "comfyui-environment.txt" : "comfyui-nodeops.log",
  };
  try {
    const res = await nodeopsFetch(defaultsUrl);
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
  nameInput.value = defaults.filename || (kind === "deps" ? "comfyui-environment.txt" : "comfyui-nodeops.log");
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
      const res = await nodeopsFetch(`${exportUrl}?filename=${encodeURIComponent(filename)}`);
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
  installPageHelp(panel);
  panel.appendChild(nameField);
  panel.appendChild(errBox);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
  nameInput.focus();
  nameInput.select();
}
