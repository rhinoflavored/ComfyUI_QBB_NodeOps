import { api } from '../../scripts/api.js';
import { isChineseLocale } from './i18n.js';
import './http.js';

const sheet = document.createElement('link');
sheet.rel = 'stylesheet'; sheet.href = new URL('./market.css', import.meta.url).href;
document.head.append(sheet);
const state = { query: '', loadedQuery: '', data: null, pending: false, message: '', page: 1, sort: 'relevance', detail: null, git: false, downloads: new Map(), expanded: false, scroll: 0 };
async function request(path, body, fresh = false) {
  const response = await api.fetchApi('/qbb_nodeops/market' + path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : (fresh ? {cache:'reload'} : {}));
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

// Requests outlive a particular mounted page. Notify the latest view, never
// the detached view which happened to initiate the request.
let activeMarketView = null;
function notifyMarketView() {
  if (!activeMarketView?.root.isConnected) {
    activeMarketView = null;
    return;
  }
  activeMarketView.refresh();
}

export function renderMarket(body, footerLeft, footerRight) {
  const tr = (a,b) => isChineseLocale() ? a : b;
  const el = (tag, text = '', cls = '') => { const e = document.createElement(tag); e.textContent = text; e.className = cls; return e; };
  const button = (text, fn) => { const b = el('button',text,'qbb-lh-btn'); b.type='button'; b.onclick=fn; return b; };
  const label = tag => isChineseLocale() ? tag.split(' / ')[0] : tag.split(' / ').at(-1);
  const link = (text,url) => { const a=el('a',text); try { const u=new URL(url); if(['https:','http:'].includes(u.protocol)) a.href=u.href; } catch {} a.target='_blank'; a.rel='noopener noreferrer'; return a; };
  const owner = item => {try{return new URL(item.url).pathname.split('/')[1];}catch{return '';}};
  body.replaceChildren(); footerLeft.replaceChildren(); footerRight.replaceChildren();
  const root=el('section','','qbb-market'); body.append(root);
  const header=el('header','','qm-header');
  const heading=el('div'); heading.append(el('h2',tr('插件市场','Plugin marketplace')),el('p',tr('从你想实现的功能开始，找到合适的插件。','Find plugins for what you want to create.'),'qm-muted'));
  header.append(heading,button(tr('从 Git 地址下载','Download from Git URL'),openGitDialog));root.append(header);
  const form=el('form','','qm-search');
  const input=el('input');input.type='search';input.value=state.query;input.placeholder=tr('中文或英文都可以，例如：逻辑运算、video upscale','Chinese or English, e.g. logic, video upscale');input.setAttribute('aria-label',tr('搜索插件功能','Search plugin capabilities'));input.oninput=()=>{state.query=input.value;};
  const search=button(tr('搜索','Search'),()=>load(false));search.classList.add('qbb-lh-btn-primary');form.append(input,search);form.onsubmit=e=>{e.preventDefault();load(false);};root.append(form);
  const tags=el('nav','','qm-tags');tags.setAttribute('aria-label',tr('功能分类','Categories'));root.append(tags);
  const toolbar=el('div','','qm-toolbar');const count=el('span');const sort=el('select');sort.setAttribute('aria-label',tr('结果排序','Sort results'));
  for(const [value,text] of [['relevance',tr('相关度','Relevance')],['stars',tr('Star 数量','Stars')],['updated_at',tr('最近更新','Recently updated')]]){const opt=el('option',text);opt.value=value;sort.append(opt);}sort.value=state.sort;sort.onchange=()=>{state.sort=sort.value;state.page=1;drawList();};toolbar.append(count,sort);root.append(toolbar);
  const status=el('p','','qm-muted');status.setAttribute('role','status');root.append(status);
  const layout=el('div','','qm-layout');const results=el('div','','qm-results');const detail=el('aside','','qm-detail');detail.setAttribute('aria-label',tr('插件详情','Plugin details'));layout.append(results,detail);root.append(layout);
  const refresh=button(tr('刷新','Refresh'),()=>load(true));footerLeft.append(refresh);
  const observer=new IntersectionObserver(entries=>{if(!root.isConnected){observer.disconnect();return;}if(entries.some(e=>e.isIntersecting)&&!state.pending&&sorted().length>state.page*30){state.page++;drawList();}},{root:body,rootMargin:'200px'});
  footerRight.append(el('small',tr('下载到当前实例。完成后请检查插件依赖并重启 ComfyUI。','Download into this instance. Check plugin dependencies and restart ComfyUI afterwards.'),'qm-muted'));
  function sorted(){const items=[...(state.data?.items||[])];if(state.sort!=='relevance')items.sort((a,b)=>{const val=i=>{const v=state.sort==='stars'?i.stars:Date.parse(i.updated_at);return Number.isFinite(v)?v:-1;};return val(b)-val(a);});return items;}
  function drawTags(){
    tags.replaceChildren();
    const groups=state.data?.tag_groups||{[tr('功能','Capabilities')]:state.data?.tags||[]};
    if(!Object.hasOwn(groups,state.tagGroup))state.tagGroup=null;
    const all=button(tr('全部','All'),()=>{
      const expanded=!state.tagGroup&&!!state.allTags;
      state.tagGroup=null;state.allTags=!expanded;
      if(!expanded)input.value=state.query='';
      drawTags();if(!expanded)load(false);
    });
    all.setAttribute('aria-expanded',String(!state.tagGroup&&!!state.allTags));
    all.setAttribute('aria-pressed',String(!state.tagGroup));tags.append(all);
    for(const group of Object.keys(groups)){
      const b=button(label(group),()=>{state.allTags=false;state.tagGroup=state.tagGroup===group?null:group;drawTags();});
      b.setAttribute('aria-pressed',String(state.tagGroup===group));
      b.setAttribute('aria-expanded',String(state.tagGroup===group));tags.append(b);
    }
    // Closing a category only hides its options; keep the current search results.
    if(!state.tagGroup&&!state.allTags)return;
    const children=el('div','','qm-tag-options qm-all-tag-options');
    const values=state.allTags?Object.values(groups).flat():groups[state.tagGroup];
    for(const tag of new Set(values)){
      const b=button(label(tag),()=>{input.value=state.query=label(tag);drawTags();load(false);});
      b.setAttribute('aria-pressed',String(state.query===label(tag)));children.append(b);
    }
    tags.append(children);
  }
  function drawList(){queueMicrotask(paintMetadata);observer.disconnect();results.replaceChildren();const items=sorted();count.textContent=`${items.length} ${tr('个结果','results')}`;search.disabled=state.pending;results.setAttribute('aria-busy',String(state.pending));status.textContent=state.pending?tr('正在搜索插件目录…','Searching the catalogue…'):state.message||(state.data?.relaxed?tr('没有精确匹配，已显示含义相近的结果。','No exact match; showing related results.'):(state.sort!=='relevance'?tr('仅按已获取的仓库数据排序，未知值排在最后。','Sorted by available repository metadata; unknown values sort last.'):''));refresh.disabled=state.pending;
    for(const item of items.slice(0,state.page*30)){const card=el('article','','qm-result');card.dataset.repositoryUrl=item.url;const title=link(item.title,item.url);title.className='qm-title';card.append(title,el('small',owner(item),'qm-muted'),el('p',item.description||tr('暂无功能说明，查看仓库了解详情。','No description available. See the repository for details.'),'qm-description'));const badges=el('div','','qm-tags');for(const tag of (item.tags||[]).slice(0,3))badges.append(el('span',label(tag),'qm-badge'));card.append(badges);const bottom=el('div','','qm-row');const date=Date.parse(item.updated_at);bottom.append(el('small',`★ ${item.stars??'—'} · ${Number.isFinite(date)?new Date(date).toLocaleDateString():tr('更新时间未知','Update date unknown')}`,'qm-muted'));const progress=state.downloads.get(item.url);const action=button(progress?.running?tr('下载中…','Downloading…'):item.installed||progress?.done?tr('已下载 · 待检查','Downloaded · review'):tr('下载','Download'),()=>download(item.url));action.disabled=!!(progress?.running||progress?.done||item.installed);action.title=tr('下载到当前实例','Download to current instance');bottom.append(action);card.append(bottom);if(progress?.message){const feedback=el('p',progress.message,'qm-muted');feedback.setAttribute('role','status');card.append(feedback);}results.append(card);}
    if(items.length>state.page*30){const sentinel=el('div',tr('向下滚动加载更多','Scroll for more'),'qm-muted');results.append(sentinel);observer.observe(sentinel);}
    if(!items.length&&!state.pending)results.append(el('p',state.data?tr('没有找到匹配插件，请更换关键词后重试。','No matches. Try different keywords.'):tr('插件目录尚未加载。','Catalogue not loaded.'),'qm-empty'));}
  function drawDetail(){detail.replaceChildren();detail.hidden=!state.detail&&!state.git;layout.classList.toggle('has-detail',!detail.hidden);if(detail.hidden)return;const close=button(tr('关闭详情','Close details'),()=>{state.detail=null;state.git=false;drawDetail();});detail.append(close);let url=state.detail;
    if(state.git){detail.append(el('h3',tr('从 Git 地址下载','Download from Git URL')));const field=el('input');field.type='url';field.placeholder='https://github.com/owner/repository';field.setAttribute('aria-label',tr('GitHub 仓库地址','GitHub repository URL'));detail.append(field);url='';field.oninput=()=>{url=field.value.trim();};}
    else {const item=state.data?.items?.find(i=>i.url===url);if(!item){detail.append(el('p',tr('该插件不在当前结果中，请重新选择。','Select a plugin from the current results.')));return;}detail.append(el('h3',item.title),link(item.url,item.url),el('p',item.description||''));if(item.readme_excerpt)detail.append(el('h4',tr('说明摘录','Documentation excerpt')),el('p',item.readme_excerpt),link(tr('阅读完整说明','Read documentation'),item.readme_url||item.url));if(item.matched_tags?.length||item.matched_terms?.length)detail.append(el('h4',tr('为什么匹配','Why this matches')),el('p',[...new Set([...(item.matched_tags||[]).map(label),...(item.matched_terms||[])])].join(' · ')));if(item.evidence_terms?.length)detail.append(el('small',item.evidence_terms.join(', '),'qm-muted'));if(item.missing_tags?.length)detail.append(el('p',tr('尚未确认的能力：','Unconfirmed capabilities: ')+item.missing_tags.map(label).join(' · ')));}
    detail.append(el('p',tr('下载至当前实例。仅克隆仓库，不安装 Python 依赖。第三方代码会在重启时加载，请先确认来源可信。','Downloads into this instance. Clones the repository only, without installing Python dependencies. Third-party code loads on restart; verify the source first.'),'qm-notice'));
    const feedback=el('p');feedback.setAttribute('role','status');const existing=state.downloads.get(url);feedback.textContent=existing?.message||'';
    const confirm=button(tr('确认下载到当前实例','Confirm download to this instance'),async()=>{const target=url; if(!/^https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/?$/.test(target||'')){feedback.textContent=tr('请输入公开 GitHub 仓库的 HTTPS 地址。','Enter the HTTPS URL of a public GitHub repository.');return;}if(state.downloads.get(target)?.running)return;confirm.disabled=true;const progress={running:true,message:tr('正在下载，最长约 3 分钟…','Downloading, up to 3 minutes…')};state.downloads.set(target,progress);feedback.textContent=progress.message;drawList();try{const result=await request('/install',{url:target});progress.done=true;progress.message=result.dependencies_required?tr('已下载。需要按仓库说明安装依赖，然后重启。','Downloaded. Install dependencies per repository instructions, then restart.'):tr('已下载。请检查仓库说明与依赖，然后重启。','Downloaded. Review documentation and dependencies, then restart.');for(const item of state.data?.items||[])if(item.url===target||item.url===result.url)item.installed=true;}catch(e){progress.message=e.message;}finally{progress.running=false;feedback.textContent=progress.message;confirm.disabled=!!progress.done;if(root.isConnected)drawList();}});
    confirm.classList.add('qbb-lh-btn-primary');confirm.disabled=!!existing?.running||!!existing?.done||!!state.data?.items?.find(i=>i.url===url)?.installed;detail.append(feedback,confirm);}
  function openGitDialog(){
    const existing=root.querySelector('.qm-git-dialog');
    if(existing){existing.querySelector('input')?.focus();return;}
    state.git=false;state.detail=null;drawDetail();
    const previousFocus=document.activeElement;
    const dialog=el('dialog','','qm-git-dialog');
    dialog.setAttribute('aria-label',tr('从 Git 地址下载','Download from Git URL'));
    const form=el('form');
    const title=el('h3',tr('从 Git 地址下载','Download from Git URL'));
    const field=el('input');field.type='url';field.required=true;field.autofocus=true;
    field.placeholder='https://github.com/owner/repository';
    field.setAttribute('aria-label',tr('GitHub 仓库地址','GitHub repository URL'));
    const note=el('p',tr('下载到当前实例。完成后请检查插件依赖并重启 ComfyUI。','Download into this instance. Check dependencies and restart ComfyUI afterwards.'),'qm-muted');
    const feedback=el('p');feedback.setAttribute('role','status');
    const actions=el('div','','qm-git-actions');
    const cancel=button(tr('取消','Cancel'),()=>dialog.close());
    const confirm=button(tr('确认下载','Download'),()=>form.requestSubmit());confirm.classList.add('qbb-lh-btn-primary');
    let pending=false;
    form.onsubmit=async event=>{
      event.preventDefault();if(pending)return;
      const target=field.value.trim();
      if(!/^https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/?$/.test(target)){
        feedback.textContent=tr('请输入公开 GitHub 仓库的 HTTPS 地址。','Enter the HTTPS URL of a public GitHub repository.');field.focus();return;
      }
      pending=true;confirm.disabled=true;field.disabled=true;
      feedback.textContent=tr('正在下载…','Downloading…');
      try{await download(target);feedback.textContent=state.downloads.get(target)?.message||'';}
      finally{pending=false;field.disabled=false;confirm.disabled=false;cancel.textContent=tr('关闭','Close');}
    };
    dialog.addEventListener('close',()=>{dialog.remove();if(previousFocus?.isConnected)previousFocus.focus();});
    dialog.addEventListener('keydown',event=>{if(event.key==='Escape')event.stopPropagation();});
    actions.append(cancel,confirm);form.append(title,field,note,feedback,actions);dialog.append(form);root.append(dialog);
    dialog.showModal();field.focus();
  }
  async function download(target){
    if(state.downloads.get(target)?.running||state.downloads.get(target)?.done||state.data?.items?.find(i=>i.url===target)?.installed)return;
    const progress={running:true,message:tr('正在下载到当前实例…','Downloading to current instance…')};
    state.downloads.set(target,progress);notifyMarketView();
    try{
      const result=await request('/install',{url:target});
      progress.done=true;
      progress.message=tr('已下载到当前实例。请检查依赖并重启 ComfyUI。','Downloaded to current instance. Check dependencies and restart ComfyUI.');
      for(const item of state.data?.items||[])if(item.url===target||item.url===result.url)item.installed=true;
    }catch(e){progress.message=tr('下载失败：','Download failed: ')+e.message;}
    finally{progress.running=false;notifyMarketView();}
  }
  async function load(refresh){if(state.pending)return;state.pending=true;const query=state.query;notifyMarketView();try{state.data=await request('?q='+encodeURIComponent(query)+(refresh?'&refresh=1':''));state.loadedQuery=query;state.page=1;state.message=state.data.warning||'';}catch(e){state.message=e.message;}finally{state.pending=false;notifyMarketView();}}
  function paintMetadata(){
    if(!root.isConnected)return;
    // Preserve each rendered card's association through asynchronous sorting/data changes.
    const rows=new Map((state.data?.items||[]).map(item=>[item.url,item]));
    for(const card of results.children){
      const item=rows.get(card.dataset.repositoryUrl),info=card.querySelector('.qm-row small');if(!item||!info)continue;
      const date=Date.parse(item.updated_at);
      const reason=item.metadata_error?tr('获取失败','Fetch failed'):tr('尚未获取','Not fetched');
      info.textContent=`★ ${item.stars??'—'} · ${Number.isFinite(date)?tr('仓库更新：','Repository updated: ')+new Date(date).toLocaleDateString():reason}`+(Number.isFinite(date)&&(item.metadata_error||item.metadata_source==='snapshot')?tr(' · 缓存数据',' · Cached'): '');
      info.title=item.metadata_error||tr('GitHub 仓库推送时间，并非版本发布日期','GitHub push time, not a release date');
    }
    const all=state.data?.items||[],known=all.filter(i=>Number.isFinite(Date.parse(i.updated_at))).length;
    count.textContent=`${all.length} ${tr('个结果','results')} · ${tr('已缓存仓库信息','Cached repository details')} ${known}/${all.length}`;
    sort.querySelectorAll('option').forEach(o=>{o.disabled=false;});
  }
  async function syncCachedData(){
    if(!root.isConnected)return;
    const query=state.loadedQuery;
    try{
      if(!state.pending&&state.query===query){
        const status=await request('/cache-status',undefined,true);
        if(status.revision===state.data?.cache_revision)return;
        const data=await request('?q='+encodeURIComponent(query),undefined,true);
        if(root.isConnected&&!state.pending&&state.query===query&&state.loadedQuery===query){
          const changed=JSON.stringify(state.data?.items)!==JSON.stringify(data.items);
          state.data=data;
          if(changed)drawList();
        }
      }
    }catch(error){/* Keep the existing table when the background request fails. */}
    finally{if(root.isConnected)setTimeout(syncCachedData,5000);}
  }
  activeMarketView = {root, refresh() {drawTags();drawList();}};
  drawTags();drawList();drawDetail();body.scrollTop=state.scroll;body.addEventListener('scroll',()=>{if(root.isConnected)state.scroll=body.scrollTop;},{passive:true});
  if(!state.data&&!state.pending)load(false);
  setTimeout(syncCachedData,5000);
}
