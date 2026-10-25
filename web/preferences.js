import {api} from '../../scripts/api.js';
import {installPageHelp} from './page-help.js';
import {setupSettingsWindow} from './settings-window.js';
import {isChineseLocale} from './i18n.js';
import './http.js';
export let preferences={popup_errors:false,popup_updates:false};
export async function loadPreferences(){
 const r=await api.fetchApi('/qbb_nodeops/preferences');if(!r.ok)throw new Error(r.statusText);
 preferences=await r.json();return preferences;
}
export function showPreferences(){
 const tr=(a,b)=>isChineseLocale()?a:b;
 const el=(tag,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
 const overlay=el('div');overlay.className='qbb-lh-export-overlay';const panel=el('div');panel.className='qbb-lh-export-modal';
 panel.append(el('h3',tr('插件设置','NodeOps settings')),el('p',tr('控制 QBB NodeOps 的自动提醒。关闭弹窗后，仍可从顶栏主动打开插件。设置保存在当前实例，重启后保留。','Control automatic notifications. The toolbar remains available when popups are disabled. Preferences persist for this instance.')));
 const fields={};for(const [key,label] of [['popup_errors',tr('启动发现加载异常时自动弹窗','Show popup for startup loading errors')],['popup_updates',tr('启动检查发现插件更新时自动弹窗','Show popup when startup checks find updates')]]){
 const row=el('label');row.className='qbb-lh-check-row';const check=el('input');check.type='checkbox';check.checked=!!preferences[key];fields[key]=check;row.append(check,document.createTextNode(label));panel.append(row);
 }
 const status=el('p');status.setAttribute('role','status');panel.append(status);const actions=el('div');actions.className='qbb-lh-actions';
 const cancel=el('button',tr('关闭','Close'));cancel.className='qbb-lh-btn';cancel.onclick=()=>overlay.remove();
 const save=el('button',tr('保存设置','Save settings'));save.className='qbb-lh-btn qbb-lh-btn-primary';
 save.onclick=async()=>{save.disabled=true;try{const r=await api.fetchApi('/qbb_nodeops/preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.checked])))});const data=await r.json();if(!r.ok)throw new Error(data.error);preferences=data;status.textContent=tr('已保存，下次自动提醒按此设置执行。','Saved. Future automatic notifications will follow these settings.');}catch(e){status.textContent=e.message;}finally{save.disabled=false;}};
 actions.append(cancel,save);panel.append(actions);overlay.append(panel);document.body.append(overlay);
 panel.querySelector('p').dataset.pageHelp='';
 panel.dataset.helpPage='preferences';
 const disposeHelp=installPageHelp(panel);
 setupSettingsWindow(overlay,panel);
 cancel.onclick=()=>{disposeHelp();overlay.remove();};
}
