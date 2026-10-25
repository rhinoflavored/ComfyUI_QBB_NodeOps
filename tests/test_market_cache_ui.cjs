const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const all=n=>[n,...n.children.flatMap(all)];
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.isConnected=true;this.value='';this.classList={add(){},toggle(){}};}
 set textContent(text){this.text=String(text);this.children=[];}get textContent(){return this.text||'';}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
 setAttribute(k,v){this[k]=v;}addEventListener(){}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 querySelectorAll(selector){return all(this).slice(1).filter(n=>selector==='option'?n.tag==='option':selector==='.qm-row small'?n.tag==='small':selector.startsWith('.')?(n.className||'').split(' ').includes(selector.slice(1)):n.tag===selector);}
}
const calls=[],timers=[];
let fail=false,nextStars=123;
const row=()=>({url:'https://github.com/example/plugin',title:'Cached plugin',description:'Saved locally',tags:[],stars:nextStars,updated_at:'2026-10-01',metadata_source:'snapshot'});
const context={document:{createElement:tag=>new Element(tag),head:new Element('head')},isChineseLocale:()=>true,URL,queueMicrotask,
 IntersectionObserver:class{observe(){}disconnect(){}},setTimeout:fn=>timers.push(fn),
 api:{fetchApi:async(url,options)=>{calls.push({url,options});if(fail)throw Error('offline');return{ok:true,json:async()=>url.endsWith('/cache-status')?{revision:nextStars}:{cache_revision:nextStars,items:[row()],tag_groups:{图像:[]}}};}}};
vm.createContext(context);
let source=fs.readFileSync(path.join(__dirname,'../web/market.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export ','').replaceAll('import.meta.url',JSON.stringify('file:///market.js'));
vm.runInContext(source,context);
const tick=async()=>{await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 const body=new Element('body'),left=new Element('left'),right=new Element('right');
 context.renderMarket(body,left,right);await tick();
 const text=()=>all(body).map(n=>n.textContent).join('\n');
 assert.ok(text().includes('Cached plugin'));assert.ok(text().includes('★ 123'));
 assert.ok(text().includes('已缓存仓库信息 1/1'));
 assert.ok(text().includes('缓存数据'));
 const table=body.querySelector('.qm-results');const original=table.children[0];
 fail=true;await timers.shift()();await tick();
 assert.equal(table.children[0],original);assert.ok(text().includes('★ 123'));assert.ok(!text().includes('offline'));
 fail=false;nextStars=456;await timers.shift()();await tick();assert.ok(text().includes('★ 456'));
 assert.ok(calls[1].options.cache==='reload');assert.ok(calls.every(c=>!c.url.includes('/metadata')));
 const beforeUnchanged=calls.length;const currentCard=table.children[0];await timers.shift()();assert.equal(calls.length,beforeUnchanged+1);assert.equal(table.children[0],currentCard);
 const count=calls.length;body.children[0].isConnected=false;await timers.shift()();assert.equal(calls.length,count);
 // Remount retains the already displayed table without waiting for another request.
 const fresh=new Element('body');context.renderMarket(fresh,left,right);assert.ok(all(fresh).some(n=>n.textContent==='Cached plugin'));assert.equal(calls.length,count);
 console.log('Saved table displays immediately; silent updates retain rows on failure and stop polling detached pages');
})().catch(error=>{console.error(error);process.exitCode=1;});
