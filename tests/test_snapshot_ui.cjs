// Offline DOM-state test: no browser, server, network, or snapshot writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tag=tag;this.tagName=tag.toUpperCase();this.children=[];this.value='';this.checked=false;this.disabled=false;this.isConnected=true;this.classList={add(){}}; }
  set textContent(value) {this.text=value;this.children=[];}
  get textContent(){return this.text||'';}
  append(...nodes){for(const node of nodes){if(node.parent)node.parent.children.splice(node.parent.children.indexOf(node),1);node.parent=this;this.children.push(node);}}
  replaceChildren(...nodes){this.children=[...nodes];}
  insertBefore(node,before){this.children.splice(this.children.indexOf(before),0,node);}
  setAttribute(key,value){this[key]=value;}
  focus(){}
  click(){}
  addEventListener(name,fn){(this.events ||= {})[name]=fn;}
  showModal(){this.open=true;}
  close(){this.open=false;this.events?.close?.();}
  remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);}
}
const calls=[];
const planCalls=[];
const restoreCalls=[];
let previewOk=true;
let now=1700000000000;
const timers=new Map();let timerId=0;
let failCapture=false;
let nextScanDelay=0;
const context={
  Date:class extends Date {static now(){return now;}},
  document:{createElement:tag=>new Element(tag),createTextNode:text=>({text}),querySelector:()=>true},
  api:{fetchApi:async(url,options)=>{
    const body=options?.body?JSON.parse(options.body):null;calls.push({url,body});
    let data;
    if(url.endsWith('/preview'))data={preview_id:'preview',expires_in:1800,expires_at:(now+1800000)/1000,can_save:previewOk,files:1,bytes_upper_bound:3,free_bytes:999999,required_bytes:16777216,space:{new_bytes:0,reused_bytes:3},items:[{name:'core',method:'snapshot',files:1,bytes:3,skipped:[{path:'external',reason:'link/reparse point'}],errors:[]}],errors:previewOk?[]:['unreadable']};
    else if(url.endsWith('/archive/remove-preview'))throw Error('Deletion must not preflight');
    else if(body)data={id:'record'};
    else if(url.endsWith('/storage'))data={directory:'store'};
    else data={items:[{id:'old',label:'Legacy',created:1,operation_checkpoint:{operation:'update',phase:'before'},content_summary:{scopes:{code:{status:'metadata_only'}},sizes:{logical_bytes:0,unique_referenced_bytes:0}}},{id:'new',label:'Captured',created:2,operation_checkpoint:{operation:'update',phase:'after',before_record_id:'old'},content_summary:{scopes:{code:{status:'complete_within_policy'},user:{status:'captured'},models:{status:'not_selected'}},sizes:{logical_bytes:1024,unique_referenced_bytes:512}}},{id:'unknown',schema:4,created:3}],running:false};
    return {ok:true,json:async()=>data};
  }},
  isChineseLocale:()=>true,mountRebuild(){},loadStatePlan(host,request,id){planCalls.push(id);},mountRecordArchives(host,request,refresh,tr,item){assert.equal(item,undefined,'Record cards must not mount backup/removal controls');},setTimeout(){return 1;},clearTimeout(){},URL,Blob,
  mountCurrentRestore(host,id){restoreCalls.push(id);return()=>{};},
  taskRequest:async()=>({status:'idle'}),rememberTask(){},rememberedTask(){return null;},renderCaptureTask(){},watchCaptureTask(){throw Error('No running task in fixture');},
  runCaptureTask:async(host,action,payload)=>{if(action==='create'&&failCapture)return{status:'failed',error:'Preview again before saving'};if(action==='preview'){now+=nextScanDelay;nextScanDelay=0;}const response=await context.api.fetchApi('/qbb_nodeops/version-records'+(action==='preview'?'/preview':''),{body:JSON.stringify(payload)});return{status:'completed',action,result:await response.json()};},
};
context.setTimeout=fn=>{timers.set(++timerId,fn);return timerId;};
context.clearTimeout=id=>timers.delete(id);
let source=fs.readFileSync(path.join(__dirname,'../web/snapshots.js'),'utf8');
source=source.replace(/^import .*;\r?\n/gm,'').replace('export function renderSnapshots','function renderSnapshots').replaceAll('import.meta.url',JSON.stringify('file:///snapshots.js'));
vm.createContext(context);vm.runInContext(source,context);
const host=new Element('host');context.renderSnapshots(host,null,null);
function all(node){return [node,...(node.children||[]).flatMap(all)];}
const find=text=>all(host).find(n=>n.textContent===text);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await tick();
  const taskPanel=all(host).find(n=>n.className==='qv-task qv-capture-task');assert.ok(taskPanel);assert.ok(all(host).find(n=>n.className==='qv-capture-step qv-preview-step').children.includes(taskPanel));
  const storageRow=all(host).find(n=>n.className==='qv-path-picker');
  assert.deepEqual(storageRow.children.map(n=>n.tag),['span','input','button']);
  assert.equal(storageRow.children[2].textContent,'浏览文件夹');
  await find('导出清单').onclick();
  assert.ok(find('已导出清单。'));
  assert.ok(!all(host).some(n=>(n.textContent||'').includes('导出的是版本清单')));
  find('恢复').onclick();
  let dialog=all(host).find(n=>n.tag==='dialog');
  assert.equal(dialog.open,true);assert.equal(dialog.children[0].tag,'header');
  assert.equal(dialog.children[1].className,'qv-dialog-body');
  assert.ok(all(dialog.children[0]).some(n=>n.textContent==='关闭'));
  assert.ok(!all(dialog.children[1]).some(n=>n.textContent==='关闭'));
  all(dialog).find(n=>n.textContent==='关闭').onclick();assert.ok(!all(host).includes(dialog));
  find('对比').onclick();dialog=all(host).find(n=>n.tag==='dialog');
  assert.ok(all(dialog.children[1]).some(n=>n['aria-label']==='比较终点'));
  all(dialog).find(n=>n.textContent==='关闭').onclick();
  const css=fs.readFileSync(path.join(__dirname,'../web/versions.css'),'utf8');
  assert.match(css,/\.qv-action-dialog\[open\]\{display:flex;flex-direction:column\}/);
  assert.match(css,/\.qv-dialog-body\{[^}]*min-height:0;overflow:auto/);
  assert.ok(!find('1 · 确认保存范围'));assert.ok(find('1 · 预览内容与空间'));assert.ok(find('2 · 确认并保存'));
  assert.ok(find('核心与插件 · 仅版本信息'));assert.ok(find('核心与插件 · 范围内完整清单'));assert.ok(find('核心与插件 · 未知'));
  assert.ok(find('配置与工作流 · 已保存'));assert.ok(find('模型 · 未选择'));
  assert.ok(!find('一致性、敏感配置与数据库说明'));
  assert.ok(!all(host).some(n=>(n.textContent||'').includes('不是删除后可释放空间')));
  assert.ok(!find('核心与插件（必选）'));assert.ok(!find('1 · 确认保存范围'));assert.ok(find('1 · 预览内容与空间'));assert.ok(find('2 · 确认并保存'));
  const helpSource=fs.readFileSync(path.join(__dirname,'../web/help-copy.js'),'utf8').replace('export function pageHelpCopy','function pageHelpCopy');
  const helpContext={};vm.createContext(helpContext);vm.runInContext(helpSource,helpContext);
  const helpHost={matches:()=>false,closest:()=>({dataset:{activeTab:'snapshots'}})};
  const chinese=helpContext.pageHelpCopy(helpHost,true).join('\n');
  const english=helpContext.pageHelpCopy(helpHost,false).join('\n');
  assert.ok(chinese.includes('先检查差异与删除项'));
  assert.ok(chinese.includes('单独确认停机'));
  assert.ok(chinese.includes('断线不自动重发操作'));
  assert.ok(chinese.includes('来源未知不再单独阻止恢复'));
  assert.ok(chinese.includes('无需寻找原始 wheel'));
  assert.ok(english.includes('complete valid installed-file ZIPs restore directly'));
  assert.ok(english.includes('without original wheels or rebuilding'));
  const save=find('确认保存记录'),preview=find('预览保存内容');
  const errorPolicy=all(host).find(n=>n['aria-label']==='遇到错误时');
  assert.equal(errorPolicy.value,'strict');assert.deepEqual(errorPolicy.children.map(n=>n.value),['strict']);
  const mode=all(host).find(n=>n['aria-label']==='文件保存方式');mode.value='auto';
  const large=all(host).find(n=>n['aria-label']==='超过 256 MiB 的文件');assert.equal(large.value,'include');
  assert.equal(all(host).some(n=>n.value==='user'),false);
  const check=all(host).find(n=>n['aria-label']==='确认保存范围');
  assert.equal(save.disabled,true);
  await save.onclick();assert.equal(calls.filter(c=>c.body?.preview_id).length,0);
  nextScanDelay=1900000;await preview.onclick();assert.equal(save.disabled,true);
  check.checked=true;check.onchange();assert.equal(save.disabled,false);
  assert.ok(all(host).some(n=>(n.textContent||'').startsWith('预计新增对象 ')));
  assert.equal(all(host).filter(n=>n.className==='qv-metric').length,3);
  assert.ok(all(host).some(n=>(n.textContent||'').startsWith('有 1 项排除内容')));
  assert.ok(!find('查看组件、排除项和错误').parent.open);
  large.value='exclude';large.onchange();assert.equal(save.disabled,true);
  await preview.onclick();check.checked=true;check.onchange();await save.onclick();
  const request=calls.find(c=>c.body?.preview_id);
  assert.equal(request.body.options.mode,'auto');assert.equal(request.body.options.large_files,'include');
  assert.equal(request.body.options.on_error,'strict');
  assert.deepEqual(request.body.options.core_dirs,[]);assert.equal(request.body.preview_id,'preview');
  assert.equal(save.disabled,true);
  previewOk=false;await preview.onclick();check.checked=true;check.onchange();assert.equal(save.disabled,true);
  assert.equal(find('查看组件、排除项和错误').parent.open,true);assert.ok(find('unreadable'));
  assert.ok(find('查看操作前后变化'));assert.ok(find('更新前检查点'));assert.ok(find('更新后检查点'));
  previewOk=true;
  const purpose=all(host).find(n=>n['aria-label']==='记录用途'),related=all(host).find(n=>n['aria-label']==='关联操作前记录');
  purpose.value='update:after';purpose.onchange();assert.equal(related.hidden,false);
  await preview.onclick();check.checked=true;check.onchange();assert.equal(save.disabled,true);
  related.value='old';related.onchange();assert.equal(save.disabled,true);
  await preview.onclick();check.checked=true;check.onchange();assert.equal(save.disabled,false);await save.onclick();
  assert.equal(calls.filter(c=>c.body?.preview_id).at(-1).body.checkpoint,undefined,'Only manual schema 4 captures are supported');
  assert.equal(all(host).filter(n=>n.textContent==='删除').length,3);
  context.confirm=()=>{throw Error('Browser confirmation must not be used');};
  const prechecks=calls.filter(c=>c.url.endsWith('/archive/remove-preview')).length;
  let deleting=find('删除').onclick();await tick();
  assert.ok(find('删除版本记录？'));find('取消').onclick();await deleting;
  assert.equal(calls.filter(c=>c.url.endsWith('/archive/remove')).length,0);
  deleting=find('删除').onclick();await tick();
  const deleteDialog=all(host).find(n=>n.tag==='dialog');
  assert.equal(find('删除记录').disabled,false);assert.equal(calls.filter(c=>c.url.endsWith('/archive/remove-preview')).length,prechecks);
  find('删除记录').onclick();await deleting;
  assert.ok(!all(host).includes(deleteDialog));
  assert.deepEqual(calls.find(c=>c.url.endsWith('/archive/remove')).body,{record_id:'old',confirmed:true});
  assert.ok(find('记录已移入回收站。'));
  assert.ok(!source.includes('mountRecordArchives('));
  const previewsBefore=calls.filter(c=>c.url.endsWith('/preview')).length;
  errorPolicy.value='metadata';errorPolicy.onchange();await tick();
  assert.equal(calls.filter(c=>c.url.endsWith('/preview')).length,previewsBefore+1);
  assert.equal(calls.filter(c=>c.url.endsWith('/preview')).at(-1).body.options.on_error,'strict');
  assert.equal(check.checked,false);assert.equal(save.disabled,true);
  all(host).filter(n=>n.textContent==='对比')[2].onclick();
  const stateDialog=all(host).find(n=>n.tag==='dialog');
  assert.equal(stateDialog.open,true);assert.deepEqual(planCalls,['unknown']);
  assert.ok(!all(stateDialog).some(n=>n['aria-label']==='比较终点'));
  all(stateDialog).find(n=>n.textContent==='关闭').onclick();
  all(host).filter(n=>n.textContent==='恢复')[2].onclick();
  assert.deepEqual(restoreCalls,['unknown']);
  assert.ok(all(host).some(n=>n.tag==='dialog'));
  purpose.value='';purpose.onchange();
  failCapture=true;await preview.onclick();check.checked=true;check.onchange();await save.onclick();
  assert.ok(find('预览已过期或失效，请重新预览后确认保存。'));
  failCapture=false;await preview.onclick();
  assert.equal(find('预览已过期或失效，请重新预览后确认保存。'),undefined,'Fresh preview clears stale save error');
  assert.ok(all(host).some(n=>n.textContent?.includes('扫描完成后预览有效期 30 分钟')));
  check.checked=true;check.onchange();assert.equal(save.disabled,false);
  const writes=calls.filter(c=>c.body?.preview_id).length;
  now+=1801000; // Simulate suspension: no timer callbacks before the click.
  await save.onclick();
  assert.equal(calls.filter(c=>c.body?.preview_id).length,writes,'Expired preview must not send a save');
  assert.equal(save.disabled,true);assert.equal(check.checked,false);
  assert.ok(find('预览已过期，请重新点击“预览保存内容”。'));
  check.checked=true;check.onchange();assert.equal(save.disabled,true);
  await preview.onclick();check.checked=true;check.onchange();assert.equal(save.disabled,false);
  now+=1801000;
  const pending=[...timers.values()];timers.clear();for(const callback of pending)callback();
  assert.equal(save.disabled,true,'Timer disables expired preview without interaction');
  assert.equal(check.checked,false);
  await preview.onclick();check.checked=true;check.onchange();await save.onclick();
  assert.equal(calls.filter(c=>c.body?.preview_id).length,writes+1,'A new preview permits exactly one save');
  console.log('Snapshot preview expiration, stale-error clearing, confirmation and record deletion UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
