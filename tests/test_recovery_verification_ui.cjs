const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{constructor(tag){this.tag=tag;this.children=[];this.events={};this.textContent='';}append(...n){this.children.push(...n);}replaceChildren(){this.children=[];}addEventListener(k,f){this.events[k]=f;}}
const context={document:{createElement:t=>new Element(t),createTextNode:t=>Object.assign(new Element('text'),{textContent:t})}};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/recovery-task.js'),'utf8').replace('export function','function'),context);
const host=new Element('div'),all=n=>[n,...n.children.flatMap(all)];let request;
context.renderRecoveryTask(host,{id:'job',can_verify:true},zh=>zh,()=>{},async(id,options)=>{request={id,options};});
const inputs=all(host).filter(n=>n.tag==='input'),button=all(host).find(n=>n.tag==='button');
assert.equal(button.disabled,true);assert.equal(inputs[2].disabled,true);
inputs[0].checked=true;inputs[0].events.change();assert.equal(button.disabled,false);
inputs[1].checked=true;inputs[1].events.change();assert.equal(inputs[2].disabled,false);
inputs[2].checked=true;inputs[1].checked=false;inputs[1].events.change();assert.equal(inputs[2].checked,false);
(async()=>{
await button.onclick();assert.equal(request.id,'job');assert.equal(request.options.startup,false);assert.equal(request.options.plugins,false);
inputs[1].checked=true;inputs[1].events.change();
inputs[3].checked=true;inputs[3].events.change();
inputs[4].value='SaveImage, LoadImage';
all(host).find(n=>n.tag==='textarea').value='{"1":{"class_type":"SaveImage","inputs":{}}}';
request=null;await button.onclick();assert.equal(request,null);
inputs[5].checked=true;await button.onclick();
assert.equal(request.options.service,true);assert.equal(request.options.workflow_confirmed,true);assert.equal(request.options.expected_nodes.length,2);assert.equal(request.options.workflow['1'].class_type,'SaveImage');
inputs[1].checked=false;inputs[1].events.change();assert.equal(inputs[3].checked,false);assert.equal(inputs[5].checked,false);
console.log('Verification opt-in UI checks passed');
})();
