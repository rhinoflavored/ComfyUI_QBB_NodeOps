import {makeDialogDraggable} from './dialog-drag.js';
import {api} from '../../scripts/api.js';
import {isChineseLocale} from './i18n.js';
import {mountRebuild} from './rebuild.js';
import {loadStatePlan} from './state-plan.js';
import {mountCurrentRestore} from './current-restore.js';
import {runCaptureTask,watchCaptureTask,taskRequest,rememberedTask,rememberTask,renderCaptureTask} from './task-view.js';
import './http.js';
if(!document.querySelector('link[data-qbb-versions]')){const style=document.createElement('link');style.rel='stylesheet';style.dataset.qbbVersions='';style.href=new URL('./versions.css',import.meta.url).href;document.head.append(style);}
export function renderSnapshots(host,left,right,options={}){
 const embedded=!!options.embedded;
 host.replaceChildren();if(left)left.replaceChildren();if(right)right.replaceChildren();
 const tr=(a,b)=>isChineseLocale()?a:b;
 const el=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
 const btn=(text,fn)=>{const n=el('button',text,'qbb-lh-btn');n.type='button';n.onclick=fn;return n;};
 const root=el('section','','qbb-versions');host.append(root);
 let items=[],timer,working=false,externalBusy=false,previewData=null,previewDeadline=0,previewTimer;
 const previewExpired=()=>previewData&&Date.now()>=previewDeadline;
 const captureError=error=>error==='Preview again before saving'?tr('预览已过期或失效，请重新预览后确认保存。','Preview expired or invalid. Preview again, then confirm saving.'):error;
 async function request(path='',payload,force=false){const r=await api.fetchApi('/qbb_nodeops/version-records'+path,payload?{timeoutMs:path.endsWith('/recovery-plan')?600000:60000,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{cache:force?'reload':'default'});const data=await r.json();if(!r.ok)throw Error(data.error||r.statusText);return data;}
 const heading=el('header','','qv-heading');const intro=el('div');intro.append(el(embedded?'h3':'h2',embedded?tr('环境版本记录','Environment records'):tr('版本记录','Version history')));
 const composer=el('form','','qv-composer');composer.hidden=!embedded;const name=el('input');name.placeholder=tr('备注，例如：升级 Torch 前（可选）','Label, e.g. before updating Torch (optional)');name.setAttribute('aria-label',tr('记录备注','Record label'));
 const createToggle=btn(tr('＋ 创建记录','＋ Create record'),()=>{composer.hidden=!composer.hidden;createToggle.setAttribute('aria-expanded',String(!composer.hidden));if(!composer.hidden)name.focus();});
 createToggle.setAttribute('aria-expanded',String(!composer.hidden));
 createToggle.textContent=tr('＋ 创建记录','＋ Record running instance');
 intro.append(el('p',tr('保存当前环境，在更新或调整依赖前留一个检查点。','Save the current environment before updating or changing dependencies.'),'qv-page-description'));
 if(embedded)heading.append(intro);else heading.append(intro,createToggle);root.append(heading);
 const mode=el('select');mode.setAttribute('aria-label',tr('文件保存方式','File capture mode'));
 mode.hidden=true;
 for(const [value,label] of [['auto',tr('来源分类与本地资源去重','Sources and deduplicated local resources')]]){const o=el('option',label);o.value=value;mode.append(o);}
 const large=el('select');large.setAttribute('aria-label',tr('超过 256 MiB 的文件','Files larger than 256 MiB'));
 for(const [value,label] of [['exclude',tr('大文件：跳过并列出','Large files: exclude and list')],['include',tr('大文件：一起保存','Large files: include')],['error',tr('大文件：阻止保存','Large files: block saving')]]){const o=el('option',label);o.value=value;large.append(o);}
 large.value='include';
 const purpose=el('select');purpose.setAttribute('aria-label',tr('记录用途','Record purpose'));
 const operationNames={update:tr('更新','Update'),dependencies:tr('依赖调整','Dependency changes'),cleanup:tr('清理','Cleanup'),other:tr('其他操作','Other operation')};
 const ordinary=el('option',tr('普通版本记录','Ordinary record'));ordinary.value='';purpose.append(ordinary);
 for(const [key,label] of Object.entries(operationNames))for(const [phase,text] of [['before',tr('前检查点',' — before')],['after',tr('后检查点',' — after')]]){const option=el('option',label+text);option.value=key+':'+phase;purpose.append(option);}
 const related=el('select');related.hidden=true;related.setAttribute('aria-label',tr('关联操作前记录','Related before-operation record'));
 function relatedOptions(){const previous=related.value;related.replaceChildren();const empty=el('option',tr('选择对应的操作前检查点','Select the corresponding before checkpoint'));empty.value='';related.append(empty);for(const item of items.filter(i=>i.operation_checkpoint?.phase==='before'&&i.operation_checkpoint.operation===purpose.value.split(':')[0])){const option=el('option',(item.label||item.id)+' · '+new Date(item.created*1000).toLocaleString());option.value=item.id;related.append(option);}related.value=items.some(i=>i.id===previous&&i.operation_checkpoint?.phase==='before'&&i.operation_checkpoint.operation===purpose.value.split(':')[0])?previous:'';}
 purpose.onchange=()=>{related.hidden=!purpose.value.endsWith(':after');relatedOptions();invalidate();};related.onchange=invalidate;
 const acknowledgement=el('input');acknowledgement.type='checkbox';acknowledgement.setAttribute('aria-label',tr('确认保存范围','Confirm capture scope'));
 const ackLabel=el('label','','qv-preview-ack');ackLabel.append(acknowledgement,document.createTextNode(tr('已核对排除项和空间估算，并已停止修改文件。','I reviewed exclusions and disk estimates and stopped file changes.')));
 const previewPanel=el('div','','qv-capture-preview');previewPanel.setAttribute('role','status');
 const scopePanel=el('div');scopePanel.hidden=true;
 const scopeNote=el('p',tr('保存核心、插件、Python 包文件与 extra_model_paths.yaml；不包含用户数据和外部资源。','Saves core, plugins, Python package files and extra_model_paths.yaml; excludes user data and external resources.'),'qv-scope-note');
 const errorPolicy=el('select');errorPolicy.setAttribute('aria-label',tr('遇到错误时','When errors occur'));
 for(const [value,label] of [['strict',tr('记录资源缺口','Record resource gaps')]]){const option=el('option',label);option.value=value;errorPolicy.append(option);}errorPolicy.value='strict';
 const errorLabel=el('label',tr('遇到错误时','When errors occur'));errorLabel.append(errorPolicy);scopePanel.append(errorLabel);
 const captureOptions=()=>({mode:'auto',large_files:'include',on_error:'strict',core_dirs:[]});
 const advanced=el('details','','qv-capture-help');advanced.append(el('summary',tr('高级选项 · 大文件处理','Advanced · Large files')),large);
 advanced.hidden=true;errorLabel.hidden=true;scopePanel.append(advanced);
 function controls(){save.disabled=working||externalBusy||!previewData?.can_save||previewExpired()||!acknowledgement.checked||(purpose.value.endsWith(':after')&&!related.value);previewButton.disabled=working||externalBusy;purpose.disabled=related.disabled=errorPolicy.disabled=mode.disabled=large.disabled=acknowledgement.disabled=working;ackLabel.hidden=!previewData;saveHint.hidden=!!previewData;createToggle.disabled=working||externalBusy;}
 function invalidate(){clearTimeout(previewTimer);previewData=null;previewDeadline=0;acknowledgement.checked=false;previewPanel.replaceChildren();controls();}
 function updatePreviewValidity(){
   clearTimeout(previewTimer);if(!root.isConnected||!previewData)return;
   const expired=previewExpired();
   validity.textContent=expired?tr('预览已过期，请重新点击“预览保存内容”。','Preview expired. Click Preview capture again.'):tr('扫描完成后预览有效期 30 分钟。剩余约 ','Preview is valid for 30 minutes after scanning. About ')+Math.ceil((previewDeadline-Date.now())/60000)+tr(' 分钟。',' minutes remaining.');
   if(expired){acknowledgement.checked=false;save.disabled=true;}else previewTimer=setTimeout(updatePreviewValidity,1000);
 }
 mode.onchange=large.onchange=invalidate;acknowledgement.onchange=controls;
 const previewButton=btn(tr('预览保存内容','Preview capture'),async()=>{
   if(working)return;working=true;invalidate();status.textContent='';previewStep.insertBefore(captureTask,previewPanel);captureTask.replaceChildren();
   try{
     const task=await runCaptureTask(captureTask,'preview',{options:captureOptions()},tr);
     if(task?.status==='completed')showPreview(task.result);
     else if(task)throw Error(task.error||tr('预览未完成','Preview incomplete'));
   }catch(e){previewPanel.replaceChildren(el('p',e.message));}finally{working=false;controls();}
 });
 const validity=el('p','','qv-warning');
 function showPreview(data){
     // Count from request start conservatively; polling must not extend the server token's lifetime.
     captureTask.replaceChildren();previewData=data;previewDeadline=Number.isFinite(data.expires_at)?data.expires_at*1000:Date.now()+Math.min(data.expires_in||600,1800)*1000;previewPanel.replaceChildren();status.textContent='';previewPanel.append(validity);updatePreviewValidity();
     const mib=b=>(b/1048576).toFixed(1)+' MiB';
     for(const scope of data.scopes||[])previewPanel.append(el('p',scope.name+' · '+(scope.selected?scope.files+tr(' 个文件 · ',' files · ')+mib(scope.bytes):tr('未选择，不保存','Not selected, not saved'))));
     previewPanel.append(el('p',data.files+tr(' 个文件 · 去重前内容 ',' files · Before deduplication ')+mib(data.bytes_upper_bound)+tr(' · 可用空间 ',' · Free space ')+mib(data.free_bytes)));
     if(data.space){const metrics=el('div','','qv-metrics');for(const [label,value] of [[tr('预计新增对象 ','New objects '),data.space.new_bytes],[tr('复用已有对象 ','Reused objects '),data.space.reused_bytes],[tr('所需可用空间（含余量）','Required free space (with reserve) '),data.required_bytes]])metrics.append(el('p',label+mib(value),'qv-metric'));previewPanel.append(metrics);}
     if(!data.can_save)previewPanel.append(el('p',tr('暂不能保存：请先处理下列错误，再重新预览；勾选确认不会绕过错误。','Cannot save: resolve the errors below and preview again. Acknowledgement does not bypass errors.'),'qv-warning'));
     const excluded=data.items.reduce((total,item)=>total+item.skipped.length,0);
     if(excluded)previewPanel.append(el('p',tr('有 ','There are ')+excluded+tr(' 项排除内容：请展开下方明细核对。缓存、未选范围和真实缺项的影响不同。',' exclusions: inspect the details below. Caches, unselected scope and missing content have different implications.'),'qv-warning'));
     for(const error of data.errors)previewPanel.append(el('p',error,'qv-warning'));
     const details=el('details');details.open=!data.can_save;details.append(el('summary',tr('查看组件、排除项和错误','Inspect components, exclusions and errors')));
     for(const item of data.items){
       const row=el('details');row.append(el('summary',item.name+' · '+({snapshot:tr('文件快照','Files'),git:tr('仅 Git 信息','Git metadata'),skip:tr('跳过','Skipped'),metadata:tr('仅版本信息','Metadata only')}[item.method])+' · '+item.files+tr(' 个文件',' files')+' · '+mib(item.bytes)));
       if(item.recoverability)row.append(el('p',item.recoverability.status+(item.recoverability.reason?' — '+item.recoverability.reason:'')));
       for(const entry of [...item.skipped,...item.errors])row.append(el('p',entry.path+' — '+entry.reason));
       details.append(row);
     }
     previewPanel.append(details);
 }
 errorPolicy.onchange=()=>{invalidate();previewButton.onclick();};
 const save=btn(tr('确认保存记录','Confirm save'),create);save.classList.add('qbb-lh-btn-primary');
 const previewStep=el('section','','qv-capture-step qv-preview-step');previewStep.append(el('h4',tr('1 · 预览内容与空间','1 · Preview content and space')),el('p',tr('检查文件清单、排除项和所需空间后，再确认保存。','Review files, exclusions and required space before saving.'),'qv-step-description'),previewButton,previewPanel);
 const saveHint=el('p',tr('先预览保存内容，核对后即可保存。','Preview the capture first, then review and save.'),'qv-save-hint');
 const saveStep=el('section','','qv-capture-step qv-save-step');const saveReview=el('div');saveReview.append(el('h4',tr('2 · 确认并保存','2 · Confirm and save')),saveHint,ackLabel);saveStep.append(saveReview,save);
 const nameLabel=el('label','','qv-name-field');nameLabel.append(el('span',tr('记录备注','Record label')),name);name.placeholder=tr('例如：升级 Torch 前（可选）','e.g. before updating Torch (optional)');
 const composerHeading=el('div','','qv-composer-heading');composerHeading.append(el('h3',tr('创建版本记录','Create version record')),el('span',tr('当前运行实例','Running instance'),'qv-tag'));
 const captureGrid=el('div','','qv-capture-grid');captureGrid.append(previewStep);
 composer.append(composerHeading,nameLabel,scopeNote,mode,purpose,related,scopePanel,captureGrid,saveStep);
 purpose.hidden=true;
 composer.onsubmit=e=>{e.preventDefault();create();};root.append(composer);controls();
 const status=el('p','','qv-status');status.setAttribute('role','status');root.append(status);
 const captureTask=el('section','','qv-task qv-capture-task');previewStep.insertBefore(captureTask,previewPanel);
 const saveTask=el('section','','qv-task qv-capture-task');composer.append(saveTask);
 const restoreResume=btn(tr('查看恢复任务','View restore task'),()=>openAction({id:restoreResume.recordId,schema:4,label:restoreResume.label},'restore'));restoreResume.hidden=true;root.append(restoreResume);
 const storage=el('details','','qv-help');storage.append(el('summary',tr('保存位置','Storage location')));
 const storagePath=el('input');storagePath.className='qbb-stretch';storagePath.setAttribute('aria-label',tr('版本记录保存文件夹','Version records folder'));
 const storageRow=el('div','','qv-path-picker');storage.append(storageRow);storageRow.append(el('span',tr('记录目录','Records folder')),storagePath,btn(tr('浏览文件夹','Browse folders'),async()=>{try{const r=await api.fetchApi('/qbb_nodeops/instances/choose-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();if(!r.ok)throw Error(d.error);if(d.path)storagePath.value=d.path;}catch(e){status.textContent=e.message;}}));storage.append(btn(tr('保存位置','Save location'),async()=>{try{const result=await request('/storage',{directory:storagePath.value.trim()});invalidate();await refresh();status.textContent=tr('已切换保存位置。旧记录仍在：','Storage changed. Old records remain in: ')+result.previous;}catch(e){status.textContent=e.message;}}));
 request('/storage').then(d=>{storagePath.value=d.directory;}).catch(e=>{status.textContent=e.message;});
 const toolbar=el('div','','qv-toolbar');const count=el('span');const search=el('input');search.type='search';search.placeholder=tr('搜索备注或记录编号','Search labels or IDs');search.setAttribute('aria-label',search.placeholder);search.oninput=draw;toolbar.append(count,search,btn(tr('刷新','Refresh'),()=>refresh(true)));root.append(toolbar);

 storage.classList.add('qv-storage-top');root.insertBefore(storage,toolbar);
 const list=el('div','','qv-list qv-record-list');root.append(list);
 function draw(){
   relatedOptions();controls();
   list.replaceChildren();count.textContent=tr('历史记录 · ','History · ')+items.length+' '+tr('份记录','records');
   const visible=items.filter(i=>((i.label||'')+' '+i.id).toLowerCase().includes(search.value.toLowerCase()));
   if(!visible.length)list.append(el('p',items.length?tr('没有匹配的记录，请尝试其他关键词。','No matching records. Try another search.'):tr('暂无版本记录。创建一份记录，为下一次环境调整保留检查点。','No records yet. Create a checkpoint before your next environment change.'),'qv-empty'));
   for(const item of visible){
     const row=el('article','','qv-record-card');
     const top=el('div','','qv-row');
     top.append(el('strong',item.label||tr('未命名记录','Untitled record')));
     if(item.id===items[0]?.id)top.append(el('span',tr('最新','Latest'),'qv-tag'));
     const checkpoint=item.operation_checkpoint;
     if(checkpoint)top.append(el('span',(operationNames[checkpoint.operation]||tr('操作','Operation'))+(checkpoint.phase==='before'?tr('前检查点',' — before'):tr('后检查点',' — after')),'qv-tag'));
     else if(item.automatic_checkpoint)top.append(el('span',tr('定时检查点','Periodic checkpoint'),'qv-tag'));
     const time=el('time',new Date(item.created*1000).toLocaleString(),'qv-muted');
     time.dateTime=new Date(item.created*1000).toISOString();
     const actions=el('div','','qv-record-actions');
     actions.append(btn(tr('对比','Compare'),()=>openAction(item,'compare')),
       btn(tr('恢复','Restore'),()=>openAction(item,'restore')),
       btn(tr('导出清单','Export manifest'),()=>exportRecord(item)),
       btn(tr('删除','Delete'),()=>deleteRecord(item)));
     if(checkpoint?.phase==='after'){const before=items.find(i=>i.id===checkpoint.before_record_id);if(before)actions.append(btn(tr('查看操作前后变化','Compare before / after'),()=>openAction(before,'compare',item.id)));}
     const metadata=[];
     if(item.offline_environment)metadata.push(tr('离线 Python / 依赖已准备','Offline Python / dependencies ready'));
     if(item.python)metadata.push('Python '+item.python);
     if(Number.isFinite(item.plugins))metadata.push(item.plugins+' '+tr('插件','plugins'));
     if(Number.isFinite(item.packages))metadata.push(item.packages+' '+tr('依赖','packages'));
     const bottom=el('div','','qv-record-bottom');
     if(metadata.length)bottom.append(el('small',metadata.join(' · '),'qv-record-summary'));
     const coverage=el('div','','qv-coverage');const content=item.content_summary;
     const scopeNames={code:tr('核心与插件','Core and plugins'),user:tr('配置与工作流','Settings and workflows'),models:tr('模型','Models'),input:tr('输入','Input'),output:tr('输出','Output'),python_environment:tr('Python 环境','Python environment')};
     const scopeStates={recorded:tr('状态已记录','State recorded'),package_management_only:tr('包管理级记录','Package-level record'),installed_files_saved:tr('受管包文件已保存','Managed package files saved'),installed_files_partial:tr('包文件有缺口','Package file gaps'),complete_within_policy:tr('范围内完整清单','Full inventory within scope'),metadata_only:tr('仅版本信息','Metadata only'),partial:tr('有缺项 / 不完整','Partial / gaps'),captured:tr('已保存','Captured'),absent:tr('源目录不存在','Source absent'),not_selected:tr('未选择','Not selected'),not_saved:tr('未保存','Not saved'),offline_assets_recorded:tr('离线资源已记录','Offline assets recorded'),unknown:tr('未知','Unknown')};
     for(const [key,label] of Object.entries(scopeNames)){if(item.schema===4&&!['code','python_environment'].includes(key))continue;const state=content?.scopes?.[key]?.status||'unknown';coverage.append(el('span',label+' · '+(scopeStates[state]||scopeStates.unknown),'qv-scope-status'+(['partial','metadata_only'].includes(state)?' qv-warning':'')));}
     const sizes=content?.sizes;const sizeText=b=>Number.isFinite(b)?(b/1048576).toFixed(1)+' MiB':tr('未知','Unknown');
     const sizeNote=el('p',tr('文件总量 ','Logical files ')+sizeText(sizes?.logical_bytes)+tr(' · 引用的去重对象 ',' · Referenced unique objects ')+sizeText(sizes?.unique_referenced_bytes),'qv-muted');
     bottom.append(actions);row.append(top,time,coverage,sizeNote,bottom);list.append(row);
     if(checkpoint?.phase==='after'&&!items.some(i=>i.id===checkpoint.before_record_id))metadataMissing(row);
   }
   if(!visible.length)list.append(el('p',items.length?tr('没有匹配的记录','No matching records'):tr('还没有记录，点击“创建记录”保存当前版本。','No records yet. Create a record to save the current version.'),'qv-empty'));
 }
 async function deleteRecord(item){
   if(working||externalBusy)return;
   working=true;controls();
   try{
     if(!await confirmDelete(item))return;
     await request('/archive/remove',{record_id:item.id,confirmed:true});
     await refresh(true);status.textContent=tr('记录已移入回收站。','Record moved to recycle bin.');
   }catch(error){status.textContent=error.message;}
   finally{working=false;controls();}
 }
 function confirmDelete(item){
   return new Promise(resolve=>{
     const previousFocus=document.activeElement;let accepted=false,closed=false;
     const dialog=el('dialog','','qv-action-dialog');dialog.setAttribute('aria-label',tr('删除版本记录','Delete version record'));
     const heading=el('header','','qv-heading');heading.append(el('h3',tr('删除版本记录？','Delete version record?')));
     const body=el('div','','qv-dialog-body');const actions=el('div','','qv-record-actions');
     const cancel=btn(tr('取消','Cancel'),()=>dialog.close());
     const remove=btn(tr('删除记录','Delete record'),()=>{if(closed)return;accepted=true;dialog.close();});remove.classList.add('qbb-lh-btn-primary');remove.disabled=false;
     const message=el('p',tr('删除记录“','Delete record “')+(item.label||item.id)+tr('”？记录将移入回收站，不删除当前环境或共享备份文件。','”? The record will move to the recycle bin; the current environment and shared backup files are kept.'));
     actions.append(cancel,remove);body.append(message,actions);dialog.append(heading,body);root.append(dialog);
     dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close();});
     dialog.addEventListener('keydown',event=>{if(event.key==='Escape')event.stopPropagation();});
     dialog.addEventListener('close',()=>{closed=true;dialog.remove();if(previousFocus?.isConnected)previousFocus.focus();resolve(accepted);});
     dialog.showModal();cancel.focus();

   });
 }
 async function exportRecord(item){
   try{
     status.textContent=tr('正在导出清单…','Exporting manifest…');
     const data=await request('/'+encodeURIComponent(item.id));
     const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
     const anchor=el('a');anchor.href=url;anchor.download='qbb-version-'+item.id+'.json';
     anchor.click();status.textContent=tr('已导出清单。','Manifest exported.');setTimeout(()=>URL.revokeObjectURL(url),1000);
   }catch(error){status.textContent=error.message;}
 }
 function metadataMissing(row){row.append(el('p',tr('关联的操作前记录不在当前存储位置；无法比较。','The linked before checkpoint is not in this store; comparison is unavailable.'),'qv-warning'));}
 function openAction(item,mode,otherRecordId=null){
   const previousFocus=document.activeElement;
   const dialog=el('dialog','','qv-action-dialog');
   const title=(mode==='compare'?tr('对比','Compare'):tr('恢复','Restore'))+' · '+(item.label||tr('未命名记录','Untitled record'));
   dialog.setAttribute('aria-label',title);if(mode==='compare'||mode==='restore')dialog.classList.add('qv-compare-dialog');
   const heading=el('header','','qv-heading');
   heading.append(el('h3',title),btn(tr('关闭','Close'),()=>dialog.close()));
   const content=el('div');content.className=mode==='restore'?'qv-restore-page':'qv-compare-page';const body=el('div','','qv-dialog-body');body.append(content);dialog.append(heading,body);root.append(dialog);
   let dispose=()=>{};const stopDragging=(mode==='compare'||mode==='restore')?makeDialogDraggable(dialog,heading):()=>{};
   dialog.addEventListener('close',()=>{stopDragging();dispose();dialog.remove();if(previousFocus?.isConnected)previousFocus.focus();});
   dialog.addEventListener('keydown',event=>{if(event.key==='Escape')event.stopPropagation();});
   const mount=()=>{dispose();content.replaceChildren();if(mode==='restore'){if(item.schema===4)dispose=mountCurrentRestore(content,item.id,tr,{onState:restoreNotice,onDone:()=>dialog.close()});else content.append(el('p',tr('旧格式记录不支持严格的当前实例恢复。可继续对比、查看和导出；请使用 schema 4 记录恢复。','Legacy records do not support strict current-instance restoration. Comparison and export remain available; restore requires schema 4.')));return;}if(item.schema===4){loadStatePlan(content,request,item.id,tr);return;}mountRebuild(content,item.id,{autoCheck:true,mode,otherRecordId});};
   if(mode==='compare'&&item.schema!==4){const select=el('select');select.setAttribute('aria-label',tr('比较终点','Comparison destination'));const current=el('option',tr('当前运行实例','Running instance'));current.value='';select.append(current);for(const record of items.filter(r=>r.id!==item.id&&r.schema!==4)){const option=el('option',(record.label||record.id)+' · '+new Date(record.created*1000).toLocaleString());option.value=record.id;select.append(option);}select.value=otherRecordId||'';select.onchange=()=>{otherRecordId=select.value||null;mount();};body.insertBefore(select,content);}
   dialog.showModal();mount();
 }
 async function refresh(force=false){clearTimeout(timer);try{const data=await request('',undefined,force);if(!root.isConnected)return;items=data.items;externalBusy=!!data.running;controls();draw();if(data.running){status.textContent=tr('后台创建中，可关闭面板；请勿退出 ComfyUI。','Recording in the background. You may close this panel; keep ComfyUI running.');timer=setTimeout(refresh,2000);}}catch(e){status.textContent=e.message;}}
 async function create(){
   if(previewExpired()){updatePreviewValidity();return;}
   if(working||save.disabled)return;
   working=true;controls();status.textContent=tr('正在保存，请勿修改文件或退出 ComfyUI。','Saving; do not edit files or exit ComfyUI.');
   try{composer.append(saveTask);const task=await runCaptureTask(saveTask,'create',{label:name.value,options:captureOptions(),preview_id:previewData.preview_id,confirmed:true},tr);if(task?.status==='completed'){await refresh(true);if(!embedded){composer.hidden=true;createToggle.setAttribute('aria-expanded','false');}name.value='';status.textContent='';captureDone(task);}else if(task)status.textContent=captureError(task.error)||tr('未确认保存完成','Save completion unconfirmed');}
   catch(e){status.textContent=captureError(e.message);}
   finally{working=false;invalidate();}
 }
 function captureDone(task){root.append(saveTask);renderCaptureTask(saveTask,task,tr);saveTask.append(btn(tr('完成','Done'),()=>{saveTask.replaceChildren();rememberTask('capture-dismissed',task.id);}));}
 function restoreNotice(task){restoreResume.hidden=!task?.id;restoreResume.recordId=task?.record_id;restoreResume.label=task?.target_label||'';if(task?.id)rememberTask('restore',task.id);}
 async function reconnectTasks(){
   let claimed=false;
   try{const task=await taskRequest('/record-tasks');if(working||!root.isConnected||task.status==='idle'||task.id===rememberedTask('capture-dismissed'))return;if(task.status==='completed'){if(task.action==='create')captureDone(task);return;}claimed=true;working=true;composer.hidden=false;previewStep.insertBefore(captureTask,previewPanel);controls();const target=task.action==='create'?saveTask:captureTask;if(task.action==='create')composer.append(saveTask);const result=await watchCaptureTask(target,task.id,tr);if(result?.status==='completed'){if(result.action==='preview'){composer.hidden=false;showPreview(result.result);}else{captureDone(result);await refresh(true);}}}
   catch(e){if(root.isConnected)captureTask.append(el('p',tr('无法重连创建任务：','Cannot reconnect capture task: ')+e.message,'qv-warning'));}
   finally{if(claimed){working=false;controls();}}
 }
 taskRequest('/current-restore').then(task=>{if(root.isConnected)restoreNotice(task);}).catch(()=>{});
 reconnectTasks();
 refresh();
}
