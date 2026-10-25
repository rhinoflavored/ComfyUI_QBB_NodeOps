const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class E{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.events={};this.isConnected=true;this.value='';this.classList={add(){}};}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
 setAttribute(k,v){this[k]=v;}addEventListener(k,f){this.events[k]=f;}
 querySelector(tag){return this.children.find(n=>n.tag===tag)||this.children.map(n=>n.querySelector?.(tag)).find(Boolean);}
}
const calls=[],plan={can_rebuild:false,repositories:Array.from({length:100},(_,i)=>({id:'p'+i,name:'Plugin'+i,gap:'not saved',action:'pending',target:'plugins/'+i})),dependencies:Array.from({length:100},(_,i)=>({key:'pkg'+i,name:'Package'+i,version:'1',action:'pending'})),runtime:{method:'current',version:'3.11',architecture:'AMD64'},blockers:Array.from({length:200},(_,i)=>'technical blocker '+i),warnings:[]};
const context={document:{createElement:t=>new E(t),createTextNode:text=>({textContent:text,children:[]})},isChineseLocale:()=>true,setTimeout(){},clearTimeout(){},api:{fetchApi:async(url,options)=>{const body=options?.body?JSON.parse(options.body):null;calls.push({url,body});return {ok:true,json:async()=>url.endsWith('/plan')?plan:{history:[]}};}}};
vm.createContext(context);
for(const file of ['recovery-plan.js','recovery-environment.js','rebuild.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',file),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function','function'),context);
const root=new E('div'),all=n=>[n,...(n.children||[]).flatMap(all)];
const visible=n=>n.hidden?[]:[n,...(n.tag==='details'&&!n.open?n.children.filter(c=>c.tag==='summary'):n.children||[]).flatMap(visible)];
(async()=>{
 context.mountRebuild(root,'record',{mode:'restore',autoCheck:true});await new Promise(r=>setImmediate(r));
 assert.equal(visible(root).filter(n=>n.tag==='select').length,0);
 const problems=all(root).find(n=>n.className==='qv-recovery-problems');assert.ok(!problems.open);problems.open=true;
 assert.equal(visible(root).filter(n=>n.tag==='select').length,3);
 assert.equal(visible(root).filter(n=>n.tag==='input'&&n.placeholder?.includes('绝对路径')).length,0);
 assert.ok(!visible(root).some(n=>n.textContent?.includes('technical blocker')));
 const batch=all(root).find(n=>n['aria-label']==='缺失插件统一处理');batch.value='skip';batch.events.change();
 const start=all(root).find(n=>n.textContent==='开始恢复');assert.equal(start.disabled,true);
 await all(root).find(n=>n.textContent==='重新检查').onclick();
 const request=calls.filter(c=>c.url.endsWith('/plan')).at(-1);assert.equal(request.body.choices.p99.action,'skip');
 assert.ok(!calls.some(c=>c.url.endsWith('/start')));
 assert.match(fs.readFileSync(path.join(__dirname,'../web/versions.css'),'utf8'),/\.qv-action-dialog \[hidden\]\{display:none!important\}/);
 console.log('200-item recovery hides choices until Resolve issues; blockers prevent automatic recovery');
})().catch(e=>{console.error(e);process.exitCode=1;});
