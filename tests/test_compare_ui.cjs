const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
 append(...items){this.children.push(...items);}
}
const context={document:{createElement:tag=>new Element(tag)}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/record-compare.js'),'utf8').replace('export function','function'),context);
const host=new Element('div');
context.renderComparison(host,{
 versions:[{name:'plugin',kind:'added',current:'present',target:null}],
 dependencies:[{name:'package',kind:'modified',current:'2',target:'1'}],checked_files:101,
 components:[{name:'files',changes:Array.from({length:101},(_,i)=>({path:'file'+i,kind:'modified',type:'file'})),unverified:[{path:'excluded',reason:'not saved'}]}],
 unverified:[],
},(zh,en)=>zh);
function all(node){return [node,...node.children.flatMap(all)];}
assert.ok(all(host).some(n=>n.textContent.includes('记录 → 当前')));
assert.ok(all(host).some(n=>n.textContent==='文件与目录对比'));
assert.equal(all(host).filter(n=>n.textContent.startsWith('变化 · 文件')).length,100);
all(host).find(n=>n.textContent==='显示更多').onclick();
assert.equal(all(host).filter(n=>n.textContent.startsWith('变化 · 文件')).length,101);
assert.ok(all(host).some(n=>n.textContent==='excluded — not saved'));
console.log('Comparison rendering and pagination checks passed');
