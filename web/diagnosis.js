// Render model output as text, never as executable HTML.
export function renderDiagnosis(container, data, zh) {
  container.replaceChildren();
  const tr = (a,b) => zh ? a : b;
  const node = (tag,text) => {const n=document.createElement(tag); n.textContent=String(text ?? ''); return n;};
  const copy = text => {
    const b=node('button',tr('复制','Copy')); b.className='qbb-lh-btn qbb-lh-btn-ghost';
    b.onclick=async()=>{try{await navigator.clipboard.writeText(text); b.textContent=tr('已复制','Copied');}catch{b.textContent=tr('复制失败，请选中文本复制','Copy failed; select text manually');}};
    return b;
  };
  const code = text => {const block=node('div',''); const pre=node('pre',text); pre.className='qbb-lh-diag-code'; block.append(copy(text),pre); return block;};
  const section = (title,text) => {const s=node('section',''); s.className='qbb-lh-diag-section';s.append(node('h4',title));if(text)s.append(node('p',text));container.append(s);return s;};
  for(const d of data.diagnosis || []) {
    const badge=node('span',zh ? d.label_zh : d.label_en); badge.title=d.evidence;
    badge.className='qbb-lh-diag-badge';container.append(badge);
  }
  const plan=data.plan;
  let full='';
  if(plan) {
    const status=plan.status==='needs_evidence' ? tr('还需一项信息','One detail needed') : tr('建议修复方案','Recommended repair');
    section(status,plan.summary);
    section(tr('原因','Cause'),plan.cause);
    for(const [i,step] of plan.steps.slice(0,4).entries()) {
      const s=section(`${i+1}. ${step.title || ''}`,step.detail);
      if(typeof step.code==='string' && step.code.trim())s.append(code(step.code));
    }
    if(plan.verification)section(tr('修复后验证','Verify after repair'),plan.verification);
    if(plan.rollback){const details=node('details','');details.append(node('summary',tr('回退办法','Rollback')),node('p',plan.rollback));container.append(details);}
    full=[plan.summary,plan.cause,...plan.steps.slice(0,4).map(s=>[s.title,s.detail,s.code].filter(Boolean).join('\n')),plan.verification,plan.rollback].filter(Boolean).join('\n\n');
  } else {
    full=data.result || data.error || '';
    // Providers returning Markdown still get separate copyable code blocks.
    const parts=String(full).split(/```[^\n]*\n([\s\S]*?)```/g);
    parts.forEach((part,i)=>{if(i%2)container.append(code(part));else{const p=node('div',part);p.style.whiteSpace='pre-wrap';container.append(p);}});
  }
  const all=copy(full); all.textContent=tr('复制完整方案','Copy full plan'); container.append(all);
  container.append(node('small',tr('以上为修复建议，未执行任何命令。','These are suggestions. No commands have been executed.')));
}
