import { api } from '../../scripts/api.js';
import { isChineseLocale } from './i18n.js';
import './http.js';

export function previewConflicts(payload, {compareOnly=false, isolate}={}) {
  const zh=isChineseLocale(), tr=(a,b)=>zh?a:b;
  return new Promise(resolve=>{
    const overlay=document.createElement('div');overlay.className='qbb-lh-export-overlay';
    const panel=document.createElement('div');panel.className='qbb-lh-export-modal qbb-dialog-scroll';
    const el=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;};
    panel.append(el('h3',tr('插件兼容性预览','Plugin compatibility preview')));
    const content=el('div',tr('正在检查依赖与节点标识…','Checking dependencies and node identifiers…'));panel.append(content);
    const actions=el('div','');actions.className='qbb-lh-actions';panel.append(actions);
    const button=(text,fn)=>{const b=el('button',text);b.className='qbb-lh-btn';b.onclick=fn;actions.append(b);return b;};
    const finish=value=>{overlay.remove();resolve(value);};
    button(tr('返回','Back'),()=>finish(false));
    overlay.append(panel);document.body.append(overlay);
    api.fetchApi('/qbb_nodeops/conflicts/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
      .then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.error);return data;})
      .then(data=>{
        content.replaceChildren(el('p',tr(`已检查 ${data.checked} 个插件；确认冲突 ${data.confirmed} 项。`,`Checked ${data.checked} plugins; ${data.confirmed} confirmed conflicts.`)));
        content.append(el('p',tr('搬家会安装目标依赖。选择回收原插件后，仍需您启动目标实例并确认插件可用，再点击确认回收；系统会重新检查依赖及文件一致性，原插件移入回收站。','Migration installs target dependencies. Recycling requires you to launch the target and confirm plugins work, followed by dependency and file checks. Originals are moved to the recycle bin.')));
        for(const finding of data.findings){
          const card=el('section','');card.className='qbb-lh-card';
          const level=({confirmed:tr('确认冲突','Confirmed'),suspected:tr('疑似冲突','Suspected'),unknown:tr('信息不足','Unknown')})[finding.level];
          card.append(el('strong',`${level} · ${finding.plugins.join(' ↔ ')}`),el('p',finding.message));
          const evidence=el('pre',finding.evidence.join('\n'));evidence.className='qbb-pre';card.append(evidence);content.append(card);
        }
        if(data.existing_findings?.length){
          const existing=el('details','');
          existing.append(el('summary',tr('目标已有冲突（不阻断本次搬家）','Existing target conflicts (do not block this migration)')));
          for(const finding of data.existing_findings)existing.append(el('p',`${finding.plugins.join(' ↔ ')}: ${finding.message}`));
          content.append(existing);
        }
        const details=el('details','');details.append(el('summary',tr(`检查范围与缺失信息 (${data.unknown.length})`,`Inspection limits (${data.unknown.length})`)));
        for(const note of data.unknown)details.append(el('p',note));content.append(details);
        if(data.logs.length){const logs=el('details','');logs.append(el('summary',tr('关联加载错误','Related load errors')));for(const log of data.logs)logs.append(el('p',`${log.name}: ${log.reason}`));content.append(logs);}
        if(!compareOnly && !data.confirmed){
          button(tr('保留原插件并搬家','Migrate and keep originals'),()=>finish({deleteSource:false}));
          button(tr('搬家后确认回收原插件','Migrate, then confirm recycling originals'),()=>finish({deleteSource:true}));
        }
      }).catch(error=>{content.textContent=error.message;});
  });
}
