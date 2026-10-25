import { t, isChineseLocale } from "./i18n.js";
import { nodeopsFetch } from "./http.js";
import { state, resetMigrationDraft } from "./shared.js";
import {
  addInstanceFromUI,
  loadMigrationData,
  startMigrationFromUI,
} from "./api.js";

let pluginQuery = "";

function el(tag, text = "", cls = "") {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function button(text, fn, cls = "qbb-lh-btn qbb-lh-btn-ghost") {
  const node = el("button", text, cls);
  node.type = "button";
  node.onclick = fn;
  return node;
}

function currentSource() {
  const instances = state.cachedInstances || [];
  const source = instances.find((item) => item.id === state.migrationSourceId) || instances[0];
  return source?.available === false ? null : source || null;
}

function targetReady() {
  if (state.migrationMode === "instance") {
    const target = (state.cachedInstances || []).find(item => item.id === state.migrationTargetId);
    return !!target && target.available !== false && target.id !== state.migrationSourceId;
  }
  return state.migrationMode === "existing" && !!String(state.migrationDestination || "").trim();
}

function targetLabel(scan, S) {
  if (state.migrationMode === "instance") {
    const target = (state.cachedInstances || []).find((item) => item.id === state.migrationTargetId);
    return target ? `${target.name} · ${target.comfyui_root}` : S.instanceTarget;
  }
  if (state.migrationMode === "existing") return state.migrationDestination || S.migrationExistingTarget;
  return state.migrationDestination || S.migrationTarget;
}

function clampStep() {
  let step = Number(state.migrationStep) || 1;
  if (step < 1) step = 1;
  if (step > 4) step = 4;
  if (!currentSource()) step = 1;
  else if (step > 2 && !state.migrationSelection.size) step = 2;
  else if (step > 3 && !targetReady()) step = 3;
  state.migrationStep = step;
  return step;
}

function isTerminalStatus(status) {
  return ["success", "error", "copied_unverified", "needs_attention"].includes(status);
}

function syncMigrationResult(status) {
  const result = state.migrationResult;
  if (!result || !status) return result;
  if (status.status) result.status = status.status;
  if (status.logs) result.logs = status.logs;
  if (status.error) result.error = status.error;
  result.phase = status.current || status.phase || result.phase || "";
  if (status.phase && !isTerminalStatus(status.phase)) result.stage = status.phase;
  result.mode = status.mode || result.mode;
  result.startedAt = status.started_at || result.startedAt;
  result.finishedAt = status.finished_at || result.finishedAt;
  result.completed = status.completed ?? result.completed;
  result.total = status.total ?? result.total;
  if (status.validation) result.validation = status.validation;
  if (isTerminalStatus(result.status)) {
    resetMigrationDraft();
    pluginQuery = "";
  }
  return result;
}

function resultTitle(status, S, tr) {
  if (status === "error") return S.migrationFailed;
  if (status === "running") return S.migrationWorking;
  if (status === "needs_attention") return tr("搬家完成，有待处理项", "Move finished, with items to review");
  if (status === "copied_unverified") return tr("文件已复制，尚未验证启动", "Files copied; startup not verified");
  return S.migrationSuccess;
}

function renderResult(body, right, { onVersions }) {
  const S = t();
  const tr = (zh, en) => (isChineseLocale() ? zh : en);
  const result = state.migrationResult;
  const done = isTerminalStatus(result.status);
  const failed = result.status === "error";
  const heading = el("div", "", "qbb-lh-instance-heading");
  heading.append(el("strong", S.tabMigration || S.migrationTitle));
  if (onVersions) heading.append(button(tr("版本记录", "Version records"), onVersions));
  body.append(heading);

  const panel = el("section", "", "qbb-lh-migration-step qbb-migration-result");
  const title = el("h2", resultTitle(result.status, S, tr), "qbb-page-heading");
  if (failed) title.classList.add("qbb-danger");
  else if (done) title.classList.add("qbb-ok");
  panel.append(title);
  if (state.migrationConnectionError) panel.append(el('p', state.migrationConnectionError));
  const stages = result.mode === "new"
    ? [["copy_core","复制核心"],["copy_plugins","复制插件"],["prepare_tools","准备工具"],["download_python","下载 Python"],["create_environment","创建环境"],["install_torch","安装 PyTorch"],["install_dependencies","安装依赖"],["verify_runtime","检查运行库"],["write_model_paths","配置模型路径"],["verify_dependencies","检查插件依赖"]]
    : [["copy_plugins","复制插件"],["install_dependencies","安装依赖"],["verify_dependencies","检查插件依赖"]];
  const english = {"copy_core":"Copy core","copy_plugins":"Copy plugins","prepare_tools":"Prepare tools","download_python":"Download Python","create_environment":"Create environment","install_torch":"Install PyTorch","install_dependencies":"Install dependencies","verify_runtime":"Check runtime","write_model_paths":"Configure models","verify_dependencies":"Check plugin dependencies"};
  const stageIndex = Math.max(0, stages.findIndex(([key]) => key === result.stage));
  const completedStages = done && !failed ? stages.length : stageIndex;
  const progressBox = el("div", "", "qbb-move-progress");
  progressBox.append(el("div", tr(`阶段进度 · ${completedStages}/${stages.length}`, `Stages completed · ${completedStages}/${stages.length}`)));
  const bar = el("progress");
  bar.max = stages.length;
  bar.value = completedStages;
  bar.setAttribute("aria-label", tr("搬家阶段进度", "Migration stage progress"));
  progressBox.append(bar);
  const steps = el("div", "", "qbb-move-stages");
  stages.forEach(([key, label], index) => {
    const step = el("span", (index < completedStages ? "✓ " : index === stageIndex && !done ? "● " : "") + tr(label, english[key]));
    if (index === stageIndex && !done) step.className = "active";
    steps.append(step);
  });
  progressBox.append(steps);
  const began = Date.parse(result.startedAt);
  const end = done ? Date.parse(result.finishedAt) : Date.now();
  const elapsed = Number.isFinite(began) && Number.isFinite(end) ? Math.max(0, Math.floor((end - began) / 1000)) : 0;
  progressBox.append(el("p", tr(`已用时 ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`, `Elapsed ${Math.floor(elapsed / 60)}m ${elapsed % 60}s`) + (done ? "" : tr(" · 剩余时间暂无法估算", " · Remaining time unavailable"))));
  panel.append(progressBox);
  const detail = el(
    "p",
    (result.phase || "") +
      (result.stage === "copy_plugins" && result.total ? ` (${result.completed || 0}/${result.total})` : "") +
      (result.error ? ` — ${result.error}` : ""),
    "qbb-runtime-status",
  );
  if (detail.textContent.trim()) panel.append(detail);
  if (result.status === "success" || result.status === "copied_unverified" || result.status === "needs_attention") {
    panel.append(el("p", S.migrationRestart, "qbb-page-subtitle"));
  }
  const dl = el("dl", "", "qbb-confirm-dl");
  for (const [label, value] of [
    [S.instanceSource, result.sourceLabel],
    [S.instanceTarget, result.targetLabel],
    [S.migrationPlugins, (result.pluginNames || []).join(tr("、", ", "))],
  ]) {
    if (value) dl.append(el("dt", label), el("dd", value));
  }
  panel.append(dl);
  if(result.validation?.recycle_token){
    panel.append(el('p',tr('回收确认前不要关闭或重启当前 ComfyUI，也不要开始新的搬家或创建任务。请暂停来源实例的工作流，用不同端口启动目标 ComfyUI（例如 --port 8189），确认所选插件可用后回到此处确认回收。回收完成后再重启来源实例。若后台已重启，旧确认已失效，原插件不会自动删除。','Keep this ComfyUI backend running and do not start another migration or creation task before confirming recycling. Pause source workflows, launch the target on another port (for example --port 8189), verify the selected plugins and return here to confirm. Restart the source only after recycling. If the backend has restarted, the old confirmation is invalid and originals are not automatically deleted.')));
    const recycle=button(tr('确认可用，回收原插件','Confirm working; recycle originals'),async()=>{
      if(!window.confirm(tr('我已启动目标实例并确认所选插件可用。现在将原插件移入回收站？','I have launched the target and verified the plugins. Recycle originals now?')))return;
      recycle.disabled=true;
      try{
        const response=await nodeopsFetch('/qbb_nodeops/migration/confirm-recycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true,token:result.validation.recycle_token})});
        const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);
        syncMigrationResult(data);detail.textContent=tr('搬家完成，原插件已移入回收站。现在可以重启来源实例。','Migration complete; originals recycled. You can now restart the source instance.');recycle.remove();
      }catch(error){detail.textContent=error.message;recycle.disabled=false;}
    });
    panel.append(recycle);
  }
  const issues = [...(result.validation?.issues || []), ...(result.validation?.unknown || [])];
  if (issues.length) {
    const list = el("div", "", "qbb-lh-migration-progress" + (result.status === "needs_attention" ? " error" : ""));
    list.append(el("strong", tr("需要核查", "Needs review")));
    for (const item of issues) list.append(el("p", item));
    panel.append(list);
  }
  if (result.logs?.length) {
    const log = el("pre", result.logs.join("\n"), "qbb-lh-migration-log");
    panel.append(log);
  }
  body.append(panel);

  if (!done) return;
  right.append(
    button(tr("完成", "Done"), () => {
      state.migrationDismissedJob = state.migrationResult?.jobKey;
      state.migrationResult = null;
      pluginQuery = "";
      resetMigrationDraft();
      loadMigrationData(true);
    }, "qbb-lh-btn qbb-lh-btn-primary"),
  );
}

async function chooseFolder(input, assign) {
  const response = await nodeopsFetch("/qbb_nodeops/instances/choose-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  if (data.path) {
    input.value = data.path;
    assign(data.path);
  }
}

function instanceCard(instance, { selected, disabled, onClick, badge }) {
  const card = el("button", "", "qbb-lh-instance-card" + (selected ? " selected" : ""));
  card.type = "button";
  card.setAttribute("aria-pressed", String(!!selected));
  card.disabled = !!disabled || instance.available === false;
  const name = el("div", instance.name + (badge ? ` · ${badge}` : ""), "qbb-lh-instance-name");
  const meta = el("div", instance.available === false
    ? (isChineseLocale() ? "暂时离线 · 登记已保留" : "Offline · registration retained")
    : `${instance.plugin_count || 0} plugins · ${instance.python_kind || "Python"}`, "qbb-lh-instance-meta");
  meta.title = [instance.comfyui_root, instance.unavailable_reason].filter(Boolean).join('\n');
  card.append(name, meta);
  card.onclick = onClick;
  return card;
}

export function renderMigration(body, left, right, { onVersions } = {}) {
  const S = t();
  const tr = (zh, en) => (isChineseLocale() ? zh : en);
  const scan = state.cachedMigration || { plugins: [], status: { status: "idle" } };
  const status = scan.status || { status: "idle" };
  const running = status.status === "running";
  body.replaceChildren();
  left.replaceChildren();
  right.replaceChildren();
  if (state.migrationConnectionError && !state.migrationResult) {
    body.append(el('p', state.migrationConnectionError));
    return;
  }

  if (state.migrationResult) {
    syncMigrationResult(status);
    renderResult(body, right, { onVersions });
    return;
  }

  const heading = el("div", "", "qbb-lh-instance-heading");
  heading.append(el("strong", S.tabMigration || S.migrationTitle));
  const tools = el("div", "", "qbb-row");
  if (state.migrationScanError) body.append(el('p', state.migrationScanError));
  tools.append(
    button(S.migrationReload, () => {
      if (!running) loadMigrationData(true);
    }),
  );
  if (onVersions) {
    const versions = button(tr("版本记录", "Version records"), onVersions);
    tools.append(versions);
  }
  heading.append(tools);
  body.append(heading);

  const step = clampStep();
  const source = currentSource();
  const steps = el("div", "", "qbb-wizard-steps");
  const labels = [
    tr("1 · 来源", "1 · Source"),
    tr("2 · 插件", "2 · Plugins"),
    tr("3 · 目标", "3 · Target"),
    tr("4 · 确认", "4 · Review"),
  ];
  labels.forEach((label, index) => {
    const item = button(label, () => {
      if (index + 1 < step) {
        state.migrationStep = index + 1;
        draw();
      }
    });
    item.disabled = index + 1 > step;
    item.setAttribute("aria-current", index + 1 === step ? "step" : "false");
    steps.append(item);
  });
  body.append(steps);

  const panel = el("section", "", "qbb-lh-migration-step");
  const error = el("p", "", "qbb-danger");
  error.setAttribute("role", "alert");
  body.append(panel, error);
  if (status.status === "error" && status.error) error.textContent = status.error;
  let nextBtn = null;

  function draw() {
    renderMigration(body, left, right, { onVersions });
  }

  function go(next) {
    state.migrationStep = next;
    draw();
  }

  function selectSource(instance) {
    if (running || !instance) return;
    state.addInstanceFormOpen = false;
    if (source?.id === instance.id) {
      draw();
      return;
    }
    state.migrationSourceId = instance.id;
    if (state.migrationTargetId === instance.id) state.migrationTargetId = "";
    state.migrationMode = "";
    state.migrationSelection.clear();
    state.cachedMigration = null;
    state.migrationStep = 1;
    loadMigrationData(true);
  }

  if (step === 1) {
    panel.append(el("h3", tr("从哪个 ComfyUI 搬走？", "Which ComfyUI should plugins come from?")));
    panel.append(el("p", S.instancesHint, "qbb-page-subtitle"));
    const cards = el("div", "", "qbb-lh-instance-cards");
    for (const instance of state.cachedInstances || []) {
      cards.appendChild(
        instanceCard(instance, {
          selected: !state.addInstanceFormOpen && source?.id === instance.id,
          disabled: running,
          badge: instance.current ? S.instanceCurrent : "",
          onClick: () => selectSource(instance),
        }),
      );
    }
    const add = el("button", "", "qbb-lh-instance-card add" + (state.addInstanceFormOpen ? " selected" : ""));
    add.type = "button";
    add.setAttribute("aria-pressed", String(!!state.addInstanceFormOpen));
    add.disabled = running;
    add.append(el("strong", "＋"), el("span", S.instanceImportExisting));
    add.onclick = () => {
      state.addInstanceFormOpen = !state.addInstanceFormOpen;
      draw();
    };
    cards.append(add);
    panel.append(cards);
    if (state.addInstanceFormOpen) {
      const pathField = el("div", "", "qbb-field");
      const pathLabel = el("label", S.instancePath);
      const path = el("input");
      path.type = "text";
      path.id = "qbb-import-path";
      pathLabel.htmlFor = path.id;
      path.placeholder = "E:\\ComfyUI\\ComfyUI";
      path.value = state.addInstanceDraft.path;
      path.oninput = () => {
        state.addInstanceDraft.path = path.value;
      };
      const browse = button(tr("浏览文件夹", "Browse folders"), async () => {
        try {
          error.textContent = "";
          await chooseFolder(path, (value) => {
            state.addInstanceDraft.path = value;
          });
        } catch (err) {
          error.textContent = tr("无法选择文件夹，可手动填写地址：", "Unable to select a folder; enter the path manually: ") + (err.message || err);
        }
      });
      const pathRow = el("div", "", "qbb-field-control");
      pathRow.append(path, browse);
      pathField.append(pathLabel, pathRow);
      const nameField = el("div", "", "qbb-field");
      const nameLabel = el("label", S.instanceName);
      const name = el("input");
      name.type = "text";
      name.id = "qbb-import-name";
      nameLabel.htmlFor = name.id;
      name.placeholder = S.instanceName;
      name.value = state.addInstanceDraft.name;
      name.oninput = () => {
        state.addInstanceDraft.name = name.value;
      };
      nameField.append(nameLabel, name);
      const submit = button(S.instanceImportExisting, async () => {
        try {
          error.textContent = "";
          if (!state.addInstanceDraft.path.trim()) {
            error.textContent = S.migrationNeedTarget;
            return;
          }
          const added = await addInstanceFromUI(state.addInstanceDraft.path.trim(), state.addInstanceDraft.name.trim());
          state.addInstanceDraft = { mode: "import", path: "", name: "" };
          state.addInstanceFormOpen = false;
          state.migrationSourceId = added.id;
          state.migrationTargetId = "";
          state.migrationMode = "";
          state.migrationSelection.clear();
          state.cachedMigration = null;
          await loadMigrationData(true);
        } catch (err) {
          error.textContent = err.message || err;
        }
      }, "qbb-lh-btn qbb-lh-btn-primary");
      panel.append(pathField, nameField, submit, el("p", S.instanceRuntimeNote, "qbb-hint"));
    }
  } else if (step === 2) {
    const plugins = scan.plugins || [];
    const head = el("div", "", "qbb-lh-instance-heading");
    head.append(el("strong", S.migrationPlugins + " · " + S.migrationSelected(state.migrationSelection.size)));
    const selectTools = el("div", "", "qbb-row");
    selectTools.append(
      button(S.migrationSelectAll, () => {
        state.migrationSelection = new Set(plugins.map((item) => item.path));
        draw();
      }),
      button(S.migrationClearAll, () => {
        state.migrationSelection.clear();
        draw();
      }),
    );
    head.append(selectTools);
    panel.append(head);
    const search = el("input");
    search.type = "search";
    search.className = "qbb-stretch";
    search.placeholder = tr("搜索插件", "Search plugins");
    search.value = pluginQuery;
    search.setAttribute("aria-label", search.placeholder);
    search.oninput = () => {
      pluginQuery = search.value;
      fillList();
    };
    panel.append(search);
    const list = el("div", "", "qbb-lh-migration-list");
    panel.append(list);
    function fillList() {
      const q = pluginQuery.trim().toLowerCase();
      list.replaceChildren();
      const visible = plugins.filter((plugin) => !q || plugin.name.toLowerCase().includes(q));
      for (const plugin of visible) {
        const option = el("label", "", "qbb-lh-migration-item");
        const box = el("input");
        box.type = "checkbox";
        box.checked = state.migrationSelection.has(plugin.path);
        box.onchange = () => {
          if (box.checked) state.migrationSelection.add(plugin.path);
          else state.migrationSelection.delete(plugin.path);
          head.firstChild.textContent = S.migrationPlugins + " · " + S.migrationSelected(state.migrationSelection.size);
          if (nextBtn) nextBtn.disabled = !state.migrationSelection.size;
        };
        const name = el("span", plugin.name, "qbb-lh-migration-name");
        name.title = plugin.path;
        option.append(box, name, el("span", plugin.git ? S.migrationGit : S.migrationLocal, "qbb-lh-migration-kind" + (plugin.git ? "" : " local")));
        list.append(option);
      }
      if (!visible.length) list.append(el("p", tr("没有符合条件的插件", "No matching plugins"), "qbb-runtime-status"));
    }
    fillList();
  } else if (step === 3) {
    if (!["existing", "instance"].includes(state.migrationMode)) {
      state.migrationMode = "existing";
      state.migrationDestination = "";
      state.migrationEnvironment = null;
    }
    const existingMode = ["existing", "instance"].includes(state.migrationMode);
    panel.append(el("h3", tr("搬到哪里？", "Where should they go?")));
    panel.append(
      el(
        "p",
        state.migrationMode === "existing" || state.migrationMode === "instance" ? S.migrationExistingHint : S.migrationIntro + " " + S.migrationModelNote,
        "qbb-page-subtitle",
      ),
    );
    function selectMode(mode, targetId = "") {
      state.migrationDeleteSource = false;
      state.migrationMode = mode;
      state.migrationTargetId = targetId;
      if (mode === "instance") {
        state.migrationDestination = "";
      } else if (mode === "existing" && !state.migrationDestination) {
        state.migrationDestination = "";
      }
      draw();
    }
    if (existingMode) {
    const targets = el("section", "", "qbb-migration-targets");
    targets.append(el("h4", tr("选择目标实例", "Choose a target instance")));
    const targetCards = el("div", "", "qbb-lh-instance-cards");
    for (const instance of (state.cachedInstances || []).filter((item) => item.id !== source?.id)) {
      targetCards.appendChild(
        instanceCard(instance, {
          selected: state.migrationMode === "instance" && state.migrationTargetId === instance.id,
          badge: S.instanceTarget,
          onClick: () => { state.migrationManualTarget = false; selectMode("instance", instance.id); },
        }),
      );
    }
    const manualSelected = state.migrationMode === "existing" && state.migrationManualTarget;
    const manualCard = el("button", tr("＋ 选择其他目录", "＋ Choose another folder"), "qbb-lh-instance-card" + (manualSelected ? " selected" : ""));
    manualCard.type = "button";
    manualCard.setAttribute("aria-pressed", String(!!manualSelected));
    manualCard.onclick = () => { state.migrationManualTarget = true; selectMode("existing"); };
    targetCards.append(manualCard);
    targets.append(targetCards);
    panel.append(targets);
    }
    if (state.migrationMode === "existing" && state.migrationManualTarget) {
      const field = el("div", "", "qbb-field");
      const caption = el("label", S.migrationExistingTarget);
      const input = el("input");
      input.type = "text";
      input.id = "qbb-migration-target";
      caption.htmlFor = input.id;
      input.value = state.migrationDestination;
      input.placeholder = "E:\\Other_ComfyUI\\ComfyUI";
      input.oninput = () => {
        state.migrationDestination = input.value;
        if (nextBtn) nextBtn.disabled = !targetReady();
      };
      const row = el("div", "", "qbb-field-control");
      const browse = button(tr("浏览文件夹", "Browse folders"), async () => {
        try {
          error.textContent = "";
          await chooseFolder(input, (value) => {
            state.migrationDestination = value;
            if (nextBtn) nextBtn.disabled = !targetReady();
          });
        } catch (err) {
          error.textContent = tr("无法选择文件夹，可手动填写地址：", "Unable to select a folder; enter the path manually: ") + (err.message || err);
        }
      });
      row.append(input, browse);
      field.append(caption, row, el("p", S.migrationExistingTargetHint, "qbb-hint"));
      panel.append(field);
    }
  } else {
    const plugins = (scan.plugins || []).filter((plugin) => state.migrationSelection.has(plugin.path));
    panel.append(el("h3", tr("确认搬家内容", "Review this move")));
    const dl = el("dl", "", "qbb-confirm-dl");
    const rows = [
      [S.instanceSource, source ? `${source.name} · ${source.comfyui_root}` : ""],
      [S.instanceTarget, targetLabel(scan, S)],
      [S.migrationPlugins, plugins.map((plugin) => plugin.name).join(tr("、", ", ")) || S.migrationSelected(0)],
    ];
    rows.push([tr("说明", "Note"), S.migrationExistingHint]);
    rows.push([tr("来源插件", "Source plugins"), tr("复制后保留，不会删除。", "Copied and retained; nothing is deleted.")]);
    for (const [label, value] of rows) dl.append(el("dt", label), el("dd", value));
    panel.append(dl);
  }

  if (step > 1) {
    left.append(
      button(tr("上一步", "Back"), () => {
        state.addInstanceFormOpen = false;
        go(step - 1);
      }),
    );
  }
  nextBtn = button(
    step === 4 ? S.migrationStart : tr("下一步", "Next"),
    async () => {
      error.textContent = "";
      if (step === 1 && !source) {
        error.textContent = tr("请选择来源实例。", "Choose a source instance.");
        return;
      }
      if (step === 2 && !state.migrationSelection.size) {
        error.textContent = S.migrationNeedPlugins;
        return;
      }
      if (step === 3 && !targetReady()) {
        error.textContent = S.migrationNeedTarget;
        return;
      }
      if (step < 4) {
        state.addInstanceFormOpen = false;
        go(step + 1);
        return;
      }
      nextBtn.disabled = true;
      try {
        await startMigrationFromUI(state.migrationDestination.trim(), state.migrationMode || "existing", false);
      } finally {
        if (nextBtn.isConnected) nextBtn.disabled = false;
      }
    },
    "qbb-lh-btn qbb-lh-btn-primary",
  );
  if (step === 1) nextBtn.disabled = !source;
  if (step === 2) nextBtn.disabled = !state.migrationSelection.size;
  if (step === 3) nextBtn.disabled = !targetReady();
  right.append(nextBtn);
}
