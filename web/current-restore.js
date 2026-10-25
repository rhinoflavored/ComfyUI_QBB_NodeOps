import {taskElement as el,taskButton as button,taskRequest,rememberTask,renderTaskProgress} from './task-view.js';
import {renderStatePlan,requestStatePlan,reviewedMode,clearReviewedComparisons} from './state-plan.js';

const activeStates=new Set(['preparing','starting','waiting_for_exit','applying','rolling_back','restarting','awaiting_verification','awaiting_manual_restart','verifying','cleaning']);
export function verifiedRestore(task){const v=task.verification;if(task.status==='completed'&&task.outcome==='applied'&&v?.status==='actions_applied'&&v.environment_rescanned===false)return true;return task.status==='completed'&&task.outcome==='applied'&&v?.status==='no_changes'&&v.diff?.state==='matched'&&v.diff.change_count===0&&v.diff.unverified_count===0&&v.blockers?.length===0;}
export function renderRestoreResult(host,task,tr){
 host.replaceChildren();const success=verifiedRestore(task);
 host.append(el('h4',success?tr('恢复计划已执行','Restore plan applied'):tr('环境未完全恢复','Environment not fully restored')));
 host.append(el('p',tr('目标版本：','Target: ')+(task.target_label||task.record_id||'—')));
 if(success){
  if((task.verification?.diff?.comparison_mode||task.verification?.comparison_mode)==='names_only')host.append(el('p',tr('按宽松模式完成：同名组件已跳过，仅验证组件名称；未验证内部文件或版本一致。','Completed in loose mode: matching names were skipped; internal files and versions were not verified.')));
  const applied=(task.action_results||[]).filter(r=>r.status==='applied');
  const metrics=el('div','','qv-metrics');for(const [title,count] of [[tr('恢复插件','Restored plugins'),applied.filter(r=>r.kind==='plugin'&&r.operation==='restore').length],[tr('移除插件','Removed plugins'),applied.filter(r=>r.kind==='plugin'&&r.operation==='remove').length],[tr('包状态变化','Package changes'),applied.filter(r=>r.kind==='package').length],[tr('恢复已保存资源','Restored saved resources'),applied.filter(r=>r.saved_artifact&&r.operation==='restore').length]])metrics.append(el('p',title+' · '+count,'qv-metric'));host.append(metrics);
  host.append(el('p',task.verification?.environment_rescanned===false?tr('已按确认的对比计划执行全部操作；未重新扫描环境。','All approved comparison actions were applied; the environment was not rescanned.'):tr('已完成恢复验证。','Restore verification completed.')));
 }else{
  const reasons={preparation_failed:tr('准备失败，未开始修改环境。','Preparation failed; environment execution did not start.'),handoff_failed:tr('交接失败，未确认执行；请检查任务日志。','Handoff failed; execution is unconfirmed. Inspect task logs.'),failed_rolled_back:tr('执行失败并已回退，不代表已达到目标版本。','Execution failed and rolled back; the target was not restored.'),failed_rollback:tr('回退未完成，已保留材料；请勿直接删除或重复执行。','Rollback incomplete. Staging retained; do not delete it or replay blindly.'),interrupted:tr('任务中断，未确认完成；不自动重放。','Task interrupted; completion unconfirmed. No automatic replay.'),cancelled:tr('已取消未执行的任务。','The unexecuted task was cancelled.'),partial:tr('最终验证、重连或清理未完成；不报告成功。','Verification, reconnection or cleanup incomplete; not reported as success.')};
  host.append(el('p',reasons[task.status]||tr('完成证据不足，请检查详情。','Insufficient completion evidence; inspect details.'),'qv-warning'));
 }
 host.append(el('p',tr('仅承诺受管理状态；不保证 Python 字节级一致性、GPU 或工作流运行成功。','Managed state only, not byte-level Python integrity, GPU or workflow success.'),'qv-muted'));
 if(task.error)host.append(el('p',task.error,'qv-warning'));
 for(const error of task.rollback_errors||[])host.append(el('p',error,'qv-warning'));
 const details=el('details');details.open=!success;details.append(el('summary',tr('结果与错误详情','Result and error details')),el('p',tr('任务编号：','Task ID: ')+task.id));
 if(task.task_directory)details.append(el('p',tr('日志 / 暂存目录：','Logs / staging: ')+task.task_directory),el('p','state.json · worker.log · verification.json'));
 renderActionResults(details,task.action_results||[],tr);
 if(task.verification?.diff){const diffHost=el('div');renderStatePlan(diffHost,task.verification,tr);details.append(diffHost);}host.append(details);
}
export function renderActionResults(host,rows,tr){
 if(!rows.length)return;
 const labels={applied:tr('已应用','Applied'),rolled_back:tr('已回退','Rolled back'),failed:tr('失败','Failed'),skipped:tr('已跳过','Skipped')};
 const list=el('div','','qv-plan-list'),pager=el('div','','qv-plan-pager'),position=el('span');let page=0;const size=12;
 const previous=button(tr('上一页','Previous'),()=>{page--;draw();}),next=button(tr('下一页','Next'),()=>{page++;draw();});pager.append(position,previous,next);host.append(list,pager);
 function draw(){list.replaceChildren();for(const row of rows.slice(page*size,(page+1)*size))list.append(el('p',row.resource_id+' · '+(labels[row.status]||row.status)+(row.error?' — '+row.error:'')));position.textContent=`${page*size+1}–${Math.min((page+1)*size,rows.length)} / ${rows.length}`;previous.disabled=page===0;next.disabled=(page+1)*size>=rows.length;pager.hidden=rows.length<=size;}
 draw();
}
export function mountCurrentRestore(host,recordId,tr,{onState=()=>{},onDone=()=>{}}={}){
 const container=host,steps=el('ol','','qv-restore-steps'),panel=el('div','','qv-restore-content');
 const stepLabels=[tr('查看差异','Review changes'),tr('准备材料','Prepare materials'),tr('执行恢复','Apply restore'),tr('查看结果','View result')];
 for(const [index,title] of stepLabels.entries()){const item=el('li');item.append(el('span',String(index+1)),el('strong',title));steps.append(item);}
 steps.setAttribute('aria-label',tr('恢复步骤','Restore steps'));container.replaceChildren(steps,panel);host=panel;
 function setStep(index){Array.from(steps.children).forEach((item,i)=>{item.classList.toggle('is-current',i===index);item.classList.toggle('is-complete',i<index);if(i===index)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});}
 setStep(0);
 let comparisonMode=reviewedMode(recordId);
 let alive=true,timer=null,task=null,plan=null,pending=false,starting=false,waitingPrepare=false,lastError='',revision=0;
 const finish=()=>{alive=false;clearTimeout(timer);};
 const attached=()=>alive&&host.isConnected;
 const getDetails=async id=>taskRequest('/current-restore/'+encodeURIComponent(id));
 function schedule(){clearTimeout(timer);if(attached())timer=setTimeout(poll,2000);}
 function disconnected(error){if(!attached())return;host.replaceChildren(el('h4',tr('等待手动关闭 / 重启 ComfyUI','Waiting for manual shutdown / restart')),el('p',tr('本功能不会自动关闭或启动 ComfyUI。请手动关闭原启动窗口，等待独立恢复窗口显示“恢复操作已完成”后，再用原启动脚本手动启动。不要在恢复完成前启动第二份 ComfyUI。','ComfyUI is never stopped or started automatically. Close the original window, wait for the separate restore window to report completion, then launch ComfyUI manually. Do not start a second copy before restoration finishes.'),'qv-warning'));host.append(el('p',tr('离线期间网页无法读取进度，请查看独立恢复窗口。此页面不会重新提交恢复。','While offline, follow progress in the separate restore window. This page will not resubmit restoration.')));schedule();}

 async function poll(){if(!attached())return;const turn=revision;try{const current=task?.id?await getDetails(task.id):await taskRequest('/current-restore');if(!attached())return;if(turn!==revision){if(pending||starting||waitingPrepare||activeStates.has(task?.status))schedule();return;}if(waitingPrepare&&!task&&!(current.record_id===recordId&&['preparing','prepared','preparation_failed'].includes(current.status))){if(!pending){host.replaceChildren(el('h4',tr('准备结果尚未确认','Preparation result unconfirmed')),el('p',lastError||tr('未读取到新任务；不会重复提交。请稍后重开恢复面板核对。','No new task available; not resubmitting. Reopen this panel later to check.'),'qv-warning'));}schedule();return;}if(current.id){const details=current.plan?current:await getDetails(current.id);if(turn!==revision){if(pending||starting||waitingPrepare||activeStates.has(task?.status))schedule();return;}task=details;waitingPrepare=false;onState(task);render();if(task.status!=='prepared'&&!activeStates.has(task.status))starting=false;}if(pending||starting||activeStates.has(task?.status))schedule();}catch(error){disconnected(error);}}
 function render(){
  if(!attached()||!task)return;
  setStep(task.status==='prepared'?(starting?2:1):task.status==='preparing'?1:activeStates.has(task.status)?2:3);
  if(task.status==='prepared'&&starting){host.replaceChildren(el('h4',tr('正在交接恢复任务','Handing off the restore task')));renderTaskProgress(host,task,tr);host.append(el('p',tr('正在核验已准备材料，随后按确认计划开始恢复（需手动关闭 ComfyUI），不重新扫描环境。','Checking prepared materials and opening the restore window. Close ComfyUI manually after handoff; no environment rescan.')));return;}
  if(task.status==='prepared'){
   host.replaceChildren(el('h4',tr('恢复材料已就绪','Restore materials prepared')));
   host.append(el('p',tr('下一步仅打开独立恢复窗口。请手动关闭当前 ComfyUI，等待恢复窗口提示完成后，再手动启动；不会自动关闭或重启服务。','Next opens a separate restore window. Close ComfyUI manually, wait for restore completion, then start it manually. No automatic shutdown or restart.'),'qv-warning'));
   if(task.plan){const details=el('details');details.append(el('summary',tr('复核冻结计划','Review frozen plan')));const content=el('div');renderStatePlan(content,task.plan,tr);details.append(content);host.append(details);}
   if(!task.plan){host.append(el('p',tr('旧任务没有可供复核的冻结计划。请取消后重新准备；不能直接执行。','This older task has no reviewable frozen plan. Cancel and prepare again; execution is unavailable.'),'qv-warning'),button(tr('取消未执行任务','Cancel unexecuted task'),()=>cancel()));return;}
   const ack=el('input');ack.type='checkbox';ack.setAttribute('aria-label',tr('确认停机恢复当前实例','Confirm stop and restore current instance'));const label=el('label','','qv-restore-confirm');label.append(ack,document.createTextNode(tr('我确认已检查删除/替换项，将手动关闭当前 ComfyUI，并在恢复完成后手动重新启动。','I reviewed removals/replacements and will close ComfyUI manually and restart it only after restoration completes.')));
   const start=button(tr('开始恢复（需手动关闭 ComfyUI）','Begin restore (close ComfyUI manually)'),async()=>{
    if(!ack.checked||pending||starting)return;revision++;pending=true;starting=true;start.disabled=true;ack.disabled=true;rememberTask('restore',task.id);host.append(el('p',tr('正在核验已准备材料并交接执行；不重新扫描环境。','Checking prepared materials and handing off execution; no environment rescan.')));schedule();
    try{await taskRequest('/current-restore/start',{job_id:task.id,plan_hash:task.plan_hash,confirmed:true});revision++;task={...task,status:'waiting_for_exit',manual_restart:true,progress:{phase:'waiting_for_exit'}};render();}
    catch(error){lastError=error.message;if(error.status){starting=false;render();}else disconnected(error);}
    finally{pending=false;schedule();}
   });start.classList.add('qbb-lh-btn-primary');start.disabled=true;ack.onchange=()=>{start.disabled=!ack.checked||pending||starting;};const actions=el('div','','qv-restore-actions');actions.append(label,start,button(tr('取消未执行任务','Cancel unexecuted task'),()=>cancel()));host.append(actions);
   if(lastError)host.append(el('p',lastError,'qv-warning'));
  }else if(task.status==='awaiting_manual_restart'){
   host.replaceChildren(el('h4',tr('恢复操作已执行，等待手动启动','Restore actions finished; start ComfyUI manually')),el('p',tr('请使用原来的启动脚本手动启动 ComfyUI。程序不会自动启动服务；启动后此处将读取恢复结果。','Use your usual launcher to start ComfyUI manually. No automatic restart; the result will be loaded after startup.')));
  }else if(activeStates.has(task.status)){
   host.replaceChildren(el('h4',task.status==='waiting_for_exit'?tr('请手动关闭当前 ComfyUI','Close the current ComfyUI manually'):tr('正在恢复当前实例','Restoring current instance')));renderTaskProgress(host,task,tr);host.append(el('p',tr('请手动关闭原 ComfyUI 启动窗口，不要关闭独立恢复窗口。等待恢复窗口提示完成后，再手动启动 ComfyUI。','Close the original ComfyUI window, keeping the separate restore window open. Restart ComfyUI manually only after that window reports completion.')));
  }else{
   clearReviewedComparisons(recordId);renderRestoreResult(host,task,tr);
   host.append(button(tr('完成','Done'),onDone));
   if(['preparation_failed','handoff_failed'].includes(task.status))host.append(button(tr('清理未执行任务','Clear unexecuted task'),()=>cancel()));
   if(['completed','cancelled'].includes(task.status))host.append(button(tr('重新对比当前状态','Compare current state again'),()=>{task=null;starting=false;loadPlan();}));
  }
 }
 async function cancel(){if(pending)return;revision++;pending=true;try{task=await taskRequest('/current-restore/cancel',{job_id:task.id,confirmed:true});revision++;starting=false;rememberTask('restore',null);render();onState(task);}catch(error){host.append(el('p',error.message,'qv-warning'));}finally{pending=false;}}
 async function loadPlan(checkMaterials=false){
  checkMaterials=checkMaterials===true;
  if(!attached())return;setStep(checkMaterials?1:0);revision++;clearTimeout(timer);waitingPrepare=false;if(checkMaterials)host.append(el('p',tr('正在检查恢复材料；上方对比结果仍可查看。','Checking restore materials; the reviewed comparison remains visible.'),'qv-muted'));else host.replaceChildren(el('h4',tr('恢复当前实例','Restore current instance')),el('p',tr('正在读取已有对比结果…','Loading the reviewed comparison…')));
  try{
   plan=await requestStatePlan((path,payload)=>taskRequest('/version-records'+path,payload),recordId,comparisonMode,!checkMaterials);if(!attached())return;
   host.replaceChildren(el('h4',tr('恢复当前实例','Restore current instance')));
   const modeLabel=el('label',tr('恢复判定方式：','Restore comparison mode: ')),mode=el('select');mode.setAttribute('aria-label',tr('恢复判定方式','Restore comparison mode'));
   for(const [value,title] of [['strict',tr('严格：核对内部文件','Strict: compare internal files')],['names_only',tr('宽松：同名组件跳过恢复','Loose: skip matching component names')]]){const option=el('option',title);option.value=value;mode.append(option);}mode.value=comparisonMode;mode.onchange=()=>{comparisonMode=mode.value;loadPlan();};modeLabel.className='qv-mode-control';modeLabel.append(mode);host.append(modeLabel);
   if(comparisonMode==='names_only')host.append(el('p',tr('同名插件、依赖包和配置项保持现状；仅处理缺失或多出的组件。','Matching plugin, package and configuration names are kept as they are; only missing or extra components are handled.')));
   host.append(el('p',plan.blockers.length?tr('存在阻碍，不能开始恢复。请展开差异查看原因。','Blocked: restoration cannot start. Inspect the differences.'):plan.execution_ready===false?tr('请先查看差异与恢复材料状态。','Review differences and recovery material status first.'):plan.actions.length?tr('先核对变化并准备材料；此步骤不会停止 ComfyUI。','Review changes and prepare materials first; this step does not stop ComfyUI.'):tr('没有需要恢复的受管差异；未执行恢复。','No managed differences require restoration; no restore executed.'),plan.blockers.length?'qv-warning':''));
   if(plan.evidence==='reused_comparison_preflight_required')host.append(el('p',tr('已继承对比结果；按确认计划准备和执行，不重新扫描环境。','Reusing the reviewed comparison for preparation and execution without environment rescans.'),'qv-muted'));
   const content=el('div');renderStatePlan(content,plan,tr);host.append(content);
   if(plan.status==='comparison_only'){
    const hasDifferences=(plan.diff.change_count||0)>0;
    if(!hasDifferences){host.append(el('p',plan.diff.unverified_count?tr('尚有无法核实的信息，请先查看对比详情。','Some evidence is unverified; inspect the comparison first.'):tr('没有需要恢复的组件；无需扫描文件或检查备份。','No components need restoration; file scanning and backup checks are unnecessary.')));return;}
    host.append(el('p',tr('已继承对比结果，尚未扫描恢复文件或校验备份。需要恢复时再点击下方按钮。','The reviewed comparison is shown. Restore file evidence and backups are checked only when requested.')));
    const inspect=button(tr('检查恢复材料','Check restore materials'),()=>{inspect.disabled=true;mode.disabled=true;loadPlan(true);});inspect.classList.add('qbb-lh-btn-primary');const actions=el('div','','qv-restore-actions');actions.append(inspect);host.append(actions);return;
   }
   if(plan.blockers.length)return;
   if(plan.execution_ready===false){host.append(el('p',tr('对比信息不完整，暂不能准备恢复。请查看环境检查与组件详情。','Comparison evidence is incomplete; inspect environment and component details before preparation.'),'qv-warning'));return;}
   if(!plan.actions.length)return;
   const trust=el('input');trust.type='checkbox';trust.setAttribute('aria-label',tr('确认快照可信与操作范围','Confirm snapshot trust and scope'));const trusted=el('label','','qv-restore-confirm');trusted.append(trust,document.createTextNode(tr('此快照来源可信；我已检查删除/替换项，已停止工作流、外部安装和文件编辑。','This snapshot is trusted. I reviewed removals/replacements and paused workflows, external installs and edits.')));
   const network=el('input');network.type='checkbox';network.setAttribute('aria-label',tr('允许提前联网获取准确资源','Allow exact resource downloads'));const net=el('label','','qv-restore-confirm');net.append(network,document.createTextNode(tr('允许提前联网获取计划中的准确 Git / wheel（仍须校验）。','Allow exact Git / wheel downloads from the plan (verification still required).')));
   const prepare=button(tr('准备恢复材料','Prepare restore materials'),async()=>{
    if(!trust.checked||pending)return;setStep(1);revision++;pending=true;waitingPrepare=true;lastError='';prepare.disabled=true;mode.disabled=true;trust.disabled=network.disabled=true;host.append(el('p',tr('正在按已确认计划准备材料。尚未停止 ComfyUI。','Preparing materials from the approved plan; ComfyUI has not stopped.')));schedule();
    try{const result=await taskRequest('/current-restore/prepare',{record_id:recordId,fingerprint:plan.fingerprint,confirmed:true,allow_network:network.checked,comparison_mode:comparisonMode,comparison_token:plan.comparison_token});clearReviewedComparisons(recordId);revision++;waitingPrepare=false;if(result.id){task=await getDetails(result.id);rememberTask('restore',task.id);onState(task);render();}else await loadPlan();}
    catch(error){lastError=error.message;if(error.status&&attached()){revision++;waitingPrepare=false;clearTimeout(timer);host.append(el('p',error.message,'qv-warning'),button(tr('重新只读检查','Check again read-only'),loadPlan));}else disconnected(error);}
    finally{pending=false;if(task||waitingPrepare)schedule();}
   });prepare.disabled=true;trust.onchange=()=>{prepare.disabled=!trust.checked||pending;};const actions=el('div','','qv-restore-actions');host.append(actions);actions.append(trusted);if(plan.actions.some(action=>!action.material||action.material.kind==='remote_source'))actions.append(net);else actions.append(el('p',tr('使用已保存的本地材料；无需联网下载。','Uses saved local materials; no downloads required.'),'qv-muted'));prepare.classList.add('qbb-lh-btn-primary');actions.append(prepare);
  }catch(error){if(attached()){if(!checkMaterials)host.replaceChildren(el('h4',tr('无法生成恢复计划','Cannot generate restore plan')));host.append(el('p',error.message,'qv-warning'),button(tr('重新只读检查','Check again read-only'),()=>loadPlan(checkMaterials)));}}
 }
 (async()=>{try{const current=await taskRequest('/current-restore');if(!attached())return;if(current.id&&current.status!=='cancelled'&&(current.record_id===recordId||!['completed','idle'].includes(current.status))){task=await getDetails(current.id);onState(task);render();if(activeStates.has(task.status))schedule();}else await loadPlan();}catch(error){disconnected(error);}})();
 return finish;
}
