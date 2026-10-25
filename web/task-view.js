import {api} from '../../scripts/api.js';
import './http.js';

export const taskElement=(tag,text='',cls='')=>{const node=document.createElement(tag);node.textContent=text;node.className=cls;return node;};
export const taskButton=(text,handler)=>{const node=taskElement('button',text,'qbb-lh-btn');node.type='button';node.onclick=handler;return node;};
export async function taskRequest(path,payload){
 const response=await api.fetchApi('/qbb_nodeops'+path,{cache:'no-store',timeoutMs:payload===undefined?15000:600000,signal:AbortSignal.timeout(payload===undefined?15000:600000),...(payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})});
 const value=await response.json();if(!response.ok){const error=Error(value.error||response.statusText);error.status=response.status;throw error;}return value;
}
export function rememberTask(kind,id){try{if(id)localStorage.setItem('qbb-'+kind+'-task',id);else localStorage.removeItem('qbb-'+kind+'-task');}catch{/* Server-side task status remains available. */}}
export function rememberedTask(kind){try{return localStorage.getItem('qbb-'+kind+'-task');}catch{return null;}}
const phaseNames={awaiting_manual_restart:['离线恢复已完成，请手动启动 ComfyUI','Offline restoration finished; start ComfyUI manually'],verify_frozen_payload:['核验已准备的恢复材料','Verifying prepared restore materials'],verify_handoff_environment:['核验准备后环境是否发生变化（一次扫描）','Checking environment changes since preparation (one scan)'],verify_handoff_packages:['核验待操作包文件','Verifying affected package files'],queued:['等待开始','Queued'],scan_core:['扫描 ComfyUI','Scanning ComfyUI'],scan_plugins:['扫描插件','Scanning plugins'],scan_packages:['扫描 Python 包状态','Scanning package state'],scan_installed_files:['扫描已安装包文件','Scanning installed package files'],save_zip_archives:['打包本地 ZIP 归档','Packing local ZIP archives'],scan_configuration:['扫描白名单配置','Scanning allowlisted configuration'],check_storage:['校验共享对象与空间','Checking objects and space'],save_objects:['保存本地资源','Saving local resources'],verify_zip_archives:['逐项核验 ZIP 归档','Verifying ZIP archive contents'],verify_objects:['核验保存内容','Verifying saved content'],verify_environment:['重新扫描并核验环境','Rescanning and verifying environment'],publish:['发布版本记录','Publishing record'],preparing:['准备恢复','Preparing restore'],prepare_resources:['准备并校验恢复材料','Preparing and verifying resources'],prepare_zip_contents:['校验并解包本地 ZIP','Verifying and unpacking local ZIP'],prepare_installed_rollback:['保存包文件回滚材料','Preparing installed-file rollback'],verify_preparation:['冻结前复查','Checking before freezing'],prepared:['材料已就绪，等待停机确认','Prepared; awaiting stop confirmation'],starting:['核验已准备材料','Checking prepared materials'],waiting_for_exit:['请手动关闭原 ComfyUI；恢复完成后再手动启动','Close the original ComfyUI manually; restart only after restore completion'],applying:['离线修改环境','Applying offline changes'],apply_core:['恢复 ComfyUI','Restoring ComfyUI'],apply_plugin:['恢复插件','Restoring plugins'],apply_configuration:['恢复配置','Restoring configuration'],apply_package:['调整 Python 包','Updating packages'],apply_finished:['离线操作完成，尚待验证','Applied; not yet verified'],rolling_back:['执行失败，正在回退','Rolling back failed execution'],rollback_packages:['回退包变更','Rolling back packages'],rollback_files:['回退文件变更','Rolling back files'],restarting:['重启原实例','Restarting original instance'],awaiting_verification:['等待服务重连与最终验证','Awaiting service and verification'],verifying:['最终验证','Final verification'],verify_artifacts:['核验目标对象','Verifying target artifacts'],cleaning:['清理执行暂存','Cleaning transaction staging']};
export function renderTaskProgress(host,task,tr,compact=false){
 const progress=task.progress||task;
 const phase=progress.phase||task.status;
 const box=taskElement('section','','qv-task-progress');box.setAttribute('aria-label',tr('任务进度','Task progress'));
 const title=taskElement('p',phaseNames[phase]?tr(...phaseNames[phase]):phase,'qv-task-stage');title.setAttribute('role','status');box.append(title);
 const meter=taskElement('progress');meter.setAttribute('aria-label',tr('当前阶段进度','Current stage progress'));
 if(Number.isFinite(progress.done)&&Number.isFinite(progress.total)&&progress.total>0){meter.max=progress.total;meter.value=Math.min(progress.done,progress.total);box.append(meter,taskElement('p',`${progress.done} / ${progress.total} `+tr('项（当前阶段，非整项恢复完成比例）','items (this stage, not overall restoration)'),'qv-muted'));}
 else if(compact){box.append(meter);}else box.append(taskElement('p',tr('本阶段总量尚不可计量；不显示估算百分比。','This stage has no measurable total; no estimated percentage.'),'qv-muted'));
 if(progress.current)box.append(taskElement('p',tr('当前：','Current: ')+progress.current,'qv-task-object'));
 if(!compact)box.append(taskElement('p',tr('总体流程：扫描 / 准备 → 保存 / 执行 → 最终校验 → 完成','Overall: scan / prepare → save / apply → verify → finish'),'qv-muted'));
 host.append(box);
}
export function renderCaptureTask(host,task,tr){
 host.replaceChildren();host.classList.add('qv-task');
 const success=task.status==='completed',failure=['failed','interrupted'].includes(task.status);
 host.append(taskElement('h4',success?(task.action==='preview'?tr('预览已就绪','Preview ready'):tr('版本记录完成','Record created')):failure?(task.status==='failed'?tr('版本记录失败','Record failed'):tr('版本记录未确认完成','Record not confirmed complete')):task.action==='create'?tr('保存记录','Saving record'):tr('扫描环境','Scanning environment')));
 if(!success&&!failure)renderTaskProgress(host,task,tr,true);
 if(success&&task.action==='create'){
  const result=task.result,stats=result.statistics;
  const rows=[[tr('记录插件','Plugins'),stats.plugins],[tr('Python 包','Packages'),stats.packages],[tr('保存本地资源','Saved resources'),stats.saved_resources],[tr('复用资源','Reused resources'),stats.reused_resources],[tr('新增存储','New storage'),(stats.new_bytes/1048576).toFixed(1)+' MiB'],[tr('复用对象','Reused objects'),(stats.reused_bytes/1048576).toFixed(1)+' MiB']];
  const metrics=taskElement('div','','qv-metrics');for(const [label,value] of rows)metrics.append(taskElement('p',label+' · '+value,'qv-metric'));host.append(metrics);
  if(result.backup_summary)renderBackupSummary(host,result.backup_summary,tr);
  const gaps=result.backup_summary?result.backup_summary.components.filter(row=>['incomplete','metadata_only','source_only'].includes(row.status)):result.unrestorable||[],issues=result.issues||[];
  if(gaps.length||issues.length){host.append(taskElement('p',tr('该 Snapshot 存在无法保证恢复的资源。','This snapshot contains resources not guaranteed restorable.'),'qv-warning'));const details=taskElement('details');details.append(taskElement('summary',`${gaps.length} `+tr('项资源缺口；','resource gaps; ')+`${issues.length} `+tr('项扫描问题','scan issues')));if(!result.backup_summary)for(const row of gaps)details.append(taskElement('p',row.name+' — '+row.recoverability.reason));for(const issue of issues)details.append(taskElement('p',JSON.stringify(issue)));host.append(details);}
  host.append(taskElement('p',tr('记录已保存不代表整个 Python 环境可字节级恢复。','Saving a record does not guarantee byte-for-byte Python recovery.'),'qv-muted'));
 }
 if(failure)host.append(taskElement('p',(task.error==='Preview again before saving'?tr('预览已过期或失效，请重新预览后确认保存。','Preview expired or invalid. Preview again, then confirm saving.'):task.error)||tr('请检查历史记录与任务详情。','Check history and task details.'),'qv-warning'));
 if(task.id){const details=taskElement('details');details.append(taskElement('summary',tr('任务详情','Task details')),taskElement('p',tr('任务编号：','Task ID: ')+task.id));if(task.phase)details.append(taskElement('p',tr('最后阶段：','Last phase: ')+task.phase));host.append(details);}
}
export function renderBackupSummary(host,summary,tr){
 const statuses={local_saved:tr('完整本地备份','Complete local backup'),incomplete:tr('有缺口','Has gaps'),metadata_only:tr('仅元数据','Metadata only'),source_only:tr('仅来源记录','Source only'),protected:tr('受保护','Protected'),outside_scope:tr('范围外','Outside scope'),absent:tr('原本不存在','Originally absent')};
 host.append(taskElement('p',tr('ZIP 内容核验通过：','ZIP archives verified: ')+summary.verified_archives+tr(' 份。完整性与恢复能力分别判断。',' archives. Backup completeness and recoverability are separate.')));
 const details=taskElement('details'),counts=summary.counts;
 details.append(taskElement('summary',tr('本地备份明细 · 完整 ','Backup inventory · Complete ')+counts.local_saved+tr(' · 有缺口 ',' · With gaps ')+counts.incomplete+tr(' · 仅元数据 ',' · Metadata only ')+counts.metadata_only));
 const toolbar=taskElement('div','','qv-toolbar'),search=taskElement('input'),filter=taskElement('select');search.type='search';search.value='';search.placeholder=tr('搜索备份组件','Search backup components');search.setAttribute('aria-label',search.placeholder);filter.setAttribute('aria-label',tr('筛选备份状态','Filter backup status'));
 const any=taskElement('option',tr('全部状态','All statuses'));any.value='all';filter.append(any);for(const [value,label] of Object.entries(statuses)){const option=taskElement('option',label);option.value=value;filter.append(option);}filter.value='all';toolbar.append(search,filter);details.append(toolbar);
 const list=taskElement('div','','qv-plan-list'),pager=taskElement('div','','qv-plan-pager'),position=taskElement('span');let page=0;const pageSize=12;
 const previous=taskButton(tr('上一页','Previous'),()=>{page--;draw();}),next=taskButton(tr('下一页','Next'),()=>{page++;draw();});pager.append(position,previous,next);details.append(list,pager);host.append(details);
 function draw(){
  const rows=summary.components.filter(row=>(filter.value==='all'||row.status===filter.value)&&(row.name+' '+row.id).toLowerCase().includes(search.value.toLowerCase()));
  const pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(page,pages-1);list.replaceChildren();
  for(const row of rows.slice(page*pageSize,(page+1)*pageSize)){
   const item=taskElement('details','','qv-plan-row'),title=taskElement('summary');title.append(taskElement('span',row.name),taskElement('span',statuses[row.status]||row.status,'qv-plan-state'));item.append(title);
   const body=taskElement('div','','qv-plan-row-body');body.append(taskElement('p',row.files+tr(' 个文件 · ',' files · ')+(row.archive_verified?tr('ZIP 内容已核验','ZIP contents verified'):row.has_archive?tr('ZIP 尚未记录内容核验','ZIP verification not recorded'):tr('未生成 ZIP','No ZIP saved'))));
   for(const gap of row.gaps||[])body.append(taskElement('p',(gap.path||'—')+' · '+gap.reason));item.append(body);list.append(item);
  }
  if(!rows.length)list.append(taskElement('p',tr('没有匹配的备份组件','No matching backup components')));
  position.textContent=rows.length?`${page*pageSize+1}–${Math.min((page+1)*pageSize,rows.length)} / ${rows.length}`:'0 / 0';previous.disabled=page===0;next.disabled=page===pages-1;pager.hidden=rows.length<=pageSize;
 }
 search.oninput=filter.onchange=()=>{page=0;draw();};draw();
}
export async function watchCaptureTask(host,id,tr,onState=()=>{}){
 let misses=0;
 while(host.isConnected){
  try{const task=await taskRequest('/record-tasks/'+encodeURIComponent(id));misses=0;renderCaptureTask(host,task,tr);onState(task);if(!['queued','running'].includes(task.status))return task;}
  catch(error){host.replaceChildren(taskElement('h4',tr('无法读取创建任务状态','Cannot read capture status')),taskElement('p',tr('尚未确认成功，不会自动重发保存请求。','Success is unconfirmed. The save request will not be resubmitted.'),'qv-warning'),taskElement('p',error.message));if(error.status===404&&++misses>=3)throw error;}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }
 return null;
}
export async function runCaptureTask(host,action,payload,tr,onState){
 const id=crypto.randomUUID().replaceAll('-','');rememberTask('capture',id);
 renderCaptureTask(host,{id,action,status:'queued',phase:'queued'},tr);
 try{await taskRequest('/record-tasks/'+action,{request_id:id,payload});}
 catch(error){if(error.status){rememberTask('capture',null);throw error;}}
 return watchCaptureTask(host,id,tr,onState);
}
