import { api } from "../../scripts/api.js";

export const NODEOPS_HEADERS = { "X-QBB-NodeOps": "1" };
export function nodeopsHeaders(extra = {}) { return { ...NODEOPS_HEADERS, ...extra }; }

// Cache only read-only page data. Task status and mutation requests stay live.
const reads = new Map();
const inflight = new Map();
let epoch = 0;
let preloadStarted = false;
const preloadPaths = [
  "/qbb_nodeops/preferences", "/qbb_nodeops/network_settings",
  "/qbb_nodeops/ai/settings", "/qbb_nodeops/report",
  "/qbb_nodeops/version-records/storage", "/qbb_nodeops/version-records",
  "/qbb_nodeops/instances", "/qbb_nodeops/health",
  "/qbb_nodeops/migration/scan?source_instance_id=current",
  "/qbb_nodeops/market?q=", "/qbb_nodeops/migration/runtime-catalog?build=cpu",
  "/qbb_nodeops/export_defaults?kind=log", "/qbb_nodeops/export_defaults?kind=deps",
];
function cacheable(url) {
  return preloadPaths.includes(url) ||
    /^\/qbb_nodeops\/version-records\/[^/?]+$/.test(url) ||
    /^\/qbb_nodeops\/migration\/runtime-catalog\?build=[a-z0-9]+$/.test(url);
}
async function read(url, options, transport) {
  const method=(options.method||"GET").toUpperCase();
  if(method!=="GET"){
    // Read-only metadata requests must not evict all prepared pages.
    if(!url.endsWith("/market/metadata") && !url.endsWith("/rebuild/plan") && !url.endsWith("/conflicts/check")){
      epoch++;reads.clear();inflight.clear();
    }
    return transport();
  }
  if(!cacheable(url))return transport();
  if(options.cache==="reload"){reads.delete(url);inflight.delete(url);}
  const cached=reads.get(url);
  if(cached && Date.now()-cached.time<60000)return cached.response.clone();
  if(inflight.has(url))return (await inflight.get(url)).clone();
  const version=epoch;
  const pending=(async()=>{
    const response=await transport();
    if(response.ok && version===epoch){
      let running=false;
      if(url==="/qbb_nodeops/version-records")running=!!(await response.clone().json()).running;
      if(!running)reads.set(url,{time:Date.now(),response:response.clone()});
    }
    return response;
  })();
  inflight.set(url,pending);
  try{return (await pending).clone();}
  finally{if(inflight.get(url)===pending)inflight.delete(url);}
}
export function nodeopsFetch(url, options = {}) {
  return read(String(url),options,()=>fetch(url,{...options,headers:nodeopsHeaders(options.headers||{})}));
}
export function preloadNodeopsData() {
  if(preloadStarted)return;
  preloadStarted=true;
  const queue=[...preloadPaths];
  const worker=async()=>{
    while(queue.length){
      const url=queue.shift();
      try{
        if(url.includes('/migration/runtime-catalog?')){
          const {loadRuntime}=await import('./runtime-data.js');
          const data=await loadRuntime(new URL(url,location.origin).searchParams.get('build'));
          if(url.endsWith('build=cpu'))for(const build of data?.builds||[])if(build!=='cpu')queue.push('/qbb_nodeops/migration/runtime-catalog?build='+encodeURIComponent(build));
          continue;
        }
        const response=await nodeopsFetch(url,{signal:AbortSignal.timeout(60000)});
        if(!response.ok)continue;
        // Record exports and alternate runtime channels are prepared in the same bounded queue.
        if(url==="/qbb_nodeops/version-records"){
          const data=await response.json();
          for(const item of data.items||[])queue.push("/qbb_nodeops/version-records/"+encodeURIComponent(item.id));
        }
        if(url.endsWith("runtime-catalog?build=cpu")){
          const data=await response.json();
          for(const build of data.builds||[])if(build!=="cpu")queue.push("/qbb_nodeops/migration/runtime-catalog?build="+encodeURIComponent(build));
        }
      }catch{/* A failed preload is retried when the page is opened. */}
    }
  };
  void Promise.allSettled([worker(),worker()]);
}
if (!api._qbbNodeopsFetchWrapped) {
  api._qbbNodeopsFetchWrapped=true;
  const original=api.fetchApi.bind(api);
  api.fetchApi=(route,options={})=>{
    if(!String(route).startsWith("/qbb_nodeops"))return original(route,options);
    return read(String(route),options,()=>original(route,{...options,headers:nodeopsHeaders(options.headers||{})}));
  };
}
