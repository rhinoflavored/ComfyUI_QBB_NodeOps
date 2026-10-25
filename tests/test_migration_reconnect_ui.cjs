const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/api.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
const task = {status:'running', mode:'instance', started_at:'today', source_root:'source', destination_root:'target', plugin_names:['node.py'], logs:['working']};
function setup(sequence) {
  let calls = 0, renders = 0;
  const delays = [];
  const state = {migrationSelection:new Set(), migrationSourceId:'current'};
  const context = {state, AbortSignal, console:{warn(){}},
    document:{querySelector:()=>({querySelector:()=>({dataset:{activeTab:'migration'}})})},
    hooks:{showAttentionModal(){renders++;}},
    setTimeout(fn, ms){delays.push(ms);queueMicrotask(fn);},
    nodeopsFetch:async url=>{
      if(url.includes('/migration/status')) {
        calls++;
        const value = sequence.shift();
        if(value instanceof Error)throw value;
        if(!value)throw new Error('Unexpected status request');
        return {ok:true,json:async()=>value};
      }
      return {ok:true,json:async()=>url.includes('/instances')?{instances:[]}:{plugins:[],status:{status:'idle'}}};
    }};
  vm.createContext(context);vm.runInContext(source,context);
  return {context,state,delays,calls:()=>calls,renders:()=>renders};
}
(async()=>{
  const done = {...task,status:'success',logs:['finished']};
  let test = setup([task,new Error('offline'),new Error('offline'),task,done]);
  const first = test.context.pollMigration();
  assert.equal(first,test.context.pollMigration(),'concurrent callers share a poller');
  await first;
  assert.equal(test.calls(),5);
  assert.equal(test.state.migrationResult.status,'success');
  assert.equal(test.state.migrationResult.sourceLabel,'source');
  assert.equal(test.state.migrationResult.pluginNames[0],'node.py');
  assert.equal(test.state.migrationConnectionError,'');
  assert.deepEqual(test.delays,[1000,2000,4000,1000]);
  test = setup([...Array(725).fill(task),done]);
  await test.context.pollMigration();assert.equal(test.calls(),726);
  test = setup([done]);await test.context.loadMigrationData();
  assert.equal(test.state.migrationResult.status,'success','refresh reconnects completed task');
  test = setup([{...task,mode:'new',status:'success'}]);await test.context.pollMigration();
  assert.equal(test.state.migrationResult,null,'creation must not appear as migration');
  test = setup([done]);test.state.migrationDismissedJob='today|source|target|instance';
  await test.context.pollMigration();assert.equal(test.state.migrationResult,undefined,'dismissed result stays dismissed');
  test = setup([{status:'idle',mode:'new'}]);test.state.migrationResult={...task};
  await test.context.pollMigration();assert.equal(test.state.migrationResult,null);
  console.log('Migration reconnect, retries, long tasks, deduplication and isolation checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
