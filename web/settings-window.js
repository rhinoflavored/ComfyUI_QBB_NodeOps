// Shared positioning for settings windows; no persisted off-screen coordinates.
export function setupSettingsWindow(overlay, panel) {
  const title = panel.querySelector('h3');
  if (!title) return;
  Object.assign(panel.style, {position:'fixed', margin:'0', transform:'none', boxSizing:'border-box', maxWidth:'calc(100vw - 24px)', maxHeight:'calc(100vh - 24px)', overflow:'auto'});
  title.style.cursor='move'; title.style.touchAction='none'; title.style.userSelect='none';
  panel.prepend(title);
  let moved=false, drag=null;
  function place(x, y) {
    const rect=panel.getBoundingClientRect();
    panel.style.left=Math.max(12,Math.min(x,window.innerWidth-rect.width-12))+'px';
    panel.style.top=Math.max(12,Math.min(y,window.innerHeight-rect.height-12))+'px';
  }
  function layout(){
    const rect=panel.getBoundingClientRect();
    place(moved?rect.left:(window.innerWidth-rect.width)/2,moved?rect.top:(window.innerHeight-rect.height)/2);
  }
  title.addEventListener('pointerdown',event=>{
    if(event.button!==0 || event.target.closest('button,a,input,select'))return;
    const rect=panel.getBoundingClientRect();drag={id:event.pointerId,x:event.clientX-rect.left,y:event.clientY-rect.top};
    title.setPointerCapture(event.pointerId);event.preventDefault();
  });
  title.addEventListener('pointermove',event=>{
    if(!drag||drag.id!==event.pointerId)return;
    moved=true;place(event.clientX-drag.x,event.clientY-drag.y);
  });
  const end=()=>{drag=null;};
  title.addEventListener('pointerup',end);title.addEventListener('pointercancel',end);title.addEventListener('lostpointercapture',end);
  const resize=new ResizeObserver(layout);resize.observe(panel);resize.observe(overlay);
  const removal=new MutationObserver(()=>{if(!overlay.isConnected){resize.disconnect();removal.disconnect();}});
  removal.observe(document.body,{childList:true});
  layout();
}
