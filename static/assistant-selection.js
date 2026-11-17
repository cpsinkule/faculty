// ASSISTANT / FRONTEND: workspace-owned selection helpers. Read visible content, never record the screen.
import {selectedRecord} from './assistant-context.js';

export const MAX_TEXT=12000, MAX_RECORDS=40, MAX_IMAGES=8, MAX_BYTES=4*1024*1024;
const PRIVATE=/password|passcode|secret|token|api[\s_-]?key|credential|authorization|cookie|submission[\s_-]?(code|link)/i;
const EXCLUDE='script,style,noscript,template,[hidden],[inert],[data-assistant-private],#assistant-panel,.assistant-area-selector,.assistant-area-bubble,.sr-only,.katex-mathml';
const words=value=>String(value||'').replace(/\s+/g,' ').trim();
const bounded=(value,limit)=>Array.from(value).slice(0,limit).join('');
const canvasOf=(width,height)=>Object.assign(document.createElement('canvas'),{width,height});

export function selectionRect(start,end,width,height) {
  const x1=Math.max(0,Math.min(width,start.x)),x2=Math.max(0,Math.min(width,end.x));
  const y1=Math.max(0,Math.min(height,start.y)),y2=Math.max(0,Math.min(height,end.y));
  return {x:Math.min(x1,x2),y:Math.min(y1,y2),width:Math.abs(x2-x1),height:Math.abs(y2-y1)};
}
export function intersect(a,b) {
  const x=Math.max(a.x,b.x),y=Math.max(a.y,b.y),right=Math.min(a.x+a.width,b.x+b.width),bottom=Math.min(a.y+a.height,b.y+b.height);
  return right>x && bottom>y ? {x,y,width:right-x,height:bottom-y} : null;
}
/** Natural-image coordinates, including object-fit letterboxing and browser zoom. */
export function imageCrop(rect,bounds,width,height,fit='fill',position='50% 50%') {
  let sx=bounds.width/width,sy=bounds.height/height;
  if (fit!=='fill') sx=sy=fit==='cover'?Math.max(sx,sy):fit==='none'?1:fit==='scale-down'?Math.min(1,sx,sy):Math.min(sx,sy);
  const parts=position.split(/\s+/),offset=(value,space)=>value?.endsWith('%')?parseFloat(value)*space/100:Number.parseFloat(value)||0;
  const drawn={x:bounds.x+offset(parts[0],bounds.width-width*sx),y:bounds.y+offset(parts[1]||parts[0],bounds.height-height*sy),width:width*sx,height:height*sy};
  const area=intersect(intersect(rect,bounds)||{x:0,y:0,width:0,height:0},drawn);
  if(!area)return null;
  const x=Math.max(0,Math.ceil((area.x-drawn.x)/sx)),y=Math.max(0,Math.ceil((area.y-drawn.y)/sy));
  const right=Math.min(width,Math.floor((area.x+area.width-drawn.x)/sx)),bottom=Math.min(height,Math.floor((area.y+area.height-drawn.y)/sy));
  return right>x&&bottom>y?{x,y,width:right-x,height:bottom-y,area,sx,sy}:null;
}
function sensitive(element) {
  if(element.closest(EXCLUDE))return true;
  const field=element.closest('input,textarea,select,label');
  if(field && (field.querySelector('input[type=password]') || PRIVATE.test([field.getAttribute('name'),field.getAttribute('autocomplete'),field.getAttribute('aria-label'),field.tagName==='LABEL'?field.textContent:''].join(' '))))return true;
  const dialog=element.closest('dialog');
  return Boolean(dialog?.querySelector('input[type=password],.aim-signin-credentials,[data-assistant-private]'));
}
function visiblePart(element,rect) {
  let part=intersect(rect,{x:0,y:0,width:innerWidth,height:innerHeight});
  for(let current=element;current && part;current=current.parentElement) {
    const css=getComputedStyle(current);
    if(css.display==='none'||css.visibility==='hidden'||css.visibility==='collapse'||Number(css.opacity)===0)return null;
    const box=current.getBoundingClientRect(),x=/hidden|clip|auto|scroll/.test(css.overflowX),y=/hidden|clip|auto|scroll/.test(css.overflowY);
    if(x||y)part=intersect(part,{x:x?box.x+current.clientLeft:part.x,y:y?box.y+current.clientTop:part.y,width:x?current.clientWidth:part.width,height:y?current.clientHeight:part.height});
  }
  return part;
}
function exposed(element,rect,textNode=null) {
  for(const dx of [.2,.5,.8])for(const dy of [.2,.5,.8]) {
    const top=document.elementsFromPoint(rect.x+rect.width*dx,rect.y+rect.height*dy).find(item=>!item.closest('.assistant-area-selector'));
    if(top && (top===element || (textNode?top.contains(textNode):element.contains(top))))return true;
  }
  return false;
}
function within(element,rect) {
  if(sensitive(element))return null;
  const clipped=visiblePart(element,element.getBoundingClientRect()),part=clipped&&intersect(clipped,rect);
  return part && exposed(element,part)?part:null;
}
function fieldText(field) {
  if(field.type==='checkbox'||field.type==='radio')return field.checked?'Checked':'Not checked';
  if(field.tagName==='SELECT')return [...field.selectedOptions].map(option=>option.textContent).join(', ');
  return field.isContentEditable?field.innerText:field.value;
}
function labelFor(field) {
  const labels=[...(field.labels||[])].map(label=>{
    const copy=label.cloneNode(true);copy.querySelectorAll('input,textarea,select,[hidden],.sr-only').forEach(node=>node.remove());return copy.textContent;
  }).join(' ');
  return bounded(words(field.getAttribute('aria-label') || labels || field.getAttribute('placeholder') || field.getAttribute('name') || 'Selected field'),500);
}
function cellLabel(cell) {
  const row=cell.closest('tr'),table=cell.closest('table');
  const heading=[...(table?.querySelectorAll('thead [data-item]')||[])].find(node=>node.dataset.item===cell.dataset.item);
  return bounded(words([row?.querySelector('.gb-name-text')?.textContent,heading?.querySelector('.gb-head-title')?.textContent].filter(Boolean).join(' · ')),500);
}
function sourceOf(element) {
  const raw=element.currentSrc||element.getAttribute('src')||'';
  if(!raw)return '';
  const url=new URL(raw,location.href);
  if(url.protocol==='data:')return '';
  if(url.origin!==location.origin || !url.pathname.startsWith('/api/') || [...url.searchParams.keys()].some(key=>PRIVATE.test(key)))throw new Error('This image cannot be selected here. Open its Faculty preview first.');
  if(url.pathname.startsWith('/api/student-photos/') || (url.searchParams.get('route')||'').startsWith('/api/student-photos/'))return null;
  return url.pathname+url.search;
}
async function drawable(element) {
  if(element.tagName.toLowerCase()!=='svg') {
    if(element.tagName==='IMG' && !element.complete)await Promise.race([element.decode(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('The image is still loading. Try selecting it again.')),8000))]);
    const width=element.naturalWidth||element.width,height=element.naturalHeight||element.height;
    if(!width||!height)throw new Error('The image did not load. Open its preview and try again.');
    return {image:element,width,height};
  }
  const copy=element.cloneNode(true),box=element.getBoundingClientRect();
  const originals=[element,...element.querySelectorAll('*')],copies=[copy,...copy.querySelectorAll('*')];
  for(let index=0;index<originals.length;index++) {
    const css=getComputedStyle(originals[index]);
    for(const property of ['fill','fill-opacity','stroke','stroke-width','stroke-opacity','font-family','font-size','font-weight','text-anchor'])copies[index].setAttribute(property,css.getPropertyValue(property));
  }
  copy.querySelectorAll('script,foreignObject,image,a').forEach(node=>node.remove());
  copy.setAttribute('xmlns','http://www.w3.org/2000/svg');copy.setAttribute('width',String(box.width));copy.setAttribute('height',String(box.height));
  const image=new Image();image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(copy));
  await image.decode();return {image,width:box.width,height:box.height};
}

/** Explicitly selected content only; hidden text, credentials, and student photos never enter the selection. */
export async function collectSelection(rect,root=document.body) {
  const dialog=root.closest('dialog');
  if(dialog && [...dialog.querySelectorAll('input')].some(input=>input.type==='password'||PRIVATE.test([input.name,input.getAttribute('aria-label'),input.labels?.[0]?.textContent].join(' '))))
    throw new Error('Sign-in and access-key views cannot be sent to the assistant.');
  const lines=[],records=[],pictures=[],omitted=new Set(),seen=new Set();let truncated=false;
  const addRecord=(element,label,value='',draft=false)=>{
    const record=selectedRecord(element),key=JSON.stringify([record,label,value]);
    if(seen.has(key))return;seen.add(key);
    if(records.length>=MAX_RECORDS){truncated=true;return;}
    records.push({record,label,value:bounded(words(value),2000),draft});
  };
  for(const cell of root.querySelectorAll('td[data-cell][data-row]')) {
    if(!within(cell,rect))continue;
    const editor=cell.querySelector('input'),value=editor?editor.value:cell.innerText,label=cellLabel(cell);
    addRecord(cell,label,value,Boolean(editor));lines.push(`${label}: ${words(value)}${editor?' (unsaved edit)':''}`);
  }
  for(const field of root.querySelectorAll('input:not([type=hidden]):not([type=password]),textarea,select,[contenteditable=true]')) {
    if(field.closest('[data-cell][data-row]') || !within(field,rect))continue;
    const label=labelFor(field);if(PRIVATE.test(label))continue;
    const value=words(fieldText(field));lines.push(`${label}: ${value}${!field.readOnly?' (editable value; may be unsaved)':''}`);
    addRecord(field,label,value,!field.readOnly);
  }
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node,examined=0;
  while((node=walker.nextNode())) {
    const element=node.parentElement;
    if(!element || sensitive(element) || element.closest('[data-cell][data-row],input,textarea,select,[contenteditable=true],svg') || !words(node.textContent))continue;
    const range=document.createRange();range.selectNodeContents(node);
    if(![...range.getClientRects()].some(box=>intersect(box,rect)))continue;
    let selected='';
    for(const match of node.textContent.matchAll(/\S+/gu)) {
      if(++examined>10000){truncated=true;break;}
      range.setStart(node,match.index);range.setEnd(node,match.index+match[0].length);
      if([...range.getClientRects()].some(box=>{const part=visiblePart(element,box),hit=part&&intersect(part,rect);return hit&&exposed(element,hit,node);}))selected+=(selected?' ':'')+match[0];
    }
    if(selected){lines.push(selected);const record=selectedRecord(element);if(Object.keys(record).length)addRecord(element,'Selected text');}
    if(examined>10000)break;
  }
  let density=1;
  for(const element of root.querySelectorAll('img,canvas,svg')) {
    const part=within(element,rect);if(!part)continue;
    const bounds=element.getBoundingClientRect(),tag=element.tagName.toLowerCase();
    if(tag==='svg' && (bounds.width<80 || bounds.height<50))continue;
    const source=sourceOf(element);
    if(source===null){omitted.add('Student photos are not sent to the assistant.');continue;}
    if(pictures.length>=MAX_IMAGES){truncated=true;continue;}
    const input=await drawable(element),css=getComputedStyle(element);
    const rx=element.offsetWidth?bounds.width/element.offsetWidth:1,ry=element.offsetHeight?bounds.height/element.offsetHeight:1;
    const edge=key=>parseFloat(css[key])||0;
    const left=(edge('borderLeftWidth')+edge('paddingLeft'))*rx,right=(edge('borderRightWidth')+edge('paddingRight'))*rx;
    const top=(edge('borderTopWidth')+edge('paddingTop'))*ry,bottom=(edge('borderBottomWidth')+edge('paddingBottom'))*ry;
    const content={x:bounds.x+left,y:bounds.y+top,width:bounds.width-left-right,height:bounds.height-top-bottom};
    const crop=imageCrop(part,content,input.width,input.height,css.objectFit,css.objectPosition);
    if(!crop)continue;
    density=Math.max(density,1/crop.sx,1/crop.sy);
    pictures.push({...input,crop,label:bounded(words(element.getAttribute('alt')||element.getAttribute('aria-label')||element.closest('figure')?.querySelector('figcaption')?.textContent||'Selected image'),300),source});
  }
  const text=lines.join('\n');if(text.length>MAX_TEXT)truncated=true;
  const selection={text:bounded(text,MAX_TEXT),records,images:pictures.map(({label,source})=>({label,source})),omitted:[...omitted],truncated};
  if(!selection.text && !records.length && !pictures.length)throw new Error(omitted.size?[...omitted][0]:'The box contains no readable content. Drag over text, a record, or an image.');
  let blob=null,preview='';
  if(pictures.length) {
    const scale=Math.min(density,3,3072/Math.max(rect.width,rect.height),Math.sqrt(12_000_000/(rect.width*rect.height)));
    const canvas=canvasOf(Math.max(1,Math.round(rect.width*scale)),Math.max(1,Math.round(rect.height*scale))),ctx=canvas.getContext('2d');
    ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);
    for(const {image,crop} of pictures)ctx.drawImage(image,crop.x,crop.y,crop.width,crop.height,(crop.area.x-rect.x)*scale,(crop.area.y-rect.y)*scale,crop.area.width*scale,crop.area.height*scale);
    try {blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(blob?.size>MAX_BYTES)blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.9));preview=canvas.toDataURL(blob?.type||'image/png',.9);}
    catch {throw new Error('This image cannot be read here. Open its Faculty preview and select it again.');}
    if(!blob||blob.size>MAX_BYTES)throw new Error('Select a smaller image area; this selection exceeds 4 MiB.');
  }
  return {selection,blob,preview};
}
