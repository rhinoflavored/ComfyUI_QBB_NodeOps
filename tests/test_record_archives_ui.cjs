const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class E{
 constructor(tag){this.tag=tag;this.children=[];this.checked=false;this.value='';}
 append(...nodes){for(const node of nodes){if(node.parent){const i=node.parent.children.indexOf(node);if(i>=0)node.parent.children.splice(i,1);}node.parent=this;this.children.push(node);}}
 insertBefore(node,before){const index=this.children.indexOf(node);if(index>=0)this.children.splice(index,1);this.children.splice(this.children.indexOf(before),0,node);}
 setAttribute(k,v){this[k]=v;}
}
const context={document:{createElement:t=>new E(t),createTextNode:t=>({textContent:t})}};
vm.createContext(context);vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../web/record-archives.js'),'utf8').replace('export function','function'),context);
const root=new E('root'),calls=[];
context.mountRecordArchives(root,async(path,body)=>{calls.push({path,body});return path.endsWith('checkpoint-status')?{policy:{enabled:false,interval_minutes:360,max_records:20,max_gib:20},state:{status:'idle'},bytes:0,unreferenced_bytes:0}:path.endsWith('inspect')?{token:'checked',label:'test',objects:1,metadata_only_components:0}:{id:'new'};},async()=>{},a=>a);
const nodes=n=>[n,...n.children.flatMap(c=>c.children?nodes(c):[c])];
const find=t=>nodes(root).find(n=>n.textContent===t),tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const rows=nodes(root).filter(n=>n.className==='qv-path-picker');assert.equal(rows.length,1);assert.deepEqual(rows[0].children.map(n=>n.tag),['span','input','button']);
 const checkbox=nodes(root).find(n=>n.type==='checkbox'),path=nodes(root).find(n=>n.tag==='input'&&n!==checkbox);
 find('确认执行预览操作').onclick();await tick();assert.equal(calls.length,0);
 path.value='C:/backup.zip';find('预览导入').onclick();await tick();
 assert.equal(checkbox.checked,false);checkbox.checked=true;
 find('确认执行预览操作').onclick();await tick();assert.equal(calls.at(-1).path,'/archive/import');assert.equal(calls.at(-1).body.token,'checked');
 const count=calls.length;find('确认执行预览操作').onclick();await tick();assert.equal(calls.length,count);
 find('预览导入').onclick();await tick();checkbox.checked=true;path.oninput();
 find('确认执行预览操作').onclick();await tick();assert.equal(calls.at(-1).path,'/archive/inspect');
 checkbox.checked=true;
 const before=calls.length;find('保存自动检查点设置').onclick();await tick();assert.equal(calls.length,before);
 find('读取自动设置与存储用量').onclick();await tick();
 find('保存自动检查点设置').onclick();await tick();assert.equal(calls.at(-1).path,'/archive/checkpoint-configure');assert.equal(calls.at(-1).body.policy.enabled,false);assert.equal(calls.at(-1).body.confirmed,true);
 console.log('Archive preview, confirmation and invalidation UI checks passed');
 const itemRoot=new E('root'),itemCalls=[];
 context.mountRecordArchives(itemRoot,async(action,body)=>{itemCalls.push({action,body});return action.endsWith('export-preview')?{token:'export-token',bytes:8,required_bytes:100,scopes:{python_environment:{status:'not_saved'}}}:action.endsWith('remove-preview')?{token:'remove-token',newly_unreferenced_objects:0,newly_unreferenced_bytes:0,related_records:['other']}:action.endsWith('check')?{status:'no_objects',checked_objects:0,objects:0,scopes:{code:{status:'metadata_only'}}}:{path:'C:/backup.zip'};},async()=>{},a=>a,{id:'record'});
 const itemFind=t=>nodes(itemRoot).find(n=>n.textContent===t),itemAck=nodes(itemRoot).find(n=>n.type==='checkbox'),itemPath=nodes(itemRoot).find(n=>n.tag==='input');
 await itemFind('选择 ZIP 保存位置…').onclick();assert.equal(itemCalls.at(-1).action,'/archive/choose-path');assert.equal(itemCalls.at(-1).body.kind,'zip-save');assert.equal(itemPath.value,'C:/backup.zip');assert.equal(itemAck.checked,false);
 await itemFind('选择 wheel 文件夹…').onclick();assert.equal(itemCalls.at(-1).body.kind,'folder');
 itemPath.value='C:/backup.zip';
 await itemFind('预览导出文件包').onclick();assert.equal(itemAck.checked,false);
 itemAck.checked=true;await itemFind('确认执行预览操作').onclick();
 assert.equal(itemCalls.at(-1).action,'/archive/export');assert.equal(itemCalls.at(-1).body.token,'export-token');assert.equal(itemCalls.at(-1).body.record_id,'record');
 await itemFind('预览移除记录').onclick();assert.equal(itemAck.checked,false);itemAck.checked=true;await itemFind('确认执行预览操作').onclick();
 assert.equal(itemCalls.at(-1).action,'/archive/remove');assert.equal(itemCalls.at(-1).body.token,'remove-token');
 await itemFind('检查备份完整性').onclick();assert.equal(itemCalls.at(-1).action,'/archive/check');
 assert.ok(nodes(itemRoot).some(n=>n.textContent?.includes('没有可校验的文件对象')));
 assert.ok(nodes(itemRoot).some(n=>n.textContent?.includes('不代表依赖、启动或工作流验证通过')));
 const raceRoot=new E('root');let resolvePreview;
 context.mountRecordArchives(raceRoot,()=>new Promise(resolve=>{resolvePreview=resolve;}),async()=>{},a=>a);
 const raceNodes=nodes(raceRoot),racePath=raceNodes.find(n=>n.tag==='input'),raceAck=raceNodes.find(n=>n.type==='checkbox');
 const pending=raceNodes.find(n=>n.textContent==='预览导入').onclick();
 racePath.value='changed';racePath.oninput();resolvePreview({token:'stale',objects:1,label:'old'});await pending;
 raceAck.checked=true;await raceNodes.find(n=>n.textContent==='确认执行预览操作').onclick();
 assert.ok(nodes(raceRoot).some(n=>n.textContent==='请先预览。'));
 console.log('Export/removal preview, integrity and stale-response UI checks passed');
 await find('选择 ZIP 文件…').onclick();assert.equal(calls.at(-1).path,'/archive/choose-path');assert.equal(calls.at(-1).body.kind,'zip-open');
})().catch(e=>{console.error(e);process.exitCode=1;});
