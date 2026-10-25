export function renderRecoveryEnvironment(host,plan,choices,invalidate,tr){
 if(!plan.runtime)return;
 const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const options=choices._environment||(choices._environment={python:'auto',dependencies:{}});
 const section=el('section');section.className='qv-recovery-section qv-environment-section';section.append(el('h4',tr('Python 与依赖来源','Python and dependency sources')));
 if(plan.runtime.method==='offline'){
  section.append(el('p',tr('离线恢复：使用记录包内的独立 Python 和 SHA-256 锁定的 wheel，不联网下载。显卡驱动、模型和工作流另行核对。','Offline recovery: bundled independent Python and hash-locked wheels, without downloads. Check drivers, models and workflows separately.')));
  section.append(el('p','Python '+plan.runtime.version+' · '+plan.runtime.architecture+' · '+plan.dependencies.length+tr(' 个依赖',' dependencies')));host.append(section);return;
 }
 section.append(el('p','Python '+plan.runtime.version+' · '+plan.runtime.architecture+' · '+(plan.runtime.method==='managed'?tr('下载同版本 CPython','Download matching CPython'):tr('使用当前匹配解释器','Use matching current interpreter'))));
 const mode=el('select');mode.setAttribute('aria-label','Python');
 for(const [value,title] of [['auto',tr('自动选择','Automatic')],['current',tr('使用当前 Python（必须匹配）','Use current Python (must match)')],['managed',tr('下载同版本独立 Python','Download matching independent Python')]]){const option=el('option',title);option.value=value;mode.append(option);}
 mode.value=options.python;mode.addEventListener('change',()=>{options.python=mode.value;invalidate();});section.append(mode);
 const details=el('details');details.append(el('summary',tr('高级：查看依赖清单或单独调整 · ','Advanced: inspect dependencies or adjust individually · ')+plan.dependencies.length));details.open=false;
 const targets=plan.dependencies.filter(p=>p.action==='pending'||options.dependencies[p.key]);
 const controls=new Map(),batch=el('select');batch.setAttribute('aria-label',tr('依赖来源统一处理','Handle dependency sources together'));
 const indexAction=p=>['torch','torchvision','torchaudio'].includes(p.key)&&/\+(cpu|cu\d+)$/.test(p.version)?'pytorch':'pypi';
 const sync=()=>{const action=p=>options.dependencies[p.key]?.action||p.action;batch.value=targets.every(p=>action(p)==='pending')?'pending':targets.every(p=>action(p)==='skip')?'skip':targets.every(p=>action(p)===indexAction(p))?'index':'custom';};
 if(targets.length){
  section.append(el('p',targets.length+tr(' 个依赖需要确认来源，只需统一选择一次。',' dependencies need source choices. Choose once for the group.')));
  for(const [value,title] of [['pending',tr('请选择处理方式','Choose an action')],['index',tr('统一从公共索引安装记录版本','Install recorded versions from public indexes')],['skip',tr('全部跳过这些依赖','Skip all these dependencies')],['custom',tr('分别设置 / 提供 wheel（高级）','Individual settings / wheels (advanced)')]]){const option=el('option',title);option.value=value;batch.append(option);}
  sync();batch.addEventListener('change',()=>{if(batch.value==='custom'){details.open=true;return;}for(const pkg of targets){const action=batch.value==='index'?indexAction(pkg):batch.value;options.dependencies[pkg.key]={action,path:''};const c=controls.get(pkg.key);c.select.value=action;c.input.value='';c.input.hidden=true;}details.open=false;invalidate();});section.append(batch);
 }
 for(const pkg of plan.dependencies){
  const row=el('div');row.append(el('p',pkg.name+' == '+pkg.version+' · '+(pkg.index||pkg.path||pkg.action)));
  const select=el('select');select.setAttribute('aria-label',pkg.name+tr('依赖来源',' source'));
  const entries=[['pending',tr('待选择','Choose source')],['pypi','PyPI'],['wheel',tr('提供本地 wheel','Provide local wheel')],['skip',tr('明确跳过','Explicitly skip')]];
  if(['torch','torchvision','torchaudio'].includes(pkg.key)&&/\+(cpu|cu\d+)$/.test(pkg.version))entries.splice(2,0,['pytorch',tr('PyTorch 官方对应构建','Official PyTorch build')]);
  for(const [value,title] of entries){const option=el('option',title);option.value=value;select.append(option);}select.value=pkg.action;
  const input=el('input');input.className='qbb-stretch';input.placeholder=tr('本机 .whl 文件的绝对路径','Absolute local .whl path');input.value=options.dependencies[pkg.key]?.path||pkg.path||'';input.hidden=select.value!=='wheel';
  const update=()=>{options.dependencies[pkg.key]={action:select.value,path:select.value==='wheel'?input.value.trim():''};input.hidden=select.value!=='wheel';sync();invalidate();};controls.set(pkg.key,{select,input});
  select.addEventListener('change',update);input.addEventListener('input',update);row.append(select,input);details.append(row);
 }
 section.append(details,el('p',tr('公共索引安装记录版本，不保证历史二进制一致；跳过依赖可能导致无法运行。修改后请重新检查。','Public indexes install recorded versions, not guaranteed historical binaries. Skipping dependencies may prevent startup. Recheck after changes.')));host.append(section);
}
