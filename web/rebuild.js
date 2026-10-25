import {api} from '../../scripts/api.js';
import {isChineseLocale} from './i18n.js';
import './http.js';
import {renderComparison} from './record-compare.js';
import {renderRecoveryPlan} from './recovery-plan.js';
import {renderRecoveryTask} from './recovery-task.js';
import {renderRecoveryEnvironment} from './recovery-environment.js';
export function mountRebuild(host,recordId,{autoCheck=false,mode='restore',otherRecordId=null}={}){
 const tr=(a,b)=>isChineseLocale()?a:b;
 const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const button=(text,fn)=>{const b=el('button',text);b.type='button';b.className='qbb-lh-btn';b.onclick=fn;return b;};
 const panel=el('section');panel.className=mode==='restore'?'qbb-lh-card qv-restore':'qbb-lh-card';host.append(panel);
 panel.append(el('h3',mode==='compare'?tr('与当前环境对比','Compare with current environment'):tr('恢复到新实例','Restore into a new instance')),el('p',tr('只创建新目录，不覆盖当前环境。缺失内容需要明确选择恢复方式；不执行安装脚本，不自动启动第三方节点。','Creates a new directory only. Missing content requires an explicit choice. Does not run installation scripts or automatically start third-party nodes.')));
 if(mode==='restore'){panel.querySelector('h3').hidden=true;panel.querySelector('p').hidden=true;}
 const summary=el('div');summary.className='qv-restore-overview';const problems=el('details');problems.className='qv-recovery-problems';const problemToggle=el('summary',tr('处理问题','Resolve issues'));const problemCount=el('p');problemCount.className='qv-restore-alert';const problemBody=el('div');problemBody.className='qv-problem-body';problems.append(problemToggle,problemBody);if(mode==='restore')panel.append(summary,problemCount,problems);
 const status=el('p');status.className='qv-restore-status';status.setAttribute('role','status');const output=el('div');panel.append(status,output);
 panel.querySelector('p').dataset.pageHelp='';
 if(mode==='compare'){panel.querySelector('h3').textContent=otherRecordId?tr('两条记录之间的变化','Changes between records'):tr('与当前环境对比','Compare with current environment');panel.querySelector('p').textContent=otherRecordId?tr('只比较保存清单，不读取原实例，也不验证对象是否仍可恢复。','Compares saved manifests only; does not read source instances or verify object availability.'):tr('只读比较记录与当前实例，不修改文件。','Read-only comparison with the running instance; no files are modified.');}
 async function request(action,payload){if(action==='/compare'&&otherRecordId)payload={...payload,other_record_id:otherRecordId};const r=await api.fetchApi('/qbb_nodeops/rebuild'+action,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});const d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d;}
 let ready=false, checking=false, submitting=false, running=false, picking=false, planId=null;
 const choices={};
 const invalidate=()=>{problemCount.textContent=tr('选择已变化，请重新检查。','Choices changed; recheck required.');ready=false;planId=null;syncControls();};
 const recovery=el('section');recovery.className='qv-recovery-section qv-restore-destination';
 const path=el('input');path.className='qbb-stretch';path.placeholder=tr('新的绝对目录（不能已存在）','New absolute directory (must not exist)');path.setAttribute('aria-label',tr('恢复目标目录','Recovery destination'));
 const chooseFolder=button(tr('选择文件夹…','Choose folder…'),async()=>{
   if(picking||checking||submitting||running)return;
   picking=true;syncControls();
   try{
     const response=await api.fetchApi('/qbb_nodeops/instances/choose-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
     const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);
     if(data.path&&!running&&panel.isConnected){
       const separator=data.path.includes('\\')?'\\':'/';
       path.value=data.path.replace(/[\\/]+$/,'')+separator+'ComfyUI-restored-'+Date.now();

       status.textContent=tr('已选择存放位置，将在其中新建下方目录；不会覆盖所选文件夹。','Location selected. Recovery will create the directory below without overwriting the selected folder.');
     }
   }catch(error){status.textContent=tr('无法选择文件夹：','Unable to choose folder: ')+error.message+tr('；也可以手动填写下方路径。','; you can also type a path below.');}
   finally{picking=false;syncControls();}
 });
 const start=button(tr('开始恢复','Start recovery'),async()=>{
   if(!ready||checking||submitting||running||picking||!path.value.trim())return;
   if(!confirm(tr('仅恢复可信记录。将在所选新目录执行 Python 并安装依赖，联网模式会下载文件。确认开始恢复？','Restore only trusted records. Run Python and install dependencies in the new directory; online mode downloads files. Start recovery?')))return;
   submitting=true;syncControls();
   try{await request('/start',{record_id:recordId,target:path.value.trim(),confirmed:true,choices,plan_id:planId});running=true;status.textContent=tr('恢复任务已启动。','Recovery started.');poll();}
   catch(e){status.textContent=tr('恢复未启动：','Recovery did not start: ')+e.message;}
   finally{submitting=false;syncControls();}
 });
 start.classList.add('qbb-lh-btn-primary');
 function syncControls(){
   start.disabled=!ready||checking||submitting||running||picking||!path.value.trim();
   path.disabled=chooseFolder.disabled=checking||submitting||running||picking;
   check.disabled=checking||submitting||running||picking;
 }
 path.addEventListener('input',()=>{syncControls();});
 const pathRow=el('div');pathRow.className='qv-path-picker';pathRow.append(el('span',tr('恢复目录','Recovery folder')),path,chooseFolder);
 recovery.append(el('h4',tr('恢复到新目录','Restore to a new directory')),pathRow,start);
 const check=button(mode==='compare'?tr('重新对比','Compare again'):tr('重新检查','Check again'),async()=>{checking=true;ready=false;syncControls();status.textContent=mode==='compare'?tr('正在逐文件读取并对比，请勿修改环境…','Reading and comparing files; avoid editing the environment…'):tr('正在检查记录与当前版本…','Inspecting record and current versions…');output.replaceChildren();try{if(mode==='compare'){const result=await request('/compare',{record_id:recordId});renderComparison(output,result,tr);status.textContent=result.change_count?tr('对比完成，发现 ','Comparison complete: ')+result.change_count+tr(' 项变化。',' changes.'):result.unverified_count?tr('未发现已确认差异，但部分内容无法核实。','No confirmed differences; some content is unverified.'):tr('对比完成，保存范围内一致。','Comparison complete: captured scope matches.');return;}const plan=await request('/plan',{record_id:recordId,choices});planId=plan.plan_id;problemBody.replaceChildren();summary.replaceChildren();
 const repositories=plan.repositories||[];const dependencies=plan.dependencies||plan.pins||[];
 summary.append(el('h4',tr('恢复概要','Recovery overview')));const metrics=el('div');metrics.className='qv-restore-metrics';
 for(const [value,label] of [[dependencies.filter(p=>p.action!=='skip').length,tr('依赖组件','Dependencies')],[repositories.filter(r=>!r.core&&r.action!=='skip').length,tr('插件','Plugins')],[plan.runtime?.version||tr('待确认','To be confirmed'),'Python']]){const metric=el('div');metric.className='qv-restore-metric';metric.append(el('strong',String(value)),el('span',label));metrics.append(metric);}summary.append(metrics);
 const count=plan.blockers.length+plan.warnings.length;problemCount.textContent=count?plan.blockers.length+tr(' 项待处理',' blockers')+' · '+plan.warnings.length+tr(' 项提示',' notes'):tr('检查通过，可以恢复','Ready to restore');problemCount.className='qv-restore-alert'+(plan.blockers.length?' is-blocked':count?' is-notice':' is-ready');problemToggle.textContent=count?tr('处理问题','Resolve issues'):tr('查看恢复详情','View recovery details');
 renderRecoveryPlan(problemBody,plan,choices,invalidate,tr);renderRecoveryEnvironment(problemBody,plan,choices,invalidate,tr);problemBody.append(check);ready=!!plan.can_rebuild&&!plan.blockers.length;status.textContent='';
 const issues=el('details');issues.className='qv-recovery-section';issues.append(el('summary',tr('恢复条件：','Recovery requirements: ')+plan.blockers.length+tr(' 项阻碍 · ',' blockers · ')+plan.warnings.length+tr(' 项提示',' notes')));for(const text of plan.warnings)issues.append(el('p',text));
 if(mode==='restore'){for(const reason of plan.blockers)issues.append(el('p',tr('无法开始：','Blocked: ')+reason));problemBody.append(issues);problemBody.append(el('p',tr('本地文件按记录中的快照恢复；缺失的旧插件按你确认的方式处理。请查看提示中的跳过项和排除文件。依赖安装仍可能因网络或版本不兼容而失败。','Local files use saved snapshots. Missing old plugins are skipped independently. Review exclusions and skipped items. Dependency installation can still fail due to networking or compatibility.')));}
 }catch(e){status.textContent=e.message;if(mode==='restore'){problems.open=true;problemBody.append(check);}}finally{checking=false;syncControls();}});if(mode==='compare')panel.append(check);else problemBody.append(check);if(mode==='restore')panel.append(recovery);syncControls();
 let timer;
 const taskStatus=el('p');taskStatus.setAttribute('role','status');const taskItems=el('div');panel.append(taskStatus,taskItems);
 async function poll(){clearTimeout(timer);if(!panel.isConnected)return;try{const data=await request('');const task=data.current?.record_id===recordId?data.current:(data.history||[]).find(t=>t.record_id===recordId);if(task){running=task.status==='running'||task.verification?.status==='running';syncControls();taskStatus.textContent=[({rebuilt_unverified:tr('恢复完成（运行未完整验证）','Recovered; runtime not fully verified'),partial:tr('恢复完成，有缺项或替代内容','Recovered with gaps/substitutions'),failed:tr('恢复失败','Recovery failed'),running:tr('恢复中','Recovering')}[task.status]||task.status),task.stage,task.error,task.target].filter(Boolean).join(' · ');renderRecoveryTask(taskItems,task,tr,async jobId=>{submitting=true;syncControls();try{await request('/resume',{job_id:jobId,confirmed:true});running=true;poll();}catch(e){taskStatus.textContent=tr('无法继续：','Unable to resume: ')+e.message;}finally{submitting=false;syncControls();}},async(jobId,options)=>{submitting=true;syncControls();try{await request('/verify',{job_id:jobId,confirmed:true,...options});running=true;poll();}catch(e){taskStatus.textContent=tr('无法验证：','Unable to verify: ')+e.message;}finally{submitting=false;syncControls();}});if(running)timer=setTimeout(poll,2000);}}catch(e){taskStatus.textContent=tr('读取任务状态失败：','Unable to read task status: ')+e.message;}}
 if(mode==='restore')poll();if(autoCheck)check.onclick();
}
