import {isChineseLocale} from './i18n.js';
import {updateDisplay} from './shared.js';
const state={filter:'all',query:'',sort:'issues',expanded:new Set(),busy:new Set(),initial:null};
export function selectPluginFilter(filter){
 if(!['all','failed','partial','updates','issues'].includes(filter))return;
 state.filter=filter;state.query='';state.initial='plugins';
}
export function renderPluginTable(body,report,updates,done,actions,initial){
 const tr=(a,b)=>isChineseLocale()?a:b;
 if(initial!==state.initial){if(initial==='load')state.filter='failed';if(initial==='updates')state.filter='updates';if(initial==='plugins')state.filter='all';state.initial=initial;}
 const el=(tag,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
 const button=(text,fn)=>{const b=el('button',text);b.type='button';b.className='qbb-lh-btn';b.onclick=async()=>{try{await fn();}catch(e){message.textContent=e.message;}};return b;};
 const key=p=>String(p||'').replaceAll('\\','/').toLowerCase();
 const updateMap=new Map((updates?.all_items || updates?.updates || []).map(u=>[key(u.path),u]));
 const doneMap=new Map(done.map(u=>[key(u.path),u]));
 const rows=(report?.records || report?.attention || []).map(r=>({...r,update:doneMap.get(key(r.path))||updateMap.get(key(r.path)),status:r.status||(r.success?'ok':'failed')}));
 const counts={all:rows.length,failed:rows.filter(r=>r.status==='failed').length,partial:rows.filter(r=>r.status==='partial').length,updates:rows.filter(r=>r.update?.has_update&&!r.update?.done).length};
 counts.issues=counts.failed+counts.partial;
 const checking=!updates||updates.status==='idle'||updates.status==='running';
 body.replaceChildren();
 const header=el('div');header.className='qbb-plugin-heading';header.append(el('h2',tr('插件管理','Plugin management')));body.append(header);
 const grid=el('div');grid.className='qbb-overview-grid';body.append(grid);
 for(const [label,value,filter] of [[tr('已扫描插件','Scanned plugins'),counts.all,'all'],[tr('加载问题','Loading issues'),counts.issues,'issues'],[tr('插件更新','Updates'),updateDisplay(updates),'updates']]){
  const tile=el('button');tile.type='button';tile.className='qbb-overview-tile';tile.append(document.createTextNode(label),el('strong',String(value)));
  tile.onclick=()=>{state.filter=filter;draw();};
  tile.dataset.filter=filter;grid.append(tile);
 }
 const hint=el('p',tr('加载状态来自本次启动；复制或更新完成后，需要重启确认是否成功加载。','Load status reflects this startup. Restart after copying or updating to confirm successful loading.'));hint.className='qbb-page-subtitle';body.append(hint);
 if(updates?.status==='error'||updates?.status==='partial'||updates?.error_count>0){
   const failure=el('p',tr('更新检测未完成，不能判断所有插件是否为最新。请检查网络后点击底部“重新检查”。','Update checks are incomplete. Check the network and retry below.'));
   failure.setAttribute('role','status');body.append(failure);
   const details=el('details');details.append(el('summary',tr('查看检测错误','Show check errors')));
   for(const item of updates.all_items||[])if(item.error&&!item.skipped)details.append(el('p',item.name+' — '+item.error));
   body.append(details);
 }
 const toolbar=el('div');toolbar.className='qbb-plugin-filter';const search=el('input');search.type='search';search.placeholder=tr('搜索插件','Search plugins');search.value=state.query;search.setAttribute('aria-label',search.placeholder);
 const sort=el('select');sort.setAttribute('aria-label',tr('排序','Sort'));for(const [value,text] of [['issues',tr('问题优先','Issues first')],['name',tr('名称 A–Z','Name A–Z')],['updates',tr('更新优先','Updates first')]]){const o=el('option',text);o.value=value;sort.append(o);}sort.value=state.sort;toolbar.append(search,sort);body.append(toolbar);
 const message=el('p');message.setAttribute('role','status');body.append(message);
 const wrap=el('div');wrap.className='qbb-table-scroll';const table=el('table');table.className='qbb-plugin-table';const thead=el('thead');const heading=el('tr');for(const text of [tr('名称','Name'),tr('状态','Status'),tr('版本 / 更新','Version / update'),tr('操作','Actions')])heading.append(el('th',text));thead.append(heading);const tbody=el('tbody');table.append(thead,tbody);wrap.append(table);body.append(wrap);
 const priority=r=>r.status==='failed'?0:r.status==='partial'?1:2;
 function draw(){
  for(const tile of grid.children)tile.setAttribute('aria-pressed',String(tile.dataset.filter===state.filter));
  tbody.replaceChildren();
  const visible=rows.filter(r=>r.name.toLowerCase().includes(state.query.toLowerCase())&&(state.filter==='all'||state.filter===r.status||(state.filter==='updates'&&r.update?.has_update&&!r.update?.done)||(state.filter==='issues'&&(r.status==='failed'||r.status==='partial'))));
  visible.sort((a,b)=>(state.sort==='issues'?priority(a)-priority(b):state.sort==='updates'?Number(!!b.update?.has_update)-Number(!!a.update?.has_update):0)||a.name.localeCompare(b.name));
  for(const row of visible){
   const id=key(row.path);const u=row.update;const line=el('tr');line.append(el('td',row.name));const status=el('td',row.status==='failed'?tr('✕ 加载失败','✕ Failed'):row.status==='partial'?tr('⚠ 部分加载','⚠ Partial'):tr('✓ 正常','✓ Loaded'));status.dataset.status=row.status;line.append(status);
   const version=el('td',u?.done?tr('✓ 更新成功 · 待重启','✓ Updated · restart'):u?.has_update?tr('↑ 有更新','↑ Update available'):u?.error?tr('检查失败','Check failed'):u?.skipped?tr('未检查','Not checked'):u?tr('✓ 最新','✓ Up to date'):tr('尚未检查','Not checked'));
   if(u?.local_sha){const sha=el('small',u.local_sha.slice(0,7));sha.style.display='block';version.append(sha);}line.append(version);
   const ops=el('td');const actionGroup=el('div');actionGroup.className='qbb-plugin-row-actions';ops.append(actionGroup);const more=button(row.status==='ok'?tr('加载详情 ›','Load details ›'):tr('查看问题 ›','View issue ›'),()=>{if(state.expanded.has(id))state.expanded.delete(id);else state.expanded.add(id);draw();});more.setAttribute('aria-expanded',String(state.expanded.has(id)));actionGroup.append(more);
   if(u?.has_update&&!u.done){const update=button(state.busy.has(id)?tr('更新中…','Updating…'):tr('更新','Update'),async()=>{
    if(state.busy.has(id))return;state.busy.add(id);draw();try{const result=await actions.update(u);row.update={...u,...result,done:true,has_update:false};message.textContent=tr('更新成功，重启后确认加载状态。','Updated. Restart to confirm loading.');}catch(e){message.textContent=e.message;}finally{state.busy.delete(id);draw();}
   });update.classList.add('qbb-lh-btn-primary');update.disabled=state.busy.has(id);actionGroup.append(update);}line.append(ops);tbody.append(line);
   if(state.expanded.has(id)){
    const detailRow=el('tr');const cell=el('td');cell.colSpan=4;cell.className='qbb-plugin-detail';cell.append(el('p',row.path));
    if(row.reason)cell.append(el('p',row.reason));
    const trace=[row.traceback,...(row.partial_issues||[])].filter(Boolean).join('\n\n');if(trace)cell.append(el('pre',trace));
    cell.append(button(tr('复制这个问题','Copy issue'),async()=>{await actions.copy(row);message.textContent=tr('已复制','Copied');}));
    if(row.status!=='ok')cell.append(button(tr('AI 分析','AI analysis'),()=>actions.analyse(row)));
    if(u?.error)cell.append(el('p',u.error));detailRow.append(cell);tbody.append(detailRow);
   }
  }
  if(!visible.length){const row=el('tr');const td=el('td',tr('没有符合条件的插件','No matching plugins'));td.colSpan=4;row.append(td);tbody.append(row);}
 }
 search.oninput=()=>{state.query=search.value;draw();};sort.onchange=()=>{state.sort=sort.value;draw();};draw();
}
