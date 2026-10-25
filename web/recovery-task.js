export function renderRecoveryTask(host,task,tr,onResume,onVerify){
 const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
 host.replaceChildren();
 const phaseNames={python:tr('准备 Python','Prepare Python'),dependencies:tr('安装并检查依赖','Install and check dependencies'),files_verified:tr('复核落盘文件与权限','Verify restored files and permissions'),launcher:tr('生成启动器','Create launcher')};
 const phaseStates={pending:tr('等待重新核验','Pending recheck'),running:tr('进行中','Running'),completed:tr('已完成','Completed'),failed:tr('失败','Failed'),interrupted:tr('已中断','Interrupted')};
 if(task.phases){const details=el('details');details.append(el('summary',tr('恢复阶段与检查点','Recovery stages and checkpoints')));for(const [key,value] of Object.entries(task.phases)){const item=(task.items||[]).find(i=>key==='component:'+i.id);details.append(el('p',(phaseNames[key]||item?.name||key)+' · '+(phaseStates[value.status]||value.status)+' · '+tr('尝试 ','Attempt ')+value.attempt+(value.error?' · '+value.error:'')));}host.append(details);}
 for(const note of task.path_notes||[])host.append(el('p',note.kind==='relocated'?tr('模型路径已适配：','Model path adapted: ')+note.field+' · '+note.from+' → '+note.to:tr('请核对外部模型路径：','Check external model path: ')+note.field+' · '+(note.base_path||'')+' · '+note.path));
 const report=task.verification||{status:'not_checked'};
 const names={files:tr('恢复文件','Recovered files'),dependencies:tr('Python 与依赖','Python and dependencies'),torch:'PyTorch',startup:tr('启动初始化','Startup initialization'),plugins:tr('插件功能','Plugin functions')};
 Object.assign(names,{http:tr('HTTP 服务','HTTP service'),nodes:tr('指定节点注册','Expected node registration'),workflow:tr('指定工作流执行','Selected workflow execution'),cleanup:tr('清理验证进程','Verification process cleanup')});
 const states={passed:tr('通过','Passed'),failed:tr('未通过','Failed'),not_checked:tr('未验证','Not checked'),running:tr('验证中','Verifying'),interrupted:tr('验证中断','Verification interrupted')};
 if(task.integrity){
  const summary=el('section');summary.className='qv-integrity';summary.append(el('h4',tr('恢复完整性（已恢复范围）','Recovery integrity (restored scope)')));
  for(const [key,label] of [['files',tr('文件、目录与权限','Files, directories and permissions')],['dependencies',tr('依赖安装与检查','Dependency installation and checks')],['startup',tr('启动初始化','Startup initialization')],['workflow',tr('指定工作流','Selected workflow')]])summary.append(el('p',label+' · '+(states[task.integrity[key]]||tr('未验证','Not checked'))));
  if(task.integrity.has_gaps)summary.append(el('p',tr('此任务有缺项或替代内容，检查通过不代表完整环境已恢复。','This task has gaps or substitutions; passing checks does not mean the entire environment was recovered.')));
  host.append(summary);
 }
 host.append(el('h4',tr('已执行检查的结果：','Results of performed checks: ')+(states[report.status]||report.status)));
 for(const item of report.checks||[])host.append(el('p',(names[item.name]||item.name)+' · '+(states[item.status]||item.status)+' · '+item.detail));
 if(report.error)host.append(el('p',report.error));
 host.append(el('p',tr('恢复完成、初始化通过与工作流可运行是不同结论。跳过或替代内容不会因验证通过变成完整恢复。','Recovery, startup initialization and workflow correctness are separate outcomes. Verification does not remove skipped or substituted content.')));
 if(task.can_verify&&onVerify){
  const confirm=el('input');confirm.type='checkbox';const startup=el('input');startup.type='checkbox';const plugins=el('input');plugins.type='checkbox';plugins.disabled=true;
  const label=(input,text)=>{const n=el('label');n.append(input,document.createTextNode(text));return n;};
  const button=el('button',tr('执行验证','Run verification'));button.className='qbb-lh-btn';button.disabled=true;
  confirm.addEventListener('change',()=>{button.disabled=!confirm.checked;});startup.addEventListener('change',()=>{plugins.disabled=!startup.checked;if(!startup.checked)plugins.checked=false;});
  const service=el('input');service.type='checkbox';service.disabled=true;
  const expected=el('input');expected.type='text';expected.placeholder=tr('可选：节点 class_type，逗号分隔','Optional node class_types, comma separated');expected.disabled=true;
  const workflow=el('textarea');workflow.placeholder=tr('可选：粘贴 API 格式工作流 JSON（不是画布 JSON）','Optional API prompt JSON (not canvas JSON)');workflow.disabled=true;
  const execute=el('input');execute.type='checkbox';execute.disabled=true;
  const error=el('p');error.setAttribute?.('role','alert');
  const sync=()=>{service.disabled=!startup.checked;if(!startup.checked)service.checked=false;expected.disabled=workflow.disabled=execute.disabled=!service.checked;if(!service.checked)execute.checked=false;};
  startup.addEventListener('change',()=>{plugins.disabled=!startup.checked;if(!startup.checked)plugins.checked=false;sync();});service.addEventListener('change',sync);
  button.onclick=async()=>{if(!confirm.checked)return;error.textContent='';let graph=null;try{if(service.checked&&workflow.value?.trim()){if(!execute.checked)throw Error(tr('请先确认执行工作流','Confirm workflow execution first'));graph=JSON.parse(workflow.value);}}catch(e){error.textContent=e.message;return;}button.disabled=true;try{await onVerify(task.id,{startup:!!startup.checked,plugins:!!plugins.checked,service:!!service.checked,expected_nodes:service.checked?(expected.value||'').split(/[,，\n]/).map(s=>s.trim()).filter(Boolean):[],workflow:graph,workflow_confirmed:!!execute.checked});}finally{button.disabled=false;}};
  host.append(label(confirm,tr('允许运行目标 Python 和 PyTorch 检查（可能占用少量 GPU 资源）。','Allow target Python/PyTorch probes (may use some GPU resources).')),label(startup,tr('同时进行 ComfyUI 启动初始化测试，最长 3 分钟后退出。','Also test ComfyUI startup initialization, up to 3 minutes.')),label(plugins,tr('加载第三方插件：其代码可能联网、写文件或启动其他进程，验证不是沙箱。','Load third-party plugins: their code may access networks, write files or spawn processes; this is not a sandbox.')),button);
  host.append(label(service,tr('启动临时本机 HTTP 服务并检查节点；启动最多 3 分钟，结束后关闭。','Start a temporary local HTTP service and check nodes; allow up to 3 minutes to start, then stop after checks.')),expected,workflow,label(execute,tr('执行上述可信工作流：可能占用大量显存、联网或写文件，最长 10 分钟；常规输出保存在验证目录，第三方节点可能写入其他位置。','Execute this trusted workflow: may use substantial GPU memory, networks or files; up to 10 minutes. Standard output goes to the verification directory; custom nodes may write elsewhere.')),error);
 }
 if(task.total)host.append(el('p',tr('组件处理进度：','Component progress: ')+(task.processed||0)+' / '+task.total));
 const labels={restored:tr('已恢复','Restored'),substituted:tr('已替代恢复','Substituted'),failed:tr('失败，可重试','Failed; retry available'),skipped:tr('按计划跳过','Skipped as planned')};
 for(const item of task.items||[])host.append(el('p',item.name+' · '+(labels[item.status]||item.status)+(item.reused?tr('（检查点校验通过）',' (checkpoint verified)'):'')+(item.error?' · '+item.error:'')));
 for(const item of task.items||[])if(item.checkpoint_binding==='legacy_unbound')host.append(el('p',item.name+tr('：旧检查点仅核对已恢复内容，未记录原计划绑定。',' : legacy checkpoint verifies restored content but has no original-plan binding.')));
 if(task.dependency_items?.length){const details=el('details');details.append(el('summary',tr('依赖进度与错误','Dependency progress and errors')));details.open=task.dependency_items.some(i=>i.status==='failed');for(const item of task.dependency_items)details.append(el('p',item.name+' · '+(item.status==='downloaded'?tr('wheel 已缓存','Wheel cached'):(labels[item.status]||item.status))+(item.error?' · '+item.error:'')));host.append(details);}
 if(task.can_resume){
  const confirm=el('input');confirm.type='checkbox';
  const label=el('label');label.append(confirm,document.createTextNode(tr('按原计划继续，保留已完成组件；允许重试联网下载与依赖安装。','Continue the original plan, preserving completed components; allow download and dependency retries.')));
  const button=el('button',tr('继续恢复 / 重试失败项','Continue recovery / retry failed items'));button.className='qbb-lh-btn';button.disabled=true;
  confirm.addEventListener('change',()=>{button.disabled=!confirm.checked;});
  button.onclick=async()=>{if(!confirm.checked)return;button.disabled=confirm.disabled=true;try{await onResume(task.id);}finally{button.disabled=confirm.disabled=false;}};
  host.append(label,button,el('p',tr('仅对本版本创建的任务续跑。请勿移动或修改目标目录；下载从阶段重试，不保证字节级断点续传。','Only checkpointed jobs can resume. Do not move or modify the target. Downloads retry by stage, not guaranteed byte-range continuation.')));
 }
}
