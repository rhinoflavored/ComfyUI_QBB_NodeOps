const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.value='';this.dataset={};this.isConnected=true;this.classList={add(){}};}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(){}
 get options(){return this.children;}
}
const all=n=>[n,...n.children.flatMap(all)];
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function setup(task){
 const timers=[],requests=[];
 const context={document:{createElement:t=>new Element(t)},tr:zh=>zh,
  runtimeState:()=>({pending:false,data:{builds:['cpu'],combinations:[{build:'cpu',python:'3.12',torch:'2.6',pins:{}}]}}),
  loadRuntime:()=>Promise.resolve(),watchRuntime:()=>{},setTimeout:fn=>timers.push(fn),
  nodeopsFetch:async(url,options)=>{requests.push([url,options]);if(task instanceof Error)throw task;return {ok:true,json:async()=>task};}};
 vm.createContext(context);
 const source=fs.readFileSync(path.join(__dirname,'../web/create-instance.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function','function');
 vm.runInContext(source,context);
 const host=new Element('host'),left=new Element('left'),right=new Element('right');
 context.renderCreateInstance(host,left,right);
 return {host,right,timers,requests,setTask:value=>task=value};
}
(async()=>{
 const task={mode:'new',status:'running',destination_root:'F:/new',environment:{build:'cpu',python:'3.12',torch:'2.6'},current:'安装依赖',logs:['working']};
 const app=setup(task);
 assert.equal(app.right.children[0].disabled,true);
 await flush();
 assert.equal(app.requests.length,1);
 assert.equal(app.requests[0][0],'/qbb_nodeops/migration/status');
 assert.ok(all(app.host).some(n=>n.value==='F:/new'));
 assert.ok(all(app.host).some(n=>n.textContent.includes('正在创建实例')));
 assert.ok(all(app.host).some(n=>n.textContent==='working'));
 app.setTask({...task,status:'success',logs:['done']});
 await app.timers.shift()();await flush();
 assert.ok(all(app.host).some(n=>n.textContent==='创建完成'));
 assert.equal(app.right.children[0].disabled,false);
 assert.equal(app.timers.length,0);
 const failed=setup(new Error('offline'));await flush();
 assert.equal(failed.right.children[0].disabled,true);
 failed.setTask(task);await failed.timers.shift()();await flush();
 assert.ok(all(failed.host).some(n=>n.textContent.includes('正在创建实例')));
 const other=setup({...task,mode:'existing'});await flush();
 assert.ok(!all(other.host).some(n=>n.textContent.includes('正在创建实例')));
 assert.equal(other.right.children[0].disabled,true);
 const closed=setup(task);await flush();closed.host.children[0].isConnected=false;
 await closed.timers.shift()();assert.equal(closed.requests.length,1);
 const idle=setup({mode:'new',status:'idle'});await flush();
 assert.equal(idle.timers.length,0);
 assert.ok(app.requests.every(([,options])=>!options.method));
 console.log('Creation task reconnect, retry, completion and isolation checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
