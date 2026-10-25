const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.isConnected=true;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this[key]=value;}
 removeAttribute(key){delete this[key];}
}
const timers=new Map();let sequence=0;
const progressId='a'.repeat(32);
const context={document:{createElement:tag=>new Element(tag)},JSON,encodeURIComponent,crypto:{randomUUID:()=>progressId},setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id)};
vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../web/state-plan.js'),'utf8');
vm.runInContext(source.replaceAll('export function','function').replaceAll('export async function','async function'),context);
const all=node=>[node,...node.children.flatMap(all)],tr=(a,b)=>a;
const host=new Element('host');
const resources=Array.from({length:102},(_,i)=>({resource_id:'plugin:'+i,name:'plugin '+i,status:'differences',differences:[{status:'extra',field:'presence',current:true,target:false}],proposals:['remove_resource'],blockers:[],warnings:[]}));
resources.push({resource_id:'protected',name:'NodeOps',status:'protected_resource',reason:'current_environment_manager',differences:[],blockers:[],warnings:[]});
const plan={status:'changes_proposed',read_only:true,execution_allowed:false,actions:resources.slice(0,102).map(r=>({resource_id:r.resource_id,operations:r.proposals,status:'proposed'})),blockers:[],artifact_health:{},diff:{resources,runtime:[],issues:[]}};
context.renderStatePlan(host,plan,tr);
assert.ok(all(host).some(n=>n.textContent==='当前环境 → 目标记录'));
assert.ok(all(host).some(n=>(n.textContent||'').includes('当前多出 → 移除')));
const rows=()=>all(host).filter(n=>n.className==='qv-plan-row');
const find=text=>all(host).find(n=>n.textContent===text);
assert.equal(rows().length,12);
const next=find('下一页'),previous=find('上一页');
assert.equal(previous.disabled,true);next.onclick();
assert.equal(rows().length,12);assert.ok(find('plugin 12'));assert.ok(!find('plugin 0'));
for(let i=0;i<7;i++)next.onclick();assert.equal(rows().length,7);assert.equal(next.disabled,true);
const search=all(host).find(n=>n.tag==='input');search.value='plugin 101';search.oninput();assert.equal(rows().length,1);assert.ok(find('plugin 101'));
search.value='no such component';search.oninput();assert.equal(rows().length,0);assert.ok(find('没有匹配的组件'));
search.value='';search.oninput();const filter=all(host).find(n=>n.tag==='select');filter.value='protected';filter.onchange();assert.equal(rows().length,1);assert.ok(find('NodeOps'));
plan.blockers=[{resource_id:'plugin:1',reason:'missing_wheel'},{resource_id:'plugin:2',reason:'missing_wheel'}];context.renderStatePlan(host,plan,tr);
assert.ok(find('阻碍原因 · 1 类（点击展开）'));find('missing_wheel · 2').onclick();assert.equal(rows().length,2);find('清除原因筛选').onclick();assert.equal(rows().length,12);
const controls=all(host).filter(n=>n.tag==='button');assert.ok(controls.every(n=>['上一页','下一页','清除原因筛选','missing_wheel · 2'].includes(n.textContent)),'No execution control');
assert.ok(!source.includes('/start')&&!source.includes('/install'));
const matched={resource_id:'package:demo',name:'demo',status:'matched',differences:[],warnings:[],blockers:[]};
const zipPlan={status:'no_changes',actions:[],blockers:[],artifact_health:{'package:demo':{status:'failed'}},backup_issues:[{resource_id:'package:demo',reason:'backup_material_invalid'}],diff:{resources:[matched],runtime:[],issues:[],unverified_count:0}};
context.renderStatePlan(host,zipPlan,tr);assert.equal(rows().length,0);assert.ok(find('备份材料提醒 · 1'));assert.ok(find('无差异'));assert.ok(!find('受阻'));
zipPlan.diff.resources=[{...matched,status:'differences',differences:[{status:'content_mismatch',field:'content',path:'native.pyd',current:'a',target:'b'}]}];
zipPlan.actions=[{resource_id:'package:demo',operations:['restore_installed_files'],status:'proposed',material:{kind:'local_zip'}}];zipPlan.execution_ready=true;
context.renderStatePlan(host,zipPlan,tr);assert.equal(rows().length,1);assert.ok(find('存在差异'));assert.ok(find('恢复材料：本地 ZIP 已校验'));assert.ok(!find('受阻'));
(async()=>{
 const calls=[];
 await context.loadStatePlan(host,async(...args)=>{calls.push(args);return plan;},'abc',tr);
 assert.equal(calls[0][0],'/abc/recovery-plan');
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])),{content_only:true,progress_id:progressId});
 let finish;
 const pending=context.loadStatePlan(host,async(path)=>path.includes('comparison-progress')?{phase:'scan_installed_files',current:'torch',done:2,total:5}:new Promise(resolve=>{finish=resolve;}),'abc',tr);
 assert.ok(find('正在对比环境'));assert.ok(all(host).some(n=>n.tag==='progress'));
 const poll=[...timers.values()].at(-1);await poll();assert.ok(find('torch'));assert.equal(all(host).find(n=>n.tag==='progress').value,2);
 finish({...plan,materials_checked:false});await pending;assert.ok(find('恢复时校验'));
 await context.loadStatePlan(host,async()=>{throw Error('changed during scan');},'abc',tr);
 assert.ok(all(host).some(n=>n.textContent==='changed during scan'));
 console.log('Phase 2 read-only direction, plan, pagination and error UI checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
