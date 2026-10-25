// Read-only comparison view. Unknown/excluded files are not confirmed changes.
export function renderComparison(host,data,tr){
 const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const saved=data.direction==='record_to_record';
 const kind=k=>({added:tr('新增','Added'),deleted:tr('删除','Deleted'),modified:tr('变化','Changed'),type_changed:tr('文件 / 目录类型变化','File / directory type changed'),permissions:tr('权限变化','Permissions changed')}[k]||k);
 function paged(parent,items,render){
   let count=0;const content=el('div');parent.append(content);
   const more=el('button',tr('显示更多','Show more'));more.type='button';more.className='qbb-lh-btn';
   const draw=()=>{for(const item of items.slice(count,count+100))content.append(render(item));count+=100;more.hidden=count>=items.length;};
   more.onclick=draw;draw();if(items.length>100)parent.append(more);
 }
 function table(title,rows){
   host.append(el('h4',title+' · '+rows.length));
   if(!rows.length)return;
   const table=el('table');table.className='qv-diff-table';const head=el('thead'),line=el('tr');
   for(const label of [tr('组件','Component'),tr('变化','Change'),saved?tr('起点记录','From record'):tr('记录','Recorded'),saved?tr('终点记录','To record'):tr('当前','Current')]){const cell=el('th',label);cell.scope='col';line.append(cell);}head.append(line);table.append(head);
   const body=el('tbody');table.append(body);
   // Version lists are normally small; file lists below are paginated.
   for(const row of rows){const line=el('tr');for(const value of [row.name,kind(row.kind),row.target??'—',row.current??'—'])line.append(el('td',value));body.append(line);}
   host.append(table);
 }
 host.append(el('p',saved?tr('方向：起点记录 → 终点记录。新增表示终点多出，删除表示终点缺少；范围不同不计为增删。','Direction: from record → to record. Added/deleted refers to the destination; scope differences are not additions/deletions.'):tr('方向：记录 → 当前。新增表示当前多出，删除表示当前缺少。','Direction: recorded → current. Added means present only now; deleted means now missing.')));
 if(saved){host.append(el('p',(data.endpoints?.before?.label||data.endpoints?.before?.id||'')+' → '+(data.endpoints?.after?.label||data.endpoints?.after?.id||'')));host.append(el('p',tr('依据保存清单中的摘要比较，不验证共享对象现状、依赖二进制或运行效果；操作关联不证明因果关系。','Compares saved manifest hashes, not current object health, dependency binaries or runtime behavior. Operation links do not prove causality.')));}
 const summary=el('div');summary.className='qv-metrics';for(const [label,count] of [[tr('已确认变化','Confirmed changes'),data.change_count],[tr('无法核实','Unverified'),data.unverified_count]])if(Number.isFinite(count)){const metric=el('p',label+' · '+count);metric.className='qv-metric';summary.append(metric);}host.append(summary);
 table(tr('插件与 Git 提交变化','Plugin and Git changes'),data.versions);
 table(tr('Python 运行环境变化','Python runtime changes'),data.runtime||[]);
 table(tr('依赖版本与来源变化','Dependency version and source changes'),data.dependencies);
 host.append(el('h4',tr('文件与目录对比','File and directory comparison')));
 host.append(el('p',(saved?tr('已比较文件清单项：','Manifest file entries compared: '):tr('已核对文件：','Files checked: '))+data.checked_files));
 for(const component of data.components){
   const section=el('details');section.className='qv-recovery-section';
   section.open=component.changes.length>0;
   section.append(el('summary',component.name+' · '+component.changes.length+tr(' 项变化 · ',' changes · ')+component.unverified.length+tr(' 项无法核实',' unverified')));
   if(component.changes.length)paged(section,component.changes,row=>el('p',kind(row.kind)+' · '+(row.type==='directory'?tr('目录','Directory'):tr('文件','File'))+' · '+row.path));
   else section.append(el('p',component.unverified.length?tr('已核对部分未发现变化；仍有内容无法核实。','No changes in checked content; some content remains unverified.'):tr('保存范围内的文件内容和目录一致。','File contents and directories match within the captured scope.')));
   if(component.unverified.length){const unknown=el('details');unknown.append(el('summary',tr('无法核实 / 排除项','Unverified / excluded')));paged(unknown,component.unverified,row=>el('p',row.path+' — '+row.reason));section.append(unknown);}
   host.append(section);
 }
 if(data.unverified.length){const pending=el('details');pending.append(el('summary',tr('其他无法核实项','Other unverified items')));paged(pending,data.unverified,row=>el('p',row.name+' — '+row.reason));host.append(pending);}
 host.append(el('small',tr('比较内容及当前平台支持的 chmod 权限，不将修改时间单独算作变化。Windows 仅核对可写标志；不验证 ACL、所有者、特殊权限位或工作流运行结果。','Compares contents and platform-supported chmod permissions, not timestamps alone. Windows checks only the writable flag; ACLs, ownership, special permission bits and workflow behavior are not verified.')));
}
