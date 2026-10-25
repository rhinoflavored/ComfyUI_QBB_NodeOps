// Historical comparison integration: no live server or filesystem writes.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.checked=false;this.isConnected=true;this.classList={add(){}};}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(){}
 addEventListener(){}
 querySelector(tag){return this.children.find(n=>n.tag===tag)||this.children.map(n=>n.querySelector?.(tag)).find(Boolean);}
}
const calls=[],rendered=[];
const context={document:{createElement:tag=>new Element(tag),createTextNode:text=>({text})},isChineseLocale:()=>true,
 api:{fetchApi:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({change_count:0,unverified_count:1})};}},
 renderComparison:(host,data)=>rendered.push(data),setTimeout(){},clearTimeout(){}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/rebuild.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function','function'),context);
(async()=>{
 const historical=new Element('div');context.mountRebuild(historical,'before',{mode:'compare',autoCheck:true,otherRecordId:'after'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(calls[0].body,{record_id:'before',other_record_id:'after'});
 assert.equal(historical.querySelector('h3').textContent,'两条记录之间的变化');
 assert.equal(rendered.length,1);
 context.mountRebuild(new Element('div'),'before',{mode:'compare',autoCheck:true});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(calls[1].body,{record_id:'before'});
 const comparisonContext={document:context.document};vm.createContext(comparisonContext);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/record-compare.js'),'utf8').replace('export function','function'),comparisonContext);
 const output=new Element('div');comparisonContext.renderComparison(output,{direction:'record_to_record',endpoints:{before:{label:'Old'},after:{label:'New'}},versions:[{name:'plugin',kind:'modified'}],runtime:[],dependencies:[],components:[],unverified:[],checked_files:0,change_count:1,unverified_count:0},zh=>zh);
 const all=n=>[n,...(n.children||[]).flatMap(all)];
 assert.ok(all(output).some(n=>n.textContent==='起点记录'));assert.ok(all(output).some(n=>n.textContent==='终点记录'));
 assert.ok(all(output).some(n=>n.textContent==='Old → New'));
 assert.ok(all(output).some(n=>n.textContent?.includes('不验证共享对象现状')));
 console.log('Historical comparison routing and evidence UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
