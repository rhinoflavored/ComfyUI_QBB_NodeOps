export function renderRecoveryPlan(host,plan,choices,invalidate,tr){
 const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const labels={recorded:tr('使用记录','Use record'),pending:tr('尚未选择','Not chosen'),skip:tr('跳过','Skip'),backup:tr('使用备份','Use backup'),current:tr('使用当前内容','Use current content')};
 const gaps=plan.repositories.filter(r=>r.gap),plugins=gaps.filter(r=>!r.core),controls=new Map();
 host.append(el('h4',tr('代码与插件','Code and plugins')),el('p',(plan.repositories.length-gaps.length)+tr(' 个组件可按记录恢复',' components can use the record')));
 function component(repo,parent,onChange=()=>{}){
  const row=el('details');row.open=false;const title=el('summary');row.append(title);
  const updateTitle=()=>{title.textContent=repo.name+' · '+(labels[choices[repo.id]?.action||repo.action]||repo.action);};updateTitle();
  row.append(el('p',tr('目标：','Target: ')+repo.target));
  if(repo.gap){
   row.append(el('p',repo.gap));
   const select=el('select');select.setAttribute('aria-label',repo.name+tr('恢复方式',' recovery action'));
   for(const action of ['pending',...(!repo.core?['skip']:[]),'current','backup']){const option=el('option',labels[action]);option.value=action;select.append(option);}
   select.value=choices[repo.id]?.action||repo.action||'pending';
   const input=el('input');input.className='qbb-stretch';input.placeholder=tr('备份组件的绝对路径（文件夹或 .py 文件）','Absolute backup component path (folder or .py file)');input.value=choices[repo.id]?.path||'';input.hidden=select.value!=='backup';
   const change=()=>{choices[repo.id]={action:select.value,path:select.value==='backup'?input.value.trim():''};input.hidden=select.value!=='backup';updateTitle();onChange();invalidate();};
   select.addEventListener('change',change);input.addEventListener('input',change);row.append(select,input);controls.set(repo.id,{select,input,updateTitle});
  }
  parent.append(row);
 }
 for(const core of gaps.filter(r=>r.core)){
  const section=el('section');section.className='qv-recovery-section';section.append(el('h4',tr('核心程序需要补充','Core program needs a source')),el('p',tr('核心不能跳过。展开选择当前内容或提供备份。','The core cannot be skipped. Expand to choose current content or a backup.')));component(core,section);host.append(section);
 }
 if(plugins.length){
  const section=el('section');section.className='qv-recovery-section';section.append(el('h4',tr('需补充的插件','Plugins needing a source')+' · '+plugins.length),el('p',tr('这些插件未保存文件或备份不可用，统一选择处理方式即可。','These plugins have no saved files or unavailable backups. Choose one action for the group.')));
  const batch=el('select');batch.setAttribute('aria-label',tr('缺失插件统一处理','Handle missing plugins together'));
  for(const [value,label] of [['pending',tr('请选择处理方式','Choose an action')],['current',tr('全部使用当前实例的同名插件','Use current matching plugins for all')],['skip',tr('全部跳过这些插件','Skip all these plugins')],['custom',tr('分别设置 / 提供备份（高级）','Individual settings / backups (advanced)')]]){const option=el('option',label);option.value=value;batch.append(option);}
  const advanced=el('details');advanced.open=false;advanced.append(el('summary',tr('高级：查看插件清单或单独调整','Advanced: inspect plugins or adjust individually')));
  const sync=()=>{const actions=new Set(plugins.map(r=>choices[r.id]?.action||r.action||'pending'));const action=actions.size===1?[...actions][0]:'custom';batch.value=['pending','current','skip'].includes(action)?action:'custom';};
  for(const repo of plugins)component(repo,advanced,sync);
  sync();batch.addEventListener('change',()=>{if(batch.value==='custom'){advanced.open=true;return;}for(const repo of plugins){choices[repo.id]={action:batch.value,path:''};const c=controls.get(repo.id);c.select.value=batch.value;c.input.hidden=true;c.input.value='';c.updateTitle();}advanced.open=false;invalidate();});
  section.append(batch,el('p',tr('当前内容不等于历史原件；跳过可能影响工作流。选择后点击“重新检查”，通过后再恢复。','Current content is not the historical original; skipping may affect workflows. Recheck after choosing, then restore.')),advanced);host.append(section);
 }
 const intact=plan.repositories.filter(r=>!r.gap);
 if(intact.length){const details=el('details');details.append(el('summary',tr('查看可直接恢复的组件','View components using the record')+' · '+intact.length));for(const repo of intact)component(repo,details);host.append(details);}
 if(!plan.dependencies){const details=el('details');details.append(el('summary',tr('查看依赖清单','View dependencies')+' · '+plan.pins.length));for(const pin of plan.pins)details.append(el('p',pin));host.append(details);}
}
