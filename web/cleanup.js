import {api} from '../../scripts/api.js';
import {isChineseLocale} from './i18n.js';
import './http.js';
export function renderCleanup(host, footerRight){
 const tr=(a,b)=>isChineseLocale()?a:b, el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
 host.classList.add('qbb-cleanup');
 const intro=el('p',tr('先按时间筛选，再核对常用工作流，确认不再需要后清理。','Filter by age, review your workflows, then remove resources you no longer need.'));intro.className='qbb-cleanup-intro';host.append(intro);
 const help=el('details');help.className='qbb-cleanup-help';help.append(el('summary',tr('时间与使用记录如何判断？','How are dates and usage recorded?')));
 help.append(el('p',tr('本地创建是文件系统时间，复制、解压或迁移可能改变它，不等于准确安装时间。文件修改、Git 提交也不等于发布时间；资源较旧不代表无用。','Local creation is a filesystem timestamp that can change after copying, extraction or migration, not an exact installation date. Modification and Git commit dates are not release dates. Older does not mean unused.')));
 help.append(el('p',tr('这里只记录提交工作流时识别到的引用，不代表执行成功。模型按完整路径匹配，同名歧义不猜测；插件内部加载、前端插件可能漏记。新版独立累计，旧记录保留但不用于清理判断。','Records identifiable submitted workflow references, not successful execution. Models use full paths; ambiguous names are not guessed. Internal loaders and frontend plugins may be missed. New tracking starts independently; legacy data is retained but excluded from cleanup decisions.')));host.append(help);
 const toolbar=el('div');toolbar.className='qbb-plugin-filter';host.append(toolbar);
 function select(options){const s=el('select');s.style.padding='8px';for(const [v,t] of options){const o=el('option',t);o.value=v;s.append(o);}toolbar.append(s);return s;}
 const kind=select([['plugin',tr('插件','Plugins')],['model',tr('模型','Models')]]);
 const days=select([['30',tr('30 天','30 days')],['60',tr('60 天','60 days')],['90',tr('90 天','90 days')],['180',tr('180 天','180 days')]]);
 const scope=select([['old',tr('已下载','Downloaded for')],['idle',tr('未使用','Unused for')],['all',tr('全部','All')]]);
 const category=select([['all',tr('全部模型类型','All model types')]]);
 category.hidden=true;
 const sort=select([['age',tr('本地时间：旧到新','Local age: oldest first')],['size',tr('体积：大到小','Size: largest first')],['usage',tr('使用记录：最久优先','Oldest usage first')],['update',tr('Git 提交：旧到新','Git commit: oldest first')]]);
 const timeFilter=el('div');timeFilter.className='qbb-time-filter';timeFilter.setAttribute('role','group');timeFilter.setAttribute('aria-label',tr('时间筛选','Time filter'));
 scope.setAttribute('aria-label',tr('时间条件','Time condition'));days.setAttribute('aria-label',tr('天数','Days'));
 const suffix=el('span',tr('以上','or more'));suffix.className='qbb-time-suffix';
 timeFilter.append(scope,days,suffix);toolbar.append(timeFilter);
 function syncTimeFilter(){
  const all=scope.value==='all';days.hidden=all;days.disabled=all;
  suffix.hidden=all;
 }
 syncTimeFilter();
 const search=el('input');search.placeholder=tr('筛选名称','Filter by name');toolbar.append(search);
 const summary=el('p');summary.setAttribute('role','status');host.append(summary);
 const list=el('div');host.append(list);
 const remove=el('button',tr('删除所选（移入回收站）','Recycle selected'));remove.className='qbb-lh-btn qbb-lh-btn-primary';remove.disabled=true;
 if(footerRight)footerRight.append(remove);else host.append(remove);
 const selected=new Set();let data=null,busy=false;
 function draw(){
  list.replaceChildren();selected.clear();remove.disabled=true;
  if(!data)return;
  const categories=[...new Set(data.items.filter(i=>i.kind==='model').map(i=>i.category).filter(Boolean))].sort();
  const categoryLabel=value=>({plugin_models:tr('插件专用模型','Plugin models'),loras:'LoRA',diffusion_models:'Diffusion Model',checkpoints:'Checkpoint',vae:'VAE',controlnet:'ControlNet',clip:'CLIP / Text Encoder',text_encoders:'Text Encoder'})[value]||value;
  if(category.options.length===1)for(const value of categories){const o=el('option',categoryLabel(value));o.value=value;category.append(o);}
  category.hidden=category.disabled=kind.value==='plugin';if(category.disabled)category.value='all';
  const threshold=Number(days.value);
  const rows=data.items.filter(item=>{const idle=item.idle_days==null||item.idle_days>=threshold;return kind.value===item.kind&&(category.value==='all'||item.category===category.value)&&item.name.toLowerCase().includes(search.value.toLowerCase())&&(scope.value==='all'||(scope.value==='old'?item.local_age_days>=threshold:idle));});
  const group=i=>i.kind==='plugin'?tr('插件','Plugins'):(categoryLabel(i.category)||tr('其他模型','Other models'));
  rows.sort((a,b)=>group(a).localeCompare(group(b))||(sort.value==='size'?(b.size??-1)-(a.size??-1):sort.value==='usage'?(a.last??Infinity)-(b.last??Infinity):sort.value==='update'?(a.updated??Infinity)-(b.updated??Infinity):(b.local_age_days??-1)-(a.local_age_days??-1))||a.name.localeCompare(b.name));
  let previousGroup=null;
  summary.textContent=tr(`匹配 ${rows.length} 项 · 插件加载累计 ${data.plugin_load_seconds}s`,`Matched ${rows.length} items · Plugin loading ${data.plugin_load_seconds}s`);
  if(!rows.length)list.append(el('p',tr('暂无符合条件的项目。','No matching items.')));
  const dateText=value=>{const d=new Date(value*1000);return value&&Number.isFinite(d.getTime())?d.toLocaleDateString():tr('未记录','Not recorded');};
  for(const item of rows){
   if(group(item)!==previousGroup){previousGroup=group(item);const heading=el('h4',previousGroup);heading.className='qbb-cleanup-group';list.append(heading);}
   const row=el('div');row.className='qbb-cleanup-row';
   const check=el('input');check.type='checkbox';check.disabled=item.protected||busy;check.setAttribute('aria-label',item.name);check.onchange=()=>{if(check.checked)selected.add(item.id);else selected.delete(item.id);remove.disabled=busy||!selected.size;};
   const content=el('div');content.className='qbb-cleanup-content';
   const detail=el('details');detail.className='qbb-cleanup-detail';
   const title=el('summary');const name=el('span',item.name);name.className='qbb-resource-name';title.append(name);
   {
    let text=tr('大小未知','Size unknown');
    if(typeof item.size==='number'&&Number.isFinite(item.size)&&item.size>=0){
     const units=['B','K','M','G','T'];let value=item.size,index=0;
     while(value>=1024&&index<units.length-1){value/=1024;index++;}
     text=value.toLocaleString(undefined,{maximumFractionDigits:index?2:0})+' '+units[index];
    }
    const size=el('span',text);size.className='qbb-resource-size';size.setAttribute('aria-label',tr('大小：','Size: ')+text);
    size.title=({linked:tr('包含链接或共享路径，不估算体积','Contains linked/shared paths; size not estimated'),scan_limit:tr('目录扫描达到上限，大小未知','Directory scan limit reached; size unknown'),unavailable:tr('部分文件无法读取，大小未知','Files unavailable; size unknown')})[item.size_status]||tr('文件逻辑大小；文件夹包含内部文件，缓存最多 60 秒','Logical file size; folders include contents, cached up to 60 seconds');title.append(size);
   }
   detail.append(title,el('pre',item.path));
   if(item.plugin_name)detail.append(el('p',tr('所属插件：','Plugin: ')+item.plugin_name));
   const meta=el('p');meta.className='qbb-cleanup-usage';meta.textContent=tr('观察 ','Observed ')+item.observed_days+tr(' 天 · ',' days · ')+item.count+tr(' 次引用',' references')+(item.size!=null?' · '+(item.size/1024/1024/1024).toFixed(2)+' GB':'')+(item.updated?' · Git '+dateText(item.updated):'');detail.append(meta);
   const dates=el('dl');dates.className='qbb-cleanup-dates';
   for(const [label,value,hint] of [
    [tr('最近工作流引用','Last workflow reference'),item.last?dateText(item.last):tr('没有观察记录','No observation record'),item.last?'':tr('未记录到工作流引用，不代表从未使用。','No workflow reference recorded; this does not mean never used.')],
    [tr('本地创建','Local creation'),dateText(item.local_created),tr('文件系统时间，复制或解压可能改变；不等于准确安装时间。','Filesystem timestamp; copying or extraction can change it. Not an exact installation date.')],
    [tr('文件修改','File modified'),dateText(item.local_modified),''],
   ]){const pair=el('div');const term=el('dt',label);if(hint)term.title=hint;pair.append(term,el('dd',value));dates.append(pair);}
   detail.append(el('p',tr('发布时间：尚未获取','Release date: not available')));
   content.append(detail,dates);row.append(check,content);list.append(row);
  }
 }
 async function load(){summary.textContent=tr('读取使用记录…','Reading usage…');try{const r=await api.fetchApi('/qbb_nodeops/health',{cache:'reload'});if(!r.ok)throw new Error(r.statusText);data=await r.json();if(host.isConnected)draw();}catch(e){summary.textContent=e.message;}}
 for(const s of [kind,days,category,sort])s.onchange=draw;
 scope.onchange=()=>{syncTimeFilter();draw();};search.oninput=draw;
 remove.onclick=()=>{if(busy||!selected.size)return;const ids=[...selected];const box=el('section');box.className='qbb-lh-card';box.append(el('h4',tr('确认移入回收站','Confirm recycling')),el('p',tr('这些文件可能仍被工作流或其他实例引用。移入回收站不会卸载 Python 依赖；插件变更后需重启。','These files may be referenced by workflows or other instances. Recycling does not uninstall Python dependencies. Restart after removing plugins.')));for(const row of data.items.filter(i=>selected.has(i.id)))box.append(el('p',row.path));const yes=el('button',tr('确认删除','Confirm deletion'));yes.className='qbb-lh-btn qbb-lh-btn-primary';const no=el('button',tr('取消','Cancel'));no.className='qbb-lh-btn';no.onclick=()=>box.remove();box.append(yes,no);host.append(box);
  yes.onclick=async()=>{if(busy)return;busy=true;yes.disabled=true;remove.disabled=true;try{const r=await api.fetchApi('/qbb_nodeops/health/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids,confirm:true})});const result=await r.json();if(!r.ok)throw new Error(result.error);box.replaceChildren(el('h4',tr('清理结果','Cleanup results')));for(const item of result.results)box.append(el('p',item.name+' · '+(item.ok?tr('已移入回收站，可在回收站恢复','Recycled; restore from Recycle Bin'):item.error)));await load();}catch(e){box.append(el('p',e.message));}finally{busy=false;remove.disabled=!selected.size;}};
 };
 load();
}
