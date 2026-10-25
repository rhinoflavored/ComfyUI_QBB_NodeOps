const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.isConnected=true;this.value='';this.classList={add(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  get options(){return this.children;}
}
const hardware={devices:[{name:'RTX 5090',capability:12}],cuda_max:133,message:'本机显卡：RTX 5090'};
let mode='offline';
const context={document:{createElement:tag=>new Element(tag)},tr:zh=>zh,AbortSignal,setTimeout(){},
  nodeopsFetch:async url=>url.endsWith('/status')?{ok:true,json:async()=>({status:'idle'})}:
    {ok:mode==='online',json:async()=>mode==='online'?{hardware,builds:['cpu'],combinations:[{build:'cpu',torch:'2.7',python:'3.12'}]}:{hardware,error:'proxy unavailable'}}};
vm.createContext(context);
for(const name of ['runtime-data.js','create-instance.js']){
  const source=fs.readFileSync(path.join(__dirname,'../web',name),'utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export ','');
  vm.runInContext(source,context);
}
const all=n=>[n,...n.children.flatMap(all)];
(async()=>{
  const host=new Element('main'),left=new Element('footer'),right=new Element('footer');
  context.renderCreateInstance(host,left,right);
  for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
  const text=()=>all(host).map(n=>n.textContent||'').join('\n');
  assert.ok(text().includes('本机显卡：RTX 5090'));
  assert.ok(text().includes('proxy unavailable'));
  assert.ok(!text().includes('正在检测本机显卡和驱动'));
  assert.equal(context.runtimeState('cpu').data,null);
  assert.equal(right.children[0].disabled,true);
  mode='online';await context.loadRuntime('cpu',true);
  const previous=context.runtimeState('cpu').data;
  mode='offline';await context.loadRuntime('cpu',true);
  assert.equal(context.runtimeState('cpu').data,previous);
  assert.ok(text().includes('本机显卡：RTX 5090'));
  assert.ok(text().includes('在线版本刷新失败'));
  console.log('Hardware survives first-load network failure and refresh failure with cached versions');
})().catch(error=>{console.error(error);process.exitCode=1;});
