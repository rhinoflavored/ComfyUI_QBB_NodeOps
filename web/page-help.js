import { isChineseLocale } from "./i18n.js";
import { pageHelpCopy } from './help-copy.js';

const explanations = [
  ".qbb-page-subtitle", ".qbb-cleanup-intro", ".qbb-cleanup-help",
  ".qv-heading p", ".qv-notice", ".qbb-versions > .qv-muted",
  // Version storage disclosures own their help, backup rows and empty states.
  // Keep those paragraphs visible inside the disclosure instead of hiding them here.
  ".qv-composer small", ".qm-header p", ".qm-notice", ".qm-settings > p.qm-muted",
  ".qbb-hint", ".qbb-lh-export-hint", "[data-page-help]",
].join(",");

export function installPageHelp(host) {
  if (host.matches(".qbb-lh-export-modal")) {
    const title = host.querySelector("h3");
    if (title && !title.dataset.draggable) {
      title.dataset.draggable = "true";
      title.style.cursor = "move";
      title.style.touchAction = "none";
      title.style.userSelect = "none";
      let drag = null;
      title.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        const rect = host.getBoundingClientRect();
        drag = {id:event.pointerId, x:event.clientX - rect.left, y:event.clientY - rect.top};
        Object.assign(host.style, {position:"fixed", left:rect.left+"px", top:rect.top+"px", width:rect.width+"px", margin:"0", transform:"none"});
        title.setPointerCapture(event.pointerId);
        event.preventDefault();
        event.stopPropagation();
      });
      title.addEventListener("pointermove", event => {
        if (!drag || drag.id !== event.pointerId) return;
        const rect = host.getBoundingClientRect();
        host.style.left = Math.max(0, Math.min(event.clientX-drag.x, innerWidth-rect.width))+"px";
        host.style.top = Math.max(0, Math.min(event.clientY-drag.y, innerHeight-Math.min(rect.height, innerHeight)))+"px";
      });
      const end = () => { drag = null; };
      title.addEventListener("pointerup", end);
      title.addEventListener("pointercancel", end);
      title.addEventListener("lostpointercapture", end);
    }
  }
  const help = document.createElement("details");
  help.className = "qbb-page-help";
  const toggle = document.createElement("summary");
  toggle.textContent = isChineseLocale() ? "说明" : "Help";
  const content = document.createElement("div");
  content.className = "qbb-page-help-content";
  help.append(toggle, content);
  const refresh = () => {
    if (!host.contains(help)) host.prepend(help);
    const nodes = [...host.querySelectorAll(explanations)].filter(node => !help.contains(node));
    for (const old of host.querySelectorAll(".qbb-explanation")) {
      if (!nodes.includes(old)) old.classList.remove("qbb-explanation");
    }
    for (const node of nodes) node.classList.add("qbb-explanation");
    if (!help.open) return;
    content.replaceChildren();
    const authored=pageHelpCopy(host,isChineseLocale());
    if(authored){for(const text of authored){const p=document.createElement('p');p.textContent=text;content.append(p);}return;}
    const texts = new Set();
    for (const node of nodes) {
      const paragraphs = node.matches("details") ? [...node.querySelectorAll("p")] : [node];
      for (const paragraph of paragraphs) {
        const text = paragraph.textContent.trim();
        if (!text || texts.has(text)) continue;
        texts.add(text);
        const p = document.createElement("p");
        p.textContent = text;
        content.append(p);
      }
    }
    if (!texts.size) content.textContent = isChineseLocale() ? "此页面暂无补充说明。" : "No additional instructions for this page.";
  };
  const observer = new MutationObserver(() => {
    observer.disconnect();
    refresh();
    observe();
  });
  const observe = () => observer.observe(host, {childList:true, subtree:true, characterData:true});
  help.ontoggle = () => { observer.disconnect(); refresh(); observe(); };
  refresh();
  observe();
  return () => { observer.disconnect(); help.remove(); };
}
