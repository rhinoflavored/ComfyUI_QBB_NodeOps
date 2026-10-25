const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class E{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.events={};this.value='';this.checked=false;this.isConnected=true;this.classList={add(){}};}
 append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}
 setAttribute(k,v){this[k]=v;}addEventListener(k,f){this.events[k]=f;}
 querySelector(tag){return this.children.find(n=>n.tag===tag)||this.children.map(n=>n.querySelector?.(tag)).find(Boolean);}
}
const calls=[];let selected='E:\\实例备份\\',error='',deferred=null;
const context={document:{createElement:t=>new E(t),createTextNode:text=>({textContent:text,children:[]})},isChineseLocale:()=>true,confirm:()=>true,setTimeout(){},clearTimeout(){},renderRecoveryPlan(){},renderRecoveryEnvironment(){},api:{fetchApi:async(url,options)=>{
 calls.push({url,body:options?.body?JSON.parse(options.body):null});
 if(url.endsWith('choose-folder')){if(deferred)await deferred.promise;return {ok:!error,json:async()=>error?{error}:{path:selected}};}
 return {ok:true,json:async()=>url.endsWith('/plan')?{can_rebuild:true,plan_id:'plan',blockers:[],warnings:[]}:{history:[]}};
}}};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/rebuild.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function','function'),context);
const all=n=>[n,...(n.children||[]).flatMap(all)],tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const root=new E('div');context.mountRebuild(root,'record',{mode:'restore',autoCheck:true});await tick();
 const button=text=>all(root).find(n=>n.textContent===text),picker=button('选择文件夹…'),target=all(root).find(n=>n['aria-label']==='恢复目标目录'),start=button('开始恢复');
 const issues=all(root).find(n=>n.className==='qv-recovery-problems');
 const metrics=all(root).filter(n=>n.className==='qv-restore-metric');assert.equal(metrics.length,3);
 for(const metric of metrics)assert.deepEqual(metric.children.map(n=>n.tag),['strong','span']);
 assert.ok(all(root).some(n=>n.className==='qv-restore-alert is-ready'));
 const css=fs.readFileSync(path.join(__dirname,'../web/versions.css'),'utf8');
 assert.match(css,/\.qv-restore-metric>strong\{font-size:26px/);
 assert.match(css,/\.qv-restore p\{font-size:13px/);
 assert.ok(!issues.open);assert.ok(all(issues).includes(button('重新检查')));
 assert.ok(!all(root).some(n=>n.tag==='input'&&n.type==='checkbox'));
 const row=all(root).find(n=>n.className==='qv-path-picker');assert.deepEqual(row.children.map(n=>n.tag),['span','input','button']);
 assert.equal(start.disabled,true);
 await picker.onclick();assert.ok(target.value.includes('ComfyUI-restored-'));assert.equal(start.disabled,false);
 assert.equal(calls.filter(c=>c.url.endsWith('/start')).length,0);
 const original=target.value;selected='';await picker.onclick();assert.equal(target.value,original);
 error='picker unavailable';await picker.onclick();assert.equal(target.value,original);assert.equal(picker.disabled,false);error='';
 selected='E:/next';deferred={};deferred.promise=new Promise(resolve=>deferred.resolve=resolve);
 const pending=picker.onclick();await tick();assert.equal(start.disabled,true);assert.equal(target.disabled,true);
 deferred.resolve();await pending;deferred=null;
 context.confirm=()=>false;await start.onclick();assert.equal(calls.filter(c=>c.url.endsWith('/start')).length,0);
 target.value='E:/manual';target.events.input();context.confirm=()=>true;await start.onclick();
 assert.equal(calls.find(c=>c.url.endsWith('/start')).body.target,'E:/manual');assert.equal(picker.disabled,true);
 console.log('Simple recovery: collapsed details, path picker, cancellation and explicit start passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
