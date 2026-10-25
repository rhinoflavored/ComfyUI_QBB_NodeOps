// Share the reviewed scan between comparison and restore; execution preflight stays live.
const reviewedComparisons=new Map(),selectedModes=new Map();
export function clearReviewedComparisons(recordId){selectedModes.delete(recordId);for(const [key,entry] of reviewedComparisons)if(entry.recordId===recordId)reviewedComparisons.delete(key);}
export function reviewedMode(recordId){const mode=selectedModes.get(recordId),entry=reviewedComparisons.get(recordId+':'+mode);return entry&&entry.expires>Date.now()?mode:'names_only';}
export async function requestStatePlan(request,recordId,mode,contentOnly=true,extra={}){
 selectedModes.set(recordId,mode);
 const key=recordId+':'+mode;let entry=reviewedComparisons.get(key);
 if(entry&&entry.expires<=Date.now()){reviewedComparisons.delete(key);entry=null;}
 if(entry?.pending){await entry.pending;entry=reviewedComparisons.get(key);}
 if(entry?.plan&&(contentOnly||entry.plan.materials_checked!==false))return entry.plan;
 if(!contentOnly&&!entry?.plan){await requestStatePlan(request,recordId,mode,true,extra);return requestStatePlan(request,recordId,mode,false,extra);}
 const previous=entry;
 const next={recordId,mode,expires:Date.now()+300000};
 const pending=request('/'+encodeURIComponent(recordId)+'/recovery-plan',{...extra,content_only:contentOnly,comparison_mode:mode,...(previous?.plan?.comparison_token?{comparison_token:previous.plan.comparison_token}:{})});
 next.pending=pending;reviewedComparisons.set(key,next);
 try{const plan=await pending;next.plan=plan;next.pending=null;next.expires=previous?.expires||Date.now()+Math.min(plan.comparison_ttl||300,300)*1000;return plan;}
 catch(error){if(reviewedComparisons.get(key)===next)reviewedComparisons.delete(key);throw error;}
}

// Read-only comparison; controls only filter and page the existing scan.
export function renderStatePlan(host,plan,tr){
 const el=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
 const compact=v=>typeof v==='object'?JSON.stringify(v):String(v??'—');
 const reasonName=code=>({shared_installed_file:tr('文件由多个包共用；可以比较，但不能单独替换或删除','Shared files can be compared but cannot be replaced or removed independently'),git_metadata_changed:tr('Git 提交信息变化；文件内容已独立核对','Git metadata changed; file contents compared separately'),source_metadata_changed:tr('来源信息变化；文件内容已独立核对','Source metadata changed; file contents compared separately'),source_unknown:tr('来源未知（本地内容仍可核对）','Unknown origin; local contents can still be compared'),installed_content_not_recorded:tr('仅有版本与来源信息，未记录安装文件','Only version/source evidence; installed files were not recorded'),target_zip_unavailable_or_invalid:tr('目标 ZIP 缺失或校验失败','Target ZIP missing or invalid'),target_installed_files_not_saved:tr('仅有源码备份，缺少安装文件，不能保证恢复包','Only source backup; installed files are unavailable'),target_backup_incomplete:tr('目标备份不完整','Target backup incomplete'),backup_material_invalid:tr('备份材料损坏或缺失','Backup material damaged or missing'),current_installed_inventory_incomplete:tr('当前包文件清单不完整，不能安全移除','Current file inventory incomplete; removal unavailable'),scope_incomplete:tr('扫描范围不完整','Incomplete scan coverage'),python_interpreter_incompatible_or_unknown:tr('Python 环境不同或信息不足','Python incompatible or unknown'),current_content_unverified:tr('当前内容无法完整核验','Current content could not be verified')}[code]||code);
 const materialName=code=>({local_zip:tr('本地 ZIP 已校验','Local ZIP verified'),local_snapshot:tr('本地文件快照已校验','Local snapshot verified'),remote_source:tr('准确远程来源（尚未下载校验）','Exact remote source; download unverified'),removal:tr('移除当前多出内容','Remove extra content'),installed_file_removal:tr('移除当前包的受管文件','Remove managed installed files'),unavailable:tr('缺少完整恢复材料','Complete restore material unavailable')}[code]||code);
 const names={historical_gap:tr('历史采集缺口','Historical capture gap'),matched:tr('一致','Matched'),blocked:tr('受阻','Blocked'),differences:tr('有差异','Changed'),protected_resource:tr('受保护','Protected'),missing:tr('当前缺失 → 补回','Missing now → restore'),extra:tr('当前多出 → 移除','Extra now → remove'),version_mismatch:tr('版本不同','Version mismatch'),commit_mismatch:tr('提交不同','Commit mismatch'),source_mismatch:tr('来源不同','Source mismatch'),content_mismatch:tr('内容不同','Content mismatch'),type_mismatch:tr('类型不同','Type mismatch'),permissions_mismatch:tr('权限不同','Permissions mismatch'),unknown:tr('未知','Unknown'),unrestorable:tr('无法保证恢复','Unrestorable')};
 host.replaceChildren();const root=el('section','','qv-plan');host.append(root);
 root.append(el('h4',tr('当前环境 → 目标记录','Current environment → Target record')),el('p',tr('只读对比 · 查看差异与恢复阻碍','Read-only comparison · Review changes and recovery blockers'),'qv-plan-description'));
 if(plan.diff.comparison_mode==='names_only')root.append(el('p',tr('宽松判定：组件名称一致即视为一致；不核对版本、来源及内部文件。','Loose matching: matching component names count as equal; versions, sources and internal files are ignored.')));
 const overview=el('div','','qv-plan-overview');
 const changed=plan.diff.resources.filter(r=>r.differences.some(c=>!['unknown','unrestorable'].includes(c.status))).length;
 for(const [label,value] of [[tr('内容对比','Content comparison'),changed?tr('存在差异','Changes found'):plan.diff.unverified_count>(plan.diff.historical_gap_count||0)?tr('信息不足','Incomplete evidence'):plan.diff.historical_gap_count?tr('已核对内容一致','Verified content matches'):tr('无差异','No changes')],[tr('变化组件','Changed components'),changed],[plan.materials_checked===false?tr('备份材料','Backup materials'):tr('恢复阻碍','Recovery blockers'),plan.materials_checked===false?tr('恢复时校验','Checked before restore'):plan.blockers.length]]){const metric=el('div');metric.append(el('strong',String(value)),el('span',label));overview.append(metric);}root.append(overview);
 if(plan.diff.historical_gap_count)root.append(el('p',tr('历史采集缺口：','Historical capture gaps: ')+plan.diff.historical_gap_count+tr(' 项；旧记录未采集的内容不作为新增，也不生成移除建议。',' items; uncaptured historical content is not treated as added or proposed for removal.')));
 if(plan.material_notices?.length){const details=el('details');details.append(el('summary',tr('恢复材料不足 / 仅有来源记录','Incomplete restore materials / source only')+' · '+plan.material_notices.length));for(const notice of plan.material_notices)details.append(el('p',notice.name+' · '+({incomplete:tr('保存不完整','Incomplete backup'),metadata_only:tr('仅保存元数据','Metadata only'),source_only:tr('仅记录来源','Source only')}[notice.status]||notice.status)));root.append(details);}
 if(plan.diff.unverified_count>(plan.diff.historical_gap_count||0))root.append(el('p',tr('部分信息无法确认，请展开组件或环境检查；不能据此宣称环境完全一致。','Some evidence is incomplete; inspect component/environment details. Full equivalence cannot be confirmed.'),'qv-warning'));
 if(plan.backup_issues?.length){const backup=el('details','','qv-plan-reasons');backup.append(el('summary',tr('备份材料提醒','Backup material notices')+' · '+plan.backup_issues.length));for(const issue of plan.backup_issues)backup.append(el('p',issue.resource_id+' · '+reasonName(issue.reason)));root.append(backup);}
 const groups=new Map();for(const b of plan.blockers){if(!groups.has(b.reason))groups.set(b.reason,[]);groups.get(b.reason).push(b);}
 let reason='',page=0;const pageSize=12;
 if(groups.size){const reasons=el('details','','qv-plan-reasons');reasons.append(el('summary',tr('阻碍原因','Blocker reasons')+' · '+groups.size+tr(' 类（点击展开）',' groups (expand)')));const body=el('div');for(const [key,entries] of groups){const button=el('button',reasonName(key)+' · '+entries.length,'qbb-lh-btn');button.type='button';button.title=key;button.onclick=()=>{reason=key;filter.value='all';page=0;draw();};body.append(button);}reasons.append(body);root.append(reasons);}
 if(plan.diff.runtime.length||plan.diff.issues.length){const environment=el('details','','qv-plan-reasons');environment.append(el('summary',tr('环境检查','Environment checks')+' · '+(plan.diff.runtime.length+plan.diff.issues.length)));for(const r of plan.diff.runtime)environment.append(el('p','Python '+r.field+' · '+compact(r.current)+' → '+compact(r.target)+' · '+r.reason));for(const issue of plan.diff.issues)environment.append(el('p',issue.side+' · '+issue.reason));root.append(environment);}
 const rows=plan.diff.resources.filter(r=>r.status!=='matched'||r.historical_gaps?.length);
 const blocked=r=>plan.blockers.some(b=>b.resource_id===r.resource_id);
 const toolbar=el('div','','qv-plan-toolbar'),search=el('input'),filter=el('select');search.type='search';search.value='';search.placeholder=tr('搜索组件名称','Search component names');search.setAttribute('aria-label',search.placeholder);filter.setAttribute('aria-label',tr('筛选对比结果','Filter comparison results'));
 for(const [value,label] of [['all',tr('全部结果','All results')],['blocked',tr('仅阻碍','Blocked only')],['history',tr('仅历史采集缺口','Historical gaps only')],['changes',tr('仅实际差异','Actual changes only')],['protected',tr('受保护','Protected')]]){const option=el('option',label);option.value=value;filter.append(option);}filter.value='all';
 const clear=el('button',tr('清除原因筛选','Clear reason filter'),'qbb-lh-btn');clear.type='button';clear.onclick=()=>{reason='';page=0;draw();};toolbar.append(search,filter,clear);root.append(toolbar);
 const list=el('div','','qv-plan-list'),pager=el('div','','qv-plan-pager'),position=el('span'),previous=el('button',tr('上一页','Previous'),'qbb-lh-btn'),next=el('button',tr('下一页','Next'),'qbb-lh-btn');previous.type=next.type='button';pager.append(position,previous,next);root.append(list,pager);
 function draw(){
  const visible=rows.filter(r=>(r.name+' '+r.resource_id).toLowerCase().includes(search.value.toLowerCase())&&(!reason||plan.blockers.some(b=>b.resource_id===r.resource_id&&b.reason===reason))&&(filter.value==='all'||filter.value==='history'&&r.historical_gaps?.length||filter.value==='blocked'&&blocked(r)||filter.value==='changes'&&r.differences.some(c=>!['unknown','unrestorable'].includes(c.status))||filter.value==='protected'&&r.status==='protected_resource'));
  const pages=Math.max(1,Math.ceil(visible.length/pageSize));page=Math.min(page,pages-1);list.replaceChildren();clear.hidden=!reason;clear.title=reason;
  for(const r of visible.slice(page*pageSize,(page+1)*pageSize)){
   const section=el('details','','qv-plan-row'),summary=el('summary');section.dataset.state=blocked(r)?'blocked':r.status;summary.append(el('span',r.name),el('span',blocked(r)?tr('受阻','Blocked'):(names[r.status]||r.status),'qv-plan-state'));section.append(summary);
   const content=el('div','','qv-plan-row-body');if(r.reason)content.append(el('p',r.reason));
   if(r.historical_gaps?.length){const gaps=el('details');gaps.append(el('summary',tr('历史采集缺口','Historical capture gaps')+' · '+r.historical_gaps.length));for(const gap of r.historical_gaps)gaps.append(el('p',gap.path+' · '+gap.reason));content.append(gaps);}
   if(r.differences.length)content.append(el('h5',tr('实际差异与当前核验问题','Actual differences and current verification issues')));
   for(const c of r.differences){
    const difference=el('section','','qv-difference'),caption=el('div','','qv-difference-caption');caption.append(el('strong',names[c.status]||c.status),el('code',c.path??c.field??''));difference.append(caption);
    const values=el('div','','qv-difference-values');for(const [label,value] of [[tr('当前环境','Current environment'),c.current],[tr('目标记录','Target record'),c.target]]){const cell=el('div');cell.append(el('span',label),el('pre',compact(value)));values.append(cell);}difference.append(values);content.append(difference);
   }
   const action=plan.actions.find(a=>a.resource_id===r.resource_id);if(action){const operationNames={restore:tr('恢复','Restore'),restore_resource:tr('恢复组件','Restore component'),remove_resource:tr('移除组件','Remove component'),restore_installed_files:tr('恢复安装文件','Restore installed files'),remove_installed_files:tr('移除安装文件','Remove installed files')};content.append(el('p',tr('计划操作：','Planned actions: ')+action.operations.map(op=>operationNames[op]||op).join(' · ')));if(action.material)content.append(el('p',tr('恢复材料：','Recovery material: ')+materialName(action.material.kind)));}
   for(const b of plan.blockers.filter(b=>b.resource_id===r.resource_id))content.append(el('p',tr('阻碍：','Blocked: ')+reasonName(b.reason)));
   for(const warning of r.warnings)content.append(el('p',tr('提示：','Notice: ')+reasonName(warning)));
   section.append(content);list.append(section);
  }
  if(!visible.length)list.append(el('p',rows.length?tr('没有匹配的组件','No matching components'):tr('未发现组件差异','No component differences'),'qv-plan-description'));
  position.textContent=visible.length?`${page*pageSize+1}–${Math.min((page+1)*pageSize,visible.length)} / ${visible.length}`:'0 / 0';previous.disabled=page===0;next.disabled=page===pages-1;pager.hidden=visible.length<=pageSize;
 }
 previous.onclick=()=>{page--;draw();};next.onclick=()=>{page++;draw();};search.oninput=filter.onchange=()=>{page=0;draw();};draw();
 root.append(el('small',tr('此页仅展示对比结果。确认恢复后按该计划准备材料并执行，不重新扫描环境。','This view shows the comparison. Confirmed restoration prepares and applies this plan without rescanning the environment.'),'qv-plan-footnote'));
}

export function loadStatePlan(host,request,recordId,tr){
 host.replaceChildren();
 const label=document.createElement('label'),select=document.createElement('select'),body=document.createElement('div');
 label.className='qv-mode-control';label.textContent=tr('对比范围','Comparison scope');
 select.setAttribute('aria-label',tr('判定方式','Comparison mode'));
 for(const [value,title] of [['strict',tr('严格：核对内部文件','Strict: compare internal files')],['names_only',tr('宽松：仅核对组件名称','Loose: component names only')]]){const option=document.createElement('option');option.value=value;option.textContent=title;select.append(option);}
 select.value='names_only';label.append(select);host.append(label,body);
 let generation=0,running=null;
 const hint=document.createElement('p');hint.className='qv-muted';host.insertBefore(hint,body);
 const run=()=>{
  generation++;
  if(running){hint.textContent=tr('正在完成当前扫描，随后按最新选择重新对比…','Finishing the current scan, then comparing with your latest selection…');return running;}
  running=(async()=>{
   let processed=0;
   while(host.isConnected&&processed!==generation){
    const current=generation,mode=select.value;processed=current;
    hint.textContent='';const panel=document.createElement('div');body.replaceChildren(panel);
    await loadComparison(panel,request,recordId,tr,mode,()=>current===generation,run);
   }
  })().finally(()=>{running=null;hint.textContent='';});
  return running;
 };
 select.onchange=run;return run();
}

async function loadComparison(host,request,recordId,tr,comparisonMode,isCurrent,retry){
 host.replaceChildren();const el=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
 const box=el('section','','qv-compare-loading'),stage=el('p',tr('读取版本记录','Reading record'),'qv-task-stage'),meter=el('progress'),current=el('p','','qv-muted'),elapsed=el('p','','qv-muted');meter.setAttribute('aria-label',tr('对比进度','Comparison progress'));
 box.setAttribute('aria-busy','true');stage.setAttribute('role','status');
 const status=el('div','','qv-loading-status');status.append(stage,elapsed);
 box.append(el('span',tr('只读检查','READ-ONLY CHECK'),'qv-eyebrow'),el('h4',tr('正在对比当前环境','Comparing the current environment')),el('p',comparisonMode==='names_only'?tr('正在核对组件名称；此模式不扫描内部文件，也不核对版本。','Checking component names; this mode does not scan internal files or compare versions.'):tr('正在核对版本、来源与内部文件；完整 ZIP 校验在恢复前执行。','Checking versions, sources and internal files; full ZIP validation runs before restore.'),'qv-muted'),status,meter,current);
 const note=el('p',tr('对比不会修改当前环境。完成后会展示差异摘要与组件明细。','Comparison does not change your environment. A summary and component differences will appear when ready.'),'qv-loading-note');host.append(box,note);
 const started=Date.now();let finished=false,timer;const identity=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID().replaceAll('-',''):null;
 const phases={read_record:tr('读取版本记录','Reading record'),scan_core:tr('扫描核心文件','Scanning core files'),scan_plugins:tr('扫描插件文件','Scanning plugins'),scan_packages:tr('读取包信息','Reading packages'),scan_installed_files:tr('核对包文件','Comparing package files'),scan_configuration:tr('核对配置','Comparing configuration')};
 async function poll(){if(finished||!host.isConnected)return;elapsed.textContent=tr('已用时 ','Elapsed ')+Math.floor((Date.now()-started)/1000)+tr(' 秒',' seconds');try{if(identity){const value=await request('/comparison-progress/'+identity);if(finished)return;stage.textContent=phases[value.phase]||tr('正在检查文件','Checking files');current.textContent=value.current||'';if(Number.isFinite(value.total)&&value.total>0){meter.max=value.total;meter.value=value.done||0;}else meter.removeAttribute('value');}}catch{/* The main comparison request reports errors. */}if(!finished&&host.isConnected)timer=setTimeout(poll,1500);}
 if(identity)timer=setTimeout(poll,500);
 try{const payload={content_only:true,comparison_mode:comparisonMode,...(identity?{progress_id:identity}:{})};const plan=await requestStatePlan(request,recordId,comparisonMode,true,identity?{progress_id:identity}:{});if(host.isConnected&&isCurrent())renderStatePlan(host,plan,tr);}
 catch(error){if(host.isConnected&&isCurrent()){const again=el('button',tr('重新对比','Retry comparison'),'qbb-lh-btn');again.type='button';again.onclick=retry;host.replaceChildren(el('h4',tr('对比未完成','Comparison incomplete')),el('p',error.message,'qv-warning'),again);}}
 finally{finished=true;clearTimeout(timer);}
}
