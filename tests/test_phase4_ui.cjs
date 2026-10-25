// Offline DOM + mocked transport; no writes to any real environment.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.isConnected=true;this.checked=false;this.disabled=false;this.classList={add(){}};}
 set textContent(value){this.text=String(value);this.children=[];}get textContent(){return this.text||'';}
 append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 setAttribute(key,value){this[key]=value;}
}
const all=n=>[n,...(n.children||[]).flatMap(all)],text=n=>all(n).map(x=>x.textContent||x.text||'').join('\n');
const find=(host,value)=>all(host).find(n=>n.textContent===value||n['aria-label']===value);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
 const calls=[],timers=new Map(),saved=new Map();let seq=0,transport=async()=>({status:'idle'});
 const context={document:{createElement:tag=>new Element(tag),createTextNode:text=>({text})},AbortSignal,crypto:require('node:crypto').webcrypto,
  localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},
  api:{fetchApi:async(url,options)=>{const body=options.body?JSON.parse(options.body):undefined;calls.push({url,body});const data=await transport(url,body);return{ok:true,json:async()=>data};}},
  setTimeout:fn=>{timers.set(++seq,fn);return seq;},clearTimeout:id=>timers.delete(id),
  renderStatePlan:(host,plan)=>host.append(new Element('diff-details'))};
 vm.createContext(context);
 for(const file of ['task-view.js','current-restore.js']){
  let source=fs.readFileSync(path.join(__dirname,'../web',file),'utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export ','');
  if(file==='current-restore.js')source='const el=taskElement,button=taskButton;\n'+source;
  vm.runInContext(source,context);
 }
 return {context,calls,timers,route:fn=>{transport=fn;},pump:async()=>{const pending=[...timers.values()];timers.clear();for(const fn of pending)fn();await tick();await tick();}};
}
const tr=(a,b)=>a;
const plan={status:'changes_proposed',fingerprint:'frozen-review',actions:[{resource_id:'plugin:Added'}],blockers:[]};
const prepared={id:'a'.repeat(32),record_id:'target',status:'prepared',plan_hash:'plan-hash',plan,target_label:'Target'};
const verified={status:'no_changes',blockers:[],diff:{state:'matched',change_count:0,unverified_count:0}};
(async()=>{
 {
  for(const notice of ['comparison_incomplete']){
   const env=setup(),host=new Element('host');
   env.route(async url=>url.endsWith('/recovery-plan')?{...plan,execution_ready:false,execution_notice:notice}:{status:'idle'});
   env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();
   assert.equal(find(host,'准备恢复材料'),undefined);assert.equal(env.calls.filter(c=>c.url.endsWith('/prepare')).length,0);
   assert.ok(text(host).includes('信息不完整'));
  }
 }
 {
  const env=setup(),host=new Element('host');
  const zipPlan={...plan,execution_ready:true,execution_notice:null,actions:[{resource_id:'package:demo',material:{kind:'local_zip'}}]};
  env.route(async url=>url.endsWith('/recovery-plan')?zipPlan:{status:'idle'});
  env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();
  assert.ok(find(host,'准备恢复材料'));assert.equal(find(host,'准备恢复材料').disabled,true);
  assert.equal(env.calls.filter(c=>c.url.endsWith('/prepare')).length,0);
  assert.equal(find(host,'允许提前联网获取准确资源'),undefined);assert.ok(text(host).includes('无需联网下载'));
 }
 {
  const {context}=setup(),host=new Element('host');
  context.renderTaskProgress(host,{phase:'save_objects',current:'plugin',done:2,total:5},tr);
  const meter=all(host).find(n=>n.tag==='progress');assert.equal(meter.value,2);assert.equal(meter.max,5);assert.ok(text(host).includes('当前阶段，非整项恢复完成比例'));
  host.replaceChildren();context.renderTaskProgress(host,{phase:'scan_core'},tr);assert.equal(all(host).filter(n=>n.tag==='progress').length,0);assert.ok(text(host).includes('不显示估算百分比'));
  context.renderCaptureTask(host,{id:'id',action:'create',status:'completed',result:{statistics:{plugins:2,packages:3,saved_resources:1,reused_resources:1,new_bytes:0,reused_bytes:10},unrestorable:[{name:'<script>bad</script>',recoverability:{reason:'original_artifact_missing'}}]}},tr);
  assert.ok(text(host).includes('版本记录完成'));assert.ok(text(host).includes('该 Snapshot 存在无法保证恢复的资源'));assert.ok(text(host).includes('original_artifact_missing'));assert.ok(!all(host).some(n=>n.tag==='script'));
 }
 {
  const {context}=setup(),host=new Element('host');
  for(const status of ['partial','failed_rollback','failed_rolled_back','interrupted','preparation_failed','handoff_failed','completed']){
   context.renderRestoreResult(host,{...prepared,status,action_results:[]},tr);assert.ok(text(host).includes('环境未完全恢复'));assert.ok(!text(host).includes('受管理环境状态恢复完成'));
  }
  const success={...prepared,status:'completed',outcome:'applied',verification:verified,action_results:[{resource_id:'plugin:a',kind:'plugin',status:'applied',operation:'remove'}]};
  assert.equal(context.verifiedRestore(success),true);context.renderRestoreResult(host,success,tr);assert.ok(text(host).includes('受管理环境状态恢复完成'));assert.ok(text(host).includes('移除插件 · 1'));assert.ok(text(host).includes('不保证 Python 字节级一致性'));
  assert.equal(context.verifiedRestore({...success,verification:{...verified,blockers:[{reason:'unknown'}]}}),false);
  assert.equal(context.verifiedRestore({...success,outcome:'rolled_back'}),false);
  const results=new Element('host');context.renderActionResults(results,Array.from({length:486},(_,i)=>({resource_id:'package:'+i,status:'rolled_back'})),tr);
  assert.ok(text(results).includes('已回退'));assert.ok(text(results).includes('1–12 / 486'));assert.ok(!text(results).includes('package:12 ·'));
  find(results,'下一页').onclick();assert.ok(text(results).includes('13–24 / 486'));assert.ok(!text(results).includes('package:0 ·'));
 }
 {
  const env=setup(),host=new Element('host');let state={status:'idle'},offline=false;
  env.route(async(url,body)=>{
   if(offline)throw Error('offline');
   if(url.endsWith('/recovery-plan'))return plan;
   if(url.endsWith('/prepare')){state=prepared;return prepared;}
   if(url.endsWith('/start')){state={...prepared,status:'waiting_for_exit'};return state;}
   return state;
  });
  const close=env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();
  let prepare=find(host,'准备恢复材料');assert.equal(prepare.disabled,true);await prepare.onclick();assert.equal(env.calls.filter(c=>c.url.endsWith('/prepare')).length,0);
  const trust=find(host,'确认快照可信与操作范围');trust.checked=true;trust.onchange();assert.equal(prepare.disabled,false);
  await prepare.onclick();await tick();assert.equal(env.calls.filter(c=>c.url.endsWith('/prepare')).length,1);assert.equal(env.calls.find(c=>c.url.endsWith('/prepare')).body.allow_network,false);
  const start=find(host,'停止并恢复当前实例'),ack=find(host,'确认停机恢复当前实例');assert.equal(start.disabled,true);assert.equal(ack.checked,false);
  await start.onclick();assert.equal(env.calls.filter(c=>c.url.endsWith('/start')).length,0);
  ack.checked=true;ack.onchange();const a=start.onclick(),b=start.onclick();await Promise.all([a,b]);assert.equal(env.calls.filter(c=>c.url.endsWith('/start')).length,1);
  offline=true;await env.pump();assert.ok(text(host).includes('等待服务重连'));assert.ok(text(host).includes('不会重新提交恢复'));assert.ok(!text(host).includes('受管理环境状态恢复完成'));
  offline=false;state={...prepared,status:'completed',outcome:'applied',verification:verified};await env.pump();assert.ok(text(host).includes('受管理环境状态恢复完成'));assert.equal(env.calls.filter(c=>c.url.endsWith('/start')).length,1);
  close();assert.equal(env.timers.size,0);
 }
 {
  const env=setup(),host=new Element('host');env.route(async url=>url.endsWith('/recovery-plan')?{...plan,status:'blocked',blockers:[{reason:'source_unknown'}]}:{status:'idle'});
  env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();assert.ok(text(host).includes('存在阻碍'));assert.equal(find(host,'准备恢复材料'),undefined);assert.equal(env.calls.filter(c=>c.url.endsWith('/start')||c.url.endsWith('/prepare')).length,0);
 }
 {
  const env=setup(),host=new Element('host');env.route(async()=>prepared);env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();assert.equal(find(host,'停止并恢复当前实例').disabled,true);assert.equal(env.calls.filter(c=>c.body).length,0);
 }
 {
  const env=setup(),host=new Element('host');env.route(async()=>({...prepared,plan:undefined}));env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();assert.equal(find(host,'停止并恢复当前实例'),undefined);assert.ok(text(host).includes('旧任务没有可供复核的冻结计划'));
 }
 {
  const env=setup(),host=new Element('host');let rejected=false;
  env.route(async url=>{if(url.endsWith('/recovery-plan'))return plan;if(url.endsWith('/prepare')){rejected=true;const error=Error('stale fingerprint');error.status=409;throw error;}return rejected?{...prepared,record_id:'older',status:'completed',outcome:'applied',verification:verified}:{status:'idle'};});
  env.context.mountCurrentRestore(host,'target',tr);await tick();await tick();const trust=find(host,'确认快照可信与操作范围');trust.checked=true;trust.onchange();await find(host,'准备恢复材料').onclick();await env.pump();assert.ok(text(host).includes('stale fingerprint'));assert.ok(!text(host).includes('受管理环境状态恢复完成'));assert.equal(env.timers.size,0);
 }
 {
  const env=setup(),host=new Element('host');let taskId;
  env.route(async(url,body)=>{if(body){taskId=body.request_id;throw Error('response lost');}return{id:taskId,action:'create',status:'completed',result:{statistics:{},unrestorable:[]}};});
  const task=await env.context.runCaptureTask(host,'create',{confirmed:true},tr);
  assert.equal(task.status,'completed');assert.equal(env.calls.filter(c=>c.body).length,1);assert.ok(env.calls.at(-1).url.endsWith(taskId));
 }
 {
  const env=setup(),host=new Element('host');let status='running';
  env.route(async()=>({id:'saved-task',action:'create',status,phase:'save_objects',done:1,total:3,result:{statistics:{},unrestorable:[]}}));
  const watching=env.context.watchCaptureTask(host,'saved-task',tr);await tick();assert.ok(text(host).includes('1 / 3'));status='completed';await env.pump();await watching;assert.ok(text(host).includes('版本记录完成'));assert.equal(env.calls.filter(c=>c.body).length,0);
 }
 {
  const env=setup(),host=new Element('host');
  env.context.renderCaptureTask(host,{id:'failed',action:'create',status:'failed',error:'Preview again before saving'},tr);
  assert.ok(text(host).includes('版本记录失败'));
  assert.ok(text(host).includes('预览已过期或失效，请重新预览后确认保存。'));
  assert.ok(!text(host).includes('Preview again before saving'));
 }
 console.log('Phase 4 progress, gaps, strict completion, two confirmations, reconnect, replay prevention and lifecycle UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
