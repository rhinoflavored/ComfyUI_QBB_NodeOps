const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/api.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
const calls = [];
const context = {state:{migrationSourceId:'current'}, nodeopsFetch:async (url, options)=>{
  calls.push({url, options}); return {ok:true,json:async()=>({plugins:[]})};
}};
vm.createContext(context);vm.runInContext(source, context);
(async()=>{
  await context.fetchMigrationScan();
  await context.fetchMigrationScan('other');
  assert.ok(calls.every(call=>call.options.cache==='reload'));
  const cleanup = fs.readFileSync(path.join(__dirname,'../web/cleanup.js'),'utf8');
  assert.match(cleanup, /fetchApi\('\/qbb_nodeops\/health',\{cache:'reload'\}\)/);
  console.log('Migration refresh bypasses cache; resource load uses reload policy');
})().catch(error=>{console.error(error);process.exitCode=1;});
