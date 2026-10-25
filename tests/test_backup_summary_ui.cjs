// Offline DOM test of verification results and bounded backup details.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.classList={add(){}};}
 set textContent(value){this.text=String(value);this.children=[];}get textContent(){return this.text||'';}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this[key]=value;}
}
const context={document:{createElement:tag=>new Element(tag)}};vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../web/task-view.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export ','');
vm.runInContext(source,context);
const all=node=>[node,...node.children.flatMap(all)],find=(host,text)=>all(host).find(n=>n.textContent===text),tr=zh=>zh;
const components=Array.from({length:26},(_,i)=>({id:'package:'+i,name:'component '+i,status:i===24?'incomplete':i===25?'metadata_only':'local_saved',archive_verified:i<25,has_archive:i<25,files:i===25?0:3,gaps:i===24?[{path:'pkg/missing.py',reason:'installed_file_missing_or_not_regular'}]:[]}));
const summary={verified_archives:25,counts:{local_saved:24,incomplete:1,metadata_only:1,source_only:0,protected:0,outside_scope:0,absent:0},components};
const host=new Element('host');context.renderBackupSummary(host,summary,tr);
const rows=()=>all(host).filter(n=>n.className==='qv-plan-row');
assert.equal(rows().length,12);assert.ok(find(host,'本地备份明细 · 完整 24 · 有缺口 1 · 仅元数据 1'));
const next=find(host,'下一页'),previous=find(host,'上一页');assert.equal(previous.disabled,true);
next.onclick();assert.equal(rows().length,12);assert.ok(find(host,'component 12'));assert.ok(!find(host,'component 0'));
next.onclick();assert.equal(rows().length,2);assert.equal(next.disabled,true);
const filter=all(host).find(n=>n.tag==='select');filter.value='incomplete';filter.onchange();assert.equal(rows().length,1);assert.ok(find(host,'有缺口'));assert.ok(find(host,'3 个文件 · ZIP 内容已核验'));assert.ok(find(host,'pkg/missing.py · installed_file_missing_or_not_regular'));
filter.value='metadata_only';filter.onchange();assert.ok(find(host,'0 个文件 · 未生成 ZIP'));
filter.value='all';filter.onchange();const search=all(host).find(n=>n.tag==='input');search.value='no such component';search.oninput();assert.ok(find(host,'没有匹配的备份组件'));
const old=new Element('host'),statistics={plugins:1,packages:1,saved_resources:1,reused_resources:0,new_bytes:10,reused_bytes:0};
context.renderCaptureTask(old,{status:'completed',action:'create',result:{statistics}},tr);assert.ok(find(old,'版本记录完成'));assert.ok(!all(old).some(n=>n.textContent.includes('ZIP 内容核验通过')));
const complete=new Element('host');context.renderCaptureTask(complete,{status:'completed',action:'create',result:{statistics,backup_summary:summary}},tr);assert.ok(find(complete,'本地备份明细 · 完整 24 · 有缺口 1 · 仅元数据 1'));
const progress=new Element('host');context.renderTaskProgress(progress,{phase:'verify_zip_archives',done:2,total:5},tr);assert.ok(find(progress,'逐项核验 ZIP 归档'));
const scanning=new Element('host');context.renderCaptureTask(scanning,{status:'running',action:'preview',phase:'scan_plugins',current:'Demo'},tr);
const saving=new Element('host');context.renderCaptureTask(saving,{status:'running',action:'create',phase:'save_zip_archives'},tr);assert.ok(find(saving,'保存记录'));assert.ok(!find(saving,'扫描环境'));assert.ok(all(saving).some(n=>n.tag==='progress'));
assert.ok(find(scanning,'扫描环境'));const meter=all(scanning).find(n=>n.tag==='progress');assert.ok(meter);assert.equal(meter.value,undefined);assert.ok(find(scanning,'当前：Demo'));assert.ok(!all(scanning).some(n=>n.textContent.includes('总体流程')));
context.renderCaptureTask(scanning,{status:'running',action:'preview',phase:'verify_objects',done:3,total:10},tr);assert.equal(all(scanning).find(n=>n.tag==='progress').value,3);
const saved=new Element('host');context.renderCaptureTask(saved,{status:'completed',action:'create',result:{statistics,backup_summary:{verified_archives:480,counts:{local_saved:480,incomplete:0,metadata_only:0},components:components.slice(0,24)},unrestorable:Array.from({length:480},(_,i)=>({name:'unknown '+i,recoverability:{reason:'source_unknown'}}))}},tr);
assert.ok(!find(saved,'该 Snapshot 存在无法保证恢复的资源。'));assert.ok(!all(saved).some(n=>n.textContent.includes('source_unknown')));
const gaps=new Element('host');context.renderCaptureTask(gaps,{status:'completed',action:'create',result:{statistics,backup_summary:summary,unrestorable:[]}},tr);assert.ok(find(gaps,'该 Snapshot 存在无法保证恢复的资源。'));assert.ok(find(gaps,'2 项资源缺口；0 项扫描问题'));
const failed=new Element('host');context.renderCaptureTask(failed,{status:'failed',action:'create',error:'ZIP index mismatch'},tr);assert.ok(find(failed,'版本记录失败'));assert.ok(!find(failed,'版本记录完成'));
console.log('Backup verification summary, pagination, filtering, gaps and legacy-result UI checks passed');
