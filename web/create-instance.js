import { nodeopsFetch } from './http.js';
import { loadRuntime, runtimeState, watchRuntime } from './runtime-data.js';
import { trUI as tr } from './shared.js';

// Independent draft: switching to migration must never change this form.
const draft = { path: '', build: 'cpu', python: '', torch: '' };
let creating = false;

export function renderCreateInstance(host, left, right) {
  host.replaceChildren(); left.replaceChildren(); right.replaceChildren();
  const el = (tag, text = '') => { const n = document.createElement(tag); n.textContent = text; return n; };
  const button = (text, action) => { const b = el('button', text); b.type = 'button'; b.className = 'qbb-lh-btn'; b.onclick = action; return b; };
  async function request(url, payload, signal) {
    const r = await nodeopsFetch(url, payload === undefined ? {signal} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload),signal});
    const data = await r.json(); if (!r.ok) throw Error(data.error || r.statusText); return data;
  }
  const root = el('section'); root.className = 'qbb-create-instance'; host.append(root);
  root.append(el('h2', tr('创建新实例', 'Create instance')));
  const help = el('p', tr('从官方仓库下载全新 ComfyUI，安装所选 Python 和 PyTorch，不复制旧实例的插件、模型或配置。', 'Download a fresh official ComfyUI and install the selected Python and PyTorch without copying existing plugins, models or configuration.'));
  help.dataset.pageHelp = ''; root.append(help);
  const notice = el('div', tr('创建期间可以关闭插件页面，任务会在后台继续运行。请保持网络连接，不要关闭或重启 ComfyUI。', 'You can close this panel while creation continues in the background. Keep the network connected and do not close or restart ComfyUI.'));
  notice.className = 'qbb-create-notice'; notice.setAttribute('role', 'note');
  const form = el('section'); form.className = 'qbb-lh-card'; root.append(form);
  const path = el('input'); path.value = draft.path; path.placeholder = tr('选择空目录', 'Choose an empty folder'); path.oninput = () => {draft.path = path.value; sync();};
  const build = el('select'), torch = el('select'), python = el('select');
  const hardware = el('p', tr('正在检测本机显卡和驱动…', 'Checking local GPU and driver…')); form.append(hardware);
  const compatibility = el('div'); compatibility.className = 'qbb-lh-card'; compatibility.setAttribute('aria-live', 'polite');
  const status = el('p'); status.setAttribute('role', 'status');
  const runtimeStatus = el('p'); runtimeStatus.setAttribute('role', 'status');
  let checkingTask = true, busyOtherTask = false;
  const progress = el('progress'); progress.hidden = true;
  const logs = el('pre'); logs.className = 'qbb-create-logs';
  const browse = button(tr('浏览文件夹', 'Browse folders'), async () => {
    try { const data = await request('/qbb_nodeops/instances/choose-folder', {}); if(data.path){path.value = draft.path = data.path; sync();} } catch(e){status.textContent=e.message;}
  });
  for (const [label, input, extra] of [[tr('实例目录','Instance folder'),path,browse],[tr('运行方式 / CUDA','Runtime / CUDA'),build],['PyTorch',torch],['Python',python]]) {
    const row = el('label', label); row.className='qbb-create-field'; row.append(input); if(extra)row.append(extra); form.append(row);
  }
  const start = button(tr('创建实例','Create instance'), async () => {
    if (creating || checkingTask || busyOtherTask || !configuration()) return;
    creating = true; sync();
    try {
      await request('/qbb_nodeops/migration/start', {purpose:'create',mode:'new',destination_root:draft.path.trim(),plugins:[],delete_source:false,environment:configuration()});
      poll();
    } catch(e){creating=false;status.textContent=tr('创建失败：','Creation failed: ')+e.message;sync();}
  });
  start.classList.add('qbb-lh-btn-primary'); right.append(start);
  const reload = button(tr('检测硬件并刷新在线版本','Check hardware and refresh online versions'), () => load(true));
  form.append(reload); root.append(compatibility,runtimeStatus,status,progress,logs,notice);
  let combinations = [], loading = false;
  function configuration(){return combinations.find(r=>r.build===draft.build && r.torch===draft.torch && r.python===draft.python);}
  function sync(){
    start.disabled=checkingTask||busyOtherTask||creating||loading||!draft.path.trim()||!configuration();
    for(const n of [path,browse])n.disabled=creating;
    build.disabled=creating||loading||!build.value;
    torch.disabled=python.disabled=creating||loading||!combinations.length;
    reload.disabled=creating||loading;
    renderCompatibility();
  }
  function buildLabel(value){return value==='cpu'?tr('CPU（不使用显卡加速）','CPU (no GPU acceleration)'):/^cu\d+$/.test(value)?`CUDA ${value.slice(2,-1)}.${value.slice(-1)} (${value})`:value;}
  function renderCompatibility(){
    compatibility.replaceChildren(el('strong',tr('环境配套检查','Environment compatibility')));
    const row=configuration();
    if(!row){compatibility.append(el('div',tr('请选择有配套安装包的运行方式、PyTorch 和 Python。版本读取完成前不能创建。','Choose a runtime, PyTorch and Python with matching packages. Creation is unavailable until versions load.')));return;}
    compatibility.append(el('div',`Python ${row.python} · PyTorch ${row.torch} · ${buildLabel(row.build)}`));
    compatibility.append(el('div',tr('已确认：官方 PyTorch 配套版本，以及当前系统与 Python 对应的安装包。','Checked: official PyTorch package pairing and wheels for this OS and Python.')));
    compatibility.append(el('div',tr('自动配套安装：','Packages installed together: ')+Object.entries(row.pins||{}).map(([name,version])=>`${name} ${version}`).join(' · ')));
    compatibility.append(el('div',tr('下拉选项会联动筛选，不支持的组合不可创建。此检查不等于完整运行验证，最新 ComfyUI 的依赖及第三方插件仍可能不兼容。','Options are filtered together; unsupported combinations cannot be created. Package availability is not a full runtime guarantee: current ComfyUI dependencies and third-party plugins may still be incompatible.')));
    compatibility.append(el('div',row.build==='cpu'?tr('CPU 模式无需 NVIDIA CUDA 驱动，但不使用显卡加速。','CPU mode does not require NVIDIA CUDA drivers and does not use GPU acceleration.'):
      tr('已按本机显卡和驱动筛选 CUDA，安装后还会实测 GPU 运算；验证失败保留文件并提示原因。','CUDA options are filtered for this GPU and driver. Installation also tests GPU execution; failed validation preserves files and reports the cause.')));
    const link=el('a',tr('查看官方 PyTorch 配套版本','Official PyTorch version combinations'));link.href='https://pytorch.org/get-started/previous-versions/';link.target='_blank';link.rel='noopener noreferrer';compatibility.append(link);
  }
  function placeholder(control,text){const option=el('option',text);option.value='';control.replaceChildren(option);control.value='';}
  function options(control, values, preferred){control.replaceChildren(...values.map(value=>{const o=el('option',value);o.value=value;return o;}));control.value=values.includes(preferred)?preferred:(values[0]||'');}
  function pythonOptions(){options(python,[...new Set(combinations.filter(r=>r.torch===draft.torch).map(r=>r.python))],draft.python);draft.python=python.value;sync();}
  torch.onchange=()=>{draft.torch=torch.value;pythonOptions();};
  python.onchange=()=>{draft.python=python.value;sync();};
  build.onchange=()=>{draft.build=build.value;load();};
  function renderRuntime(selectedBuild, state){
    if(!root.isConnected || selectedBuild!==draft.build || creating)return;
    loading=!!state.pending;
    const detected = state.hardware || state.data?.hardware;
    if(detected?.message)hardware.textContent=detected.message;
    else if(!loading)hardware.textContent=tr('未能读取本机显卡检测结果，请重试。','Could not read local GPU detection; please retry.');
    if(state.data){
      if(!state.data.builds.includes(selectedBuild)) { draft.build='cpu'; load(); return; }
      combinations=state.data.combinations;
      options(build,state.data.builds,selectedBuild);
      for(const option of build.options)option.textContent=buildLabel(option.value);
      options(torch,[...new Set(combinations.map(r=>r.torch))],draft.torch);draft.torch=torch.value;
      pythonOptions();
      runtimeStatus.textContent=state.error?tr('在线版本刷新失败，保留上次可用版本（本机显卡检测结果见上方）：','Online version refresh failed; keeping previous versions (local GPU detection is shown above): ')+state.error:
        loading?tr('正在后台更新版本…','Updating versions in background…'):
        combinations.length?'':tr('没有匹配的版本，请切换运行方式。','No matching versions; choose another runtime.');
      if(!combinations.length){placeholder(torch,tr('无可用版本','No versions'));placeholder(python,tr('无匹配版本','No matching versions'));}
    }else{
      combinations=[];
      if(!build.value)placeholder(build,loading?tr('正在读取运行方式…','Loading runtimes…'):tr('运行方式读取失败','Runtime loading failed'));
      placeholder(torch,loading?tr('正在读取 PyTorch 版本…','Loading PyTorch…'):tr('版本读取失败','Version loading failed'));
      placeholder(python,loading?tr('正在读取 Python 版本…','Loading Python…'):tr('版本读取失败','Version loading failed'));
      runtimeStatus.textContent=loading?tr('正在后台读取可用版本…','Loading available versions…'):
        tr('读取失败，请点击刷新重试：','Loading failed. Refresh to retry: ')+state.error;
    }
    sync();
    if (!state.data) build.disabled = true;
  }
  function load(force=false){
    const selectedBuild=draft.build;
    renderRuntime(selectedBuild,runtimeState(selectedBuild));
    return loadRuntime(selectedBuild,force);
  }
  watchRuntime(renderRuntime);
  async function poll(){
    if(!root.isConnected)return;
    try {const task=await request('/qbb_nodeops/migration/status');if(!root.isConnected)return;
      checkingTask=false;
      busyOtherTask=task.mode!=='new'&&task.status==='running';
      if(task.mode!=='new'||task.status==='idle'){
        creating=false;progress.hidden=true;logs.textContent='';
        status.textContent=busyOtherTask?tr('插件搬家任务正在运行，请完成后再创建实例。','A migration is running. Wait before creating an instance.'):'';
        sync();if(busyOtherTask)setTimeout(poll,1500);else load();return;
      }
      creating=task.status==='running';progress.hidden=!creating;
      if(task.destination_root)path.value=draft.path=task.destination_root;
      const environment=task.environment||{};
      for(const [key,control] of [['build',build],['torch',torch],['python',python]]){
        if(environment[key]){draft[key]=environment[key];options(control,[environment[key]],environment[key]);}
      }
      if(creating)runtimeStatus.textContent='';
      status.textContent=creating?tr('正在创建实例','Creating instance')+(task.current?' · '+task.current:''):
        task.status==='success'?tr('创建完成','Creation complete'):
        task.status==='error'?tr('创建失败：','Creation failed: ')+(task.error||tr('请查看下方日志','See logs below')):
        task.status==='copied_unverified'?tr('安装完成，未验证 CUDA 可用性','Installation complete; CUDA usability unverified'):
        task.status==='needs_attention'?tr('安装完成，CUDA 验证失败；文件已保留：','Installation complete; CUDA validation failed; files retained: ')+(task.error||''):'';
      logs.textContent=(task.logs||[]).filter(line=>!line.startsWith('文件复制完成；尚未验证目标 ComfyUI')&&!line.startsWith('请处理上述依赖问题，再启动目标 ComfyUI')).join('\n');sync();if(creating)setTimeout(poll,1500);else load();
    }catch(e){if(!root.isConnected)return;checkingTask=true;status.textContent=tr('无法读取后台任务，正在重试：','Cannot read background task; retrying: ')+e.message;sync();setTimeout(poll,3000);}
  }
  sync();load();poll();
}
