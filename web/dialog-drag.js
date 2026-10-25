// Pointer capture keeps dragging reliable outside the title bar.
export function makeDialogDraggable(dialog,handle){
 let drag=null,moved=false;
 handle.style.cursor='move';handle.style.touchAction='none';handle.style.userSelect='none';
 const place=(x,y)=>{
  const rect=dialog.getBoundingClientRect();
  dialog.style.left=Math.max(0,Math.min(x,Math.max(0,window.innerWidth-rect.width)))+'px';
  dialog.style.top=Math.max(0,Math.min(y,Math.max(0,window.innerHeight-rect.height)))+'px';
 };
 const stop=event=>{if(!drag||event.pointerId!==drag.id)return;const id=drag.id;drag=null;if(handle.hasPointerCapture?.(id))handle.releasePointerCapture(id);};
 const down=event=>{
  if(drag||event.button!==0||event.target.closest('button,input,select,textarea,a,[contenteditable]'))return;
  const rect=dialog.getBoundingClientRect();
  Object.assign(dialog.style,{position:'fixed',margin:'0',right:'auto',bottom:'auto',boxSizing:'border-box',width:rect.width+'px',left:rect.left+'px',top:rect.top+'px'});
  drag={id:event.pointerId,x:event.clientX-rect.left,y:event.clientY-rect.top};moved=true;
  handle.setPointerCapture(event.pointerId);event.preventDefault();
 };
 const move=event=>{if(drag&&event.pointerId===drag.id){place(event.clientX-drag.x,event.clientY-drag.y);event.preventDefault();}};
 const resize=()=>{if(moved){const rect=dialog.getBoundingClientRect();place(rect.left,rect.top);}};
 handle.addEventListener('pointerdown',down);handle.addEventListener('pointermove',move);
 for(const type of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(type,stop);
 window.addEventListener('resize',resize);
 return ()=>{if(drag)stop({pointerId:drag.id});handle.removeEventListener('pointerdown',down);handle.removeEventListener('pointermove',move);for(const type of ['pointerup','pointercancel','lostpointercapture'])handle.removeEventListener(type,stop);window.removeEventListener('resize',resize);};
}
