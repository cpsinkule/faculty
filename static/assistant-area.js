// ASSISTANT / FRONTEND: temporary selection of Faculty content, with no screen sharing.
import {collectSelection, selectionRect} from './assistant-selection.js';
import {conversationCards, batchClick, submitActionReview, actionReviewMessage, checkActionMail} from './assistant.js';
import {chatMarkup, chatTitle, composerMarkup, contextChips, createChatView, createComposer, emptyChatMarkup, eventMarkup, pageName, sendMessage, turnsOf} from './assistant-chat.js';
import {questionMarkup, chooseQuestionOption} from './assistant-workspace.js';
import {registerLeaveGuard, errorMessage, runFormAction, holdCaptureFocus, setBusy} from './ui.js';
import {patchChildren} from './dom-patch.js';
import {memoryReworded,memoryUndone} from './assistant-memory.js';
import {recordContext, routeContext} from './assistant-context.js';
import {createAttachments} from './assistant-attach.js';
import {attachPickers} from './assistant-pickers.js';

const BASE='/api/assistant';
const topLayer = (node, parent) => { (parent || document.body).append(node); node.setAttribute('popover','manual'); node.showPopover(); };
const drop = node => { if (node?.isConnected) { try{node.hidePopover?.();}catch{} node.remove(); } };
const READ_RETRY=[900,2000,5000];
const GEOMETRY='faculty:assistant:window',MIN_W=360,MIN_H=320,CHANGES_W=440;

export function createAreaAssistant({api,toast,icon,registerShortcut,openConversation,prepareSelection=async()=>null}) {
  // The bubble (floating chat or selected-area chat) and its conversation; `generation` guards its requests.
  let bubble=null,parent=null,releaseFocus=null;
  let generation=0,poll=0,conversation=null,events=[],actions=new Map(),batches=[],after=0,busy=false,anchor=null,view=null;
  let chatMode='selection',readFailures=0,reconnecting=false,readingContext=false;
  // ASSISTANT-MEMORY "STILL BEING CONSIDERED": the view's `remembering` while a sent message's note is not yet as it will stay;
  // the chat is read on until it is gone, though the answer is in.
  let remembering=null;
  // MOVABLE (requirement, 2026-10-09): where the instructor dragged this chat, kept
  // while it is open; null keeps the usual place. ONE CHAT (same day: a Command-hold box "populates that specific opened chat
  // window"): a box sent while the open chat is answering or asking waits here and goes to it once it is free. `gone`: the
  // conversation has ended on the server, so a new box starts a new chat instead.
  let moved=null,queued=null,gone=false;
  // REAL WINDOW (requirement, 2026-10-09): `size` is the size the instructor dragged it to;
  // size and place are remembered in this browser (GEOMETRY) for the next chat. `minimised`: put away as a small bar at the
  // window's lower right, its conversation still running; `fresh`: an answer came in while it was put away.
  let size=null,minimised=false,fresh=false;
  // ATTACH IN CHAT: this bubble's paperclip (files that go with its next message); made with the bubble, gone with it.
  let attach=null;
  // CHANGES BESIDE THE CHAT (requirement, 2026-10-10): the conversation's cards are drawn in a panel at the window's right, which
  // widens for it; the conversation keeps one line where each was made. `cardCount`: the cards drawn; a new one opens the panel.
  // `changesHidden`: the instructor put the panel away (until the next card). `changesHTML`: what the panel shows now.
  let cardCount=0,changesHidden=false,changesHTML='';
  // The shared conversation pane and composer (assistant-chat.js), made with the bubble. `startedAt`: when the running turn
  // began by this browser's clock; `sending`/`sendingCapture`: a message or a selection is on its way (a second one waits).
  let pane=null,composer=null,startedAt=0,sending=false,sendingCapture=false;
  // A selection being drawn, kept apart from the bubble: an open bubble is only hidden (`stashed`) until a box is sent, so a
  // cancelled selection gives it back unchanged. `picking` guards the selection's own steps.
  let selector=null,prepared=null,picking=0,starting=false,holdSelecting=false,finishSelection=null,pickParent=null,pickRelease=null,pickView=null,stashed=false,pickPointer=false;
  // Unsent here: typed words, attached files, or a message waiting for the running answer (QUEUE ONE).
  const draft=() => Boolean(composer?.unsent());
  // The floating chat survives navigation, so only a selection chat asks before leaving; a reload still asks for both.
  registerLeaveGuard(() => !readingContext && chatMode!=='chat' && draft() ? 'Leave this Assistant chat? Your message has not been sent.' : null);
  window.addEventListener('beforeunload',event=>{if(chatMode==='chat'&&draft()){event.preventDefault();event.returnValue='';}});
  const remembered=() => {try{const found=JSON.parse(localStorage.getItem(GEOMETRY)||'null');return found&&typeof found==='object'?found:{};}catch{return {};}};
  // The size is remembered from either kind of chat; the place only from the floating chat (a selection chat sits by its selection).
  const remember=() => {
    const whole=Object.fromEntries(Object.entries({...remembered(),...(size||{}),...(chatMode==='chat'&&moved?moved:{})}).map(([key,value])=>[key,Math.round(Number(value))]));
    try{localStorage.setItem(GEOMETRY,JSON.stringify(whole));}catch{}
  };
  // Ends a conversation this window owned.
  const retire=id => api(`${BASE}/conversations/${id}/close`,'POST',{});
  const removeProtection=() => {releaseFocus?.();releaseFocus=null;};
  const restoreContent=() => {prepared?.restore();prepared=null;};
  const reveal=() => { if(!bubble)return; try{bubble.showPopover();}catch{} position(); };
  /** Ends only the selector (or its preparation); a hidden bubble comes back unchanged. */
  function closeSelection({focus=true,toChat=focus}={}) {
    picking++;starting=false;holdSelecting=false;finishSelection=null;pickParent=null;pickView=null;
    drop(selector);selector=null;restoreContent();
    const held=pickRelease;pickRelease=null;held?.({focus});
    if(stashed){stashed=false;reveal();if(!held&&toChat)bubble?.querySelector('#area-question')?.focus({preventScroll:true});}
  }
  function close({stop=true,restore=true}={}) {
    stashed=false;closeSelection({focus:restore});
    generation++;clearTimeout(poll);drop(bubble);bubble=null;
    // This bubble owns its conversation: closing ends it on the server too (stopping any analysis), as the drawer's New does.
    // Otherwise every selection would keep a conversation, its pixels and model process, and push the drawer's out (8 kept).
    if (stop && conversation) void retire(conversation.id).catch(error=>toast(errorMessage(error),true));
    conversation=null;events=[];after=0;busy=false;remembering=null;readFailures=0;reconnecting=false;
    actions=new Map();batches=[];moved=null;queued=null;gone=false;attach=null;size=null;minimised=false;fresh=false;
    cardCount=0;changesHidden=false;changesHTML='';
    composer?.stop();composer=null;pane=null;startedAt=0;sending=false;sendingCapture=false;
    if (restore) removeProtection();
  }
  const discard=() => !draft() || window.confirm('Discard the message you have not sent?');
  // Page context never counts this bubble's own draft as the page's unsaved work.
  const context=() => { readingContext=true; try { return {title:document.getElementById('page-title')?.textContent || '',trail:document.getElementById('breadcrumb')?.textContent || '',context:recordContext()}; } finally { readingContext=false; } };
  const liveView=() => ({page:location.hash || '#overview',view:context()});
  const activeDialog=() => [...document.querySelectorAll('dialog[open]')].at(-1) || null;
  const idle=() => Boolean(conversation?.id && !busy && !conversation.question);
  // Only a selection still being drawn, prepared or analyzed for the first time is released when the tab is hidden.
  const unfinished=() => !conversation?.capture || (busy && !events.some(e=>e.kind==='assistant'||e.kind==='error'));
  const changesOpen=() => cardCount>0&&!changesHidden;
  /** The panel's width: 440 px, less only where the window could not also hold the narrowest conversation. */
  const changesWidth=() => changesOpen()?Math.min(CHANGES_W,Math.max(0,innerWidth-32-MIN_W)):0;
  function position() {
    if (!bubble || !anchor) return;
    if (minimised) {
      // Put away: a small bar at the window's lower right.
      const narrow=Math.min(320,innerWidth-32);
      Object.assign(bubble.style,{width:`${narrow}px`,height:'auto',maxHeight:'none'});
      Object.assign(bubble.style,{left:`${innerWidth-narrow-16}px`,top:`${innerHeight-bubble.offsetHeight-16}px`});
      return;
    }
    // 460 by 660 unless the instructor sized it (never under 360 by 320), always within the window. The panel of changes adds
    // its own width at the right: the conversation keeps the width it had.
    const side=changesWidth(),chat=Math.min(Math.max(MIN_W,size?.w||460),innerWidth-32-side),width=chat+side;
    const height=Math.min(Math.max(MIN_H,size?.h||660),innerHeight-32);
    bubble.style.width=`${width}px`;bubble.style.maxHeight=`${height}px`;
    bubble.querySelector('[data-area-changes]').style.flexBasis=`${side}px`;
    // A chosen size holds for both kinds of chat; otherwise a selection chat is as tall as what it holds (the sheet's own rule).
    bubble.style.height=size?`${height}px`:'';
    if (moved) {
      // Where the instructor put it, always whole in the window: a smaller window or a longer answer pulls it back in.
      const tall=bubble.offsetHeight||height;
      bubble.style.left=`${Math.max(8,Math.min(innerWidth-width-8,moved.x))}px`;
      bubble.style.top=`${Math.max(8,Math.min(innerHeight-tall-8,moved.y))}px`;
      return;
    }
    // The conversation stays where it would be without the panel, unless the panel then would not fit in the window.
    bubble.style.left=`${Math.max(16,Math.min(innerWidth-width-16,chatMode==='chat'?(innerWidth-chat)/2:anchor.x+anchor.width+12))}px`;
    bubble.style.top=`${chatMode==='chat'?(innerHeight-height)/2:Math.max(16,Math.min(innerHeight-height-16,anchor.y))}px`;
  }
  /** The last turn has an answer and did not fail. */
  const answered=() => { const turn=turnsOf(events).at(-1)||[]; return turn.some(e=>e.kind==='assistant')&&!turn.some(e=>e.kind==='error'); };
  function absorb(result) {
    for (const event of result.events || []) if (event.seq>after) { events.push(event);after=event.seq; }
    const was=busy;
    conversation={...conversation,...result,events:undefined};busy=Boolean(result.busy);remembering=result.remembering||null;
    if(was&&!busy&&minimised)fresh=true;
    if(busy&&!startedAt){const at=Date.parse((turnsOf(events).at(-1)||[])[0]?.at);startedAt=was||!Number.isFinite(at)?Date.now():Math.min(at,Date.now());}
    if(!busy)startedAt=0;
    draw();flush();
    // QUEUE ONE: the waiting message goes once the answer is in (after a waiting selection); a stop, a failure or a question
    // gives it back to the box.
    if(was&&!busy) {
      const ready=!conversation.question&&!conversation.error&&answered();
      if(!ready)composer?.settle({ready:false});
      else if(!queued&&!sendingCapture)composer?.settle({ready:true});
    }
  }
  /** ONE CHAT: a box that waited for the chat to finish its answer (or its question to be answered) goes now, once. */
  function flush() {
    if(!queued||!idle())return;
    const waiting=queued;queued=null;
    if(waiting.token!==generation)return;
    draw();void sendCapture(conversation.id,waiting.rect,waiting.selection,waiting.blob,waiting.chosen,waiting.token,false);
  }
  /** What a typed message carries: the floating chat sends the page and hints on screen now; a selection chat keeps those of
   * its selection. A record the instructor removed from the chips is left out (the page's own address hints stay). */
  function carried(message='') {
    const now=chatMode==='chat'?liveView():view;
    // PICKERS: the records the message's "@" names stand for go with it.
    const record={...(composer?.dropped('record')?routeContext(now.page):now.view.context.record),...(composer?.mentioned(message)||{})};
    return {page:now.page,view:{...now.view,context:{...now.view.context,record}}};
  }
  function draw() {
    if (!bubble||!pane) return;
    const plain=chatMode==='chat',found=conversationCards(events,actions,batches);
    const last=busy?null:events.findLast(e=>e.kind==='user');
    // One drawing for the drawer and this window (assistant-chat.js); the selection itself is shown above the conversation.
    // CHANGES BESIDE THE CHAT: here a card is one line in the conversation and whole in the panel beside it.
    const html=plain&&!events.length?emptyChatMarkup(location.hash):chatMarkup(events,busy,event=>eventMarkup(event,{cards:found.line,capture:conversation?.capture,selection:false,edit:last,undone:memoryUndone(events),reworded:memoryReworded(events),rewording:remembering?.rewording}),{actions:true,memories:conversation?.memories});
    pane.draw({html,question:questionMarkup(conversation?.question)});
    drawChanges(found);
    bubble.querySelector('[data-area-stop]').hidden=!busy;
    const ready=conversation?.id && (plain||conversation?.capture);
    // QUEUE ONE: Send stays on while the assistant answers (the message waits for the answer), until one message waits.
    bubble.querySelector('.assistant-area-compose [type=submit]').disabled=!ready || Boolean(attach?.busy()) || busy&&Boolean(composer.queued());
    bubble.querySelector('[data-area-open]').disabled=!ready;
    if(busy&&!startedAt)startedAt=Date.now();
    composer.setState({started:busy&&!queued?startedAt:0,
      text:queued?(conversation?.question?'Your selection is sent once you answer.':'Your selection is sent when this answer finishes.'):busy?'':ready?(plain?'':'Ask about this selection.'):plain?'Opening Assistant…':'Preparing selected content…'});
    // CHIPS: the floating chat shows what goes with its next message; a selection chat's own selection is shown above.
    const now=plain?liveView():null;
    composer.setChips(now?contextChips({page:pageName(),record:now.view.context.record,route:routeContext(now.page)}):[]);
    const title=bubble.querySelector('header > strong'),name=plain?chatTitle(events)||'Assistant':'Ask about this area';
    if(title.textContent!==name)title.textContent=name;
    // Put away, the bar says what the chat is doing.
    const pill=bubble.querySelector('[data-area-pill]'),said=!minimised?'':busy?'Working…':conversation?.question?'Has a question for you':fresh?'Answer ready':'';
    if(pill.textContent!==said)pill.textContent=said;
    pill.hidden=!said;
    position();
  }
  /** The panel of this conversation's cards. A card that was not here before opens it, also after the instructor hid it; it is
   * patched in place, so a list the instructor opened on a card stays open through a poll. */
  function drawChanges(found) {
    if(found.count>cardCount)changesHidden=false;
    cardCount=found.count;
    const panel=bubble.querySelector('[data-area-changes]'),open=changesOpen();
    if(panel.hidden===open)panel.hidden=!open;
    if(found.panel!==changesHTML){patchChildren(panel.querySelector('[data-area-changes-list]'),found.panel);changesHTML=found.panel;}
    const waits=found.waiting?`${found.waiting} wait${found.waiting===1?'s':''} for you`:'';
    const note=panel.querySelector('[data-area-changes-note]');
    if(note.textContent!==waits)note.textContent=waits;
    // Put away, the footer brings it back; the number is how many changes wait (none: no number).
    const show=bubble.querySelector('[data-area-changes-show]'),label=found.waiting?`Show changes (${found.waiting})`:'Show changes';
    if(show.textContent!==label){show.textContent=label;show.title=waits;}
    show.hidden=!cardCount||open;
  }
  /** Brings one card of the panel into view (from its line in the conversation), opening the panel if it was put away. */
  function showCard(key) {
    changesHidden=false;draw();
    const card=[...bubble.querySelectorAll('[data-area-changes-list] > [data-patch-key]')].find(node=>node.dataset.patchKey===key);
    if(!card)return;
    card.setAttribute('tabindex','-1');card.scrollIntoView({block:'nearest'});card.focus({preventScroll:true});
  }
  /** A view bringing a card this window has not taken in: one a turn made, or cards it grouped. */
  const bringsCard=result=>(result.events||[]).some(e=>e.seq>after&&(e.action?.action_id||e.tool==='group_actions'));
  /** DONE AT ONCE: what a turn changed or prepared (a join, a follow-up prepared again, a new card) shows on the page beside the chat
   *  now, not at that page's next refresh. */
  const tellPages=()=>window.dispatchEvent(new CustomEvent('faculty:assistant-pages-changed'));
  /** Reads the cards this conversation shows; pending mail drafts start their JEV send checks (redrawn when they finish). */
  async function refreshActions(token) {
    const found=await api(`${BASE}/actions`);if(token!==generation)return false;
    actions=new Map([...(found.pending||[]),...(found.recent||[])].map(a=>[a.id,a]));batches=found.batches||[];
    checkActionMail(actions.values(),api,()=>{if(token===generation)draw();});
    return true;
  }
  /** The cards again after an Approve or Decline, retried like a read (a GET) until they come; `lost` clears its error once they do. */
  async function rereadCards(token,lost=false,tries=0) {
    try { if(!await refreshActions(token))return; }
    catch { if(token===generation&&tries<READ_RETRY.length)setTimeout(()=>void rereadCards(token,lost,tries+1),READ_RETRY[tries]);return; }
    if(lost)bubble?.querySelector('[data-area-error]')?.setAttribute('hidden','');
    draw();void read(token);
  }
  async function read(token) {
    if (!bubble || !conversation || token!==generation) return;
    clearTimeout(poll);
    try {
      const result=await api(`${BASE}/conversations/${conversation.id}?after=${after}`);
      if (token!==generation) return;
      const acted=bringsCard(result);
      if (acted && !await refreshActions(token)) return;
      readFailures=0;if(reconnecting){reconnecting=false;bubble?.querySelector('[data-area-error]')?.setAttribute('hidden','');}
      absorb(result);
      if(acted)tellPages();
      if (busy||remembering) poll=setTimeout(()=>void read(token),900);
    } catch(error) {
      if (token!==generation) return;
      // Only a conversation that is gone stops the reads; a failed read is retried (it is a GET), and Stop stays while busy.
      if (error?.status===404||error?.status===410) {busy=false;gone=true;queued=null;draw();showError(errorMessage(error));return;}
      readFailures++;reconnecting=true;showError(`${errorMessage(error)} Reconnecting…`);
      poll=setTimeout(()=>void read(token),READ_RETRY[Math.min(readFailures-1,READ_RETRY.length-1)]);
    }
  }
  /** Takes in the answer to something sent (a message, a selection, a question, Stop), then reads on (`reading` false: only
   * while the assistant answers or a note is still being considered). Such an answer can already hold a card its turn made (a
   * quick turn, a slow or lost response, the steps since the last read). It is then left whole to `read`, the one place a card
   * comes in: that brings the same events with the cards read first, so the card and its checks are drawn, and tries again when
   * the cards cannot be read. */
  function receive(result,token,reading=true) {
    const card=bringsCard(result);
    if(!card)absorb(result);
    if(reading||busy||remembering||card)void read(token);
  }
  function showError(message) { if (bubble) { const error=bubble.querySelector('[data-area-error]');error.textContent=message;error.hidden=false; } else toast(message,true); }
  /** The "Selected content" disclosure: the selection mode's own, or added to a floating chat a selection joined. */
  function showSelected(selection,preview) {
    if(!bubble)return;
    bubble.querySelector('.assistant-area-preview')?.remove();
    const details=document.createElement('details');details.className='disclosure assistant-area-preview';
    details.innerHTML='<summary>Selected content</summary><div class="assistant-area-selected"></div>';
    const selected=details.querySelector('.assistant-area-selected');
    if(selection?.text){const text=document.createElement('pre');text.textContent=selection.text;selected.append(text);}
    if(preview){const image=document.createElement('img');image.alt='Selected image content from Faculty';image.src=preview;selected.append(image);}
    if(selection&&(selection.truncated||selection.omitted.length)){const note=document.createElement('p');note.className='supporting-copy';note.textContent=[selection.truncated?'Large selection shortened. Select a smaller area for more detail.':'',...selection.omitted].filter(Boolean).join(' ');selected.append(note);}
    bubble.querySelector('.assistant-area-main').prepend(details);
  }
  /** MOVABLE: the chat's title bar drags it like a window (its buttons keep their clicks); a press that does not move leaves it
   * where it is. Pointer capture keeps the drag on the bar over page content and frames. */
  function draggable(node) {
    const handle=node.querySelector('header');handle.title='Drag to move';
    handle.addEventListener('pointerdown',event=>{
      if(event.button!==0||event.target.closest('button'))return;
      event.preventDefault();
      const box=node.getBoundingClientRect(),dx=event.clientX-box.left,dy=event.clientY-box.top,x=event.clientX,y=event.clientY;
      let dragging=false;
      handle.setPointerCapture(event.pointerId);
      const move=e=>{
        if(node!==bubble||minimised)return;
        if(!dragging&&Math.hypot(e.clientX-x,e.clientY-y)<4)return;
        dragging=true;node.dataset.moving='';moved={x:e.clientX-dx,y:e.clientY-dy};position();
      };
      const end=()=>{
        handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);
        delete node.dataset.moving;
        // Keep the place it shows, not a point dragged past the window's edge; the next floating chat opens there too.
        if(dragging&&node===bubble){moved={x:parseFloat(node.style.left),y:parseFloat(node.style.top)};remember();}
        // Put away, a press on the bar that did not move brings the chat back.
        else if(!dragging&&node===bubble&&minimised)expand();
      };
      handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end);handle.addEventListener('pointercancel',end);
    });
  }
  /** RESIZE: the right and bottom edges and their corner drag the window's size, its top left corner staying where it is; the
   * size is remembered for the next chat. */
  function resizable(node) {
    for(const edge of ['e','s','se']) {
      const handle=document.createElement('div');handle.className='assistant-area-resize';handle.dataset.edge=edge;handle.setAttribute('aria-hidden','true');node.append(handle);
      handle.addEventListener('pointerdown',event=>{
        if(event.button!==0||minimised)return;
        event.preventDefault();
        const box=node.getBoundingClientRect(),x=event.clientX,y=event.clientY;
        handle.setPointerCapture(event.pointerId);node.dataset.resizing='';
        const move=e=>{
          if(node!==bubble)return;
          if(!moved)moved={x:box.left,y:box.top};
          // The width dragged is the conversation's: the panel of changes keeps its own.
          const side=changesWidth();
          size={w:edge==='s'?box.width-side:Math.max(MIN_W,Math.min(innerWidth-box.left-8,box.width+e.clientX-x)-side),
            h:edge==='e'?box.height:Math.max(MIN_H,Math.min(innerHeight-box.top-8,box.height+e.clientY-y))};
          position();
        };
        const end=()=>{
          handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);
          delete node.dataset.resizing;
          if(node===bubble&&size)remember();
        };
        handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end);handle.addEventListener('pointercancel',end);
      });
    }
  }
  /** MINIMISE: puts the chat away as a small bar (its conversation, unsent message and waiting message stay); the bar, its
   * button, a double tap of Command or a selection sent to it bring it back. */
  function minimise() {
    if(!bubble||minimised)return;
    minimised=true;fresh=false;bubble.dataset.minimised='';windowButton();draw();
  }
  function expand({focus=true}={}) {
    if(!bubble||!minimised)return;
    minimised=false;fresh=false;delete bubble.dataset.minimised;windowButton();draw();pane?.toEnd();
    if(focus)bubble.querySelector('#area-question')?.focus({preventScroll:true});
  }
  function windowButton() {
    const button=bubble.querySelector('[data-area-minimise]'),label=minimised?'Bring the chat back':'Put the chat away';
    button.innerHTML=icon(minimised?'window':'minimize');button.setAttribute('aria-label',label);button.title=label;
  }
  /** ONE CHAT: a box drawn in another dialog than the chat's moves the chat into it, so the chat stays usable above that dialog
   * and closes with it. */
  function adopt(host,token) {
    try{bubble.hidePopover();}catch{}
    parent=host;(host||document.body).append(bubble);
    if(host)host.addEventListener('close',()=>{if(token===generation&&parent===host)close();},{once:true});
  }
  /** `how` "click": the window was opened by a click, so it fades and settles in (FRONTEND "Press and open motion"); a Command
   * gesture shows it at once. */
  function showBubble(rect,preview,selection,token,how='') {
    anchor=rect;bubble=document.createElement('section');
    const plain=chatMode==='chat',kept=remembered();
    // The remembered size serves both kinds of chat; the remembered place only the floating chat.
    size=Number.isFinite(kept.w)&&Number.isFinite(kept.h)?{w:kept.w,h:kept.h}:null;
    moved=plain&&Number.isFinite(kept.x)&&Number.isFinite(kept.y)?{x:kept.x,y:kept.y}:null;
    if(how==='click'){bubble.dataset.entered='click';const shown=bubble;setTimeout(()=>{delete shown.dataset.entered;},400);}
    bubble.className='assistant-area-bubble';bubble.dataset.mode=chatMode;bubble.setAttribute('role','dialog');bubble.setAttribute('aria-label',plain?'Assistant chat':'Ask about this area');
    bubble.innerHTML=`<header><strong>${plain?'Assistant':'Ask about this area'}</strong><span class="assistant-area-pill" data-area-pill hidden></span><button class="icon-button" type="button" data-area-minimise aria-label="Put the chat away" title="Put the chat away">${icon('minimize')}</button><button class="icon-button" type="button" data-area-close aria-label="${plain?'Close Assistant chat':'Close selected-area chat'}">${icon('close')}</button></header>
      <div class="assistant-area-body"><div class="assistant-area-main">
      <div class="assistant-area-chat" data-area-chat aria-live="polite"></div><p class="form-error" role="alert" data-area-error hidden></p>
      <form class="assistant-area-compose">${composerMarkup({id:'area-question',label:plain?'Message the assistant':'Message about this area',icon,
        placeholder:plain?'What can I help with?':'Ask about this selection…',hooks:{attached:'data-area-attached',state:'data-area-state',stop:'data-area-stop'}})}</form>
      <footer><button class="text-button" type="button" data-area-again>${plain?'Select an area':'Select another area'}</button><button class="text-button" type="button" data-area-changes-show hidden>Show changes</button><button class="text-button" type="button" data-area-open disabled>Open in Assistant</button></footer>
      </div><aside class="assistant-area-changes" data-area-changes aria-label="Changes in this chat" hidden>
      <div class="assistant-area-changes-head"><strong>Changes</strong><span data-area-changes-note></span><button class="text-button" type="button" data-area-changes-hide>Hide</button></div>
      <div class="assistant-area-changes-list" data-area-changes-list></div></aside></div>`;
    if(!plain)showSelected(selection,preview);
    attach=createAttachments({toast,host:bubble.querySelector('[data-area-attached]'),changed:()=>{if(token===generation)draw();}});
    bubble.querySelector('[data-assistant-attach-input]').addEventListener('change',event=>{const input=event.target;if(input.files?.length)void attach.add(input.files);input.value='';});
    const compose=bubble.querySelector('.assistant-area-compose'),field=bubble.querySelector('#area-question');
    composer=createComposer({form:compose,field,attachments:attach,toast,dropTarget:bubble,state:bubble.querySelector('[data-area-state]'),
      deliver:entry=>void deliver(entry,token),changed:()=>{if(token===generation)draw();}});
    attachPickers({form:compose,field,composer,api});
    pane=createChatView({host:bubble.querySelector('[data-area-chat]'),toast,turns:()=>turnsOf(events),
      onAgain:words=>{
        if(busy||sending){toast('Wait for the current answer first.');return;}
        if(composer.unsent()){toast('Send or clear your unsent message first.');return;}
        const now=carried(words);void deliver({text:words,files:[],kept:[],page:now.page,view:now.view},token);
      },
      onEdit:words=>{composer.fill(words);},onStarter:words=>{composer.fill(words);},
      // ASSISTANT MEMORY: Undo takes back what a message left to remember; the chat's note then says so.
      onUndo:async(id,button)=>{
        setBusy(button,true);
        try{await api(`${BASE}/memory/${id}/undo`,'POST',{conversation_id:conversation?.id});if(token===generation)await read(token);toast('Taken back.');}
        catch(cause){toast(errorMessage(cause),true);setBusy(button,false);}
      },
      // MEMORIES GIVEN: Remove (or Restore) a memory from under the answer whose request was given it.
      onMemory:async(operation,id,revision,button)=>{
        setBusy(button,true);
        try{
          await api(`${BASE}/memory/${id}/${operation}`,'POST',operation==='remove'?{revision}:{conversation_id:conversation?.id});
          if(token===generation){await read(token);draw();}
          toast(operation==='remove'?'Removed. Your next message is not given it.':operation==='restore'?'Restored.':'Kept as you typed it.');
        }catch(cause){toast(errorMessage(cause),true);setBusy(button,false);}
      }});
    topLayer(bubble,parent);draggable(bubble);resizable(bubble);position();
    bubble.addEventListener('submit',event=>{
      event.preventDefault();
      const form=event.target;
      if (form.matches('[data-assistant-question]')) {
        void runFormAction(form,async()=>{ const result=await api(`${BASE}/conversations/${conversation.id}/questions/${form.dataset.assistantQuestion}`,'POST',{answers:Object.fromEntries(new FormData(form))}); if(token===generation)receive(result,token); }).catch(error=>{if(token===generation)showError(errorMessage(error));});return;
      }
      const text=form.elements.text.value.trim(),files=attach?.ids(),kept=attach?.snapshot()||[];
      if ((!text&&!kept.length) || !conversation?.id || (!plain&&!conversation?.capture)) return;
      if (!files) {showError('Wait for the files to finish attaching.');return;}
      const now=carried(text),entry={text,files,kept,page:now.page,view:now.view};
      // QUEUE ONE: a message sent while the assistant answers (or while a selection waits or is on its way) waits its turn.
      if(busy||sending||sendingCapture||queued){composer.enqueue(entry);return;}
      form.elements.text.value='';attach.clear();composer.grow();
      void deliver(entry,token);
    });
    bubble.addEventListener('click',async event=>{
      // A link to the Memory page (a note's "reword it", Change under "Memories given"; ASSISTANT-MEMORY "ONE PAGE"): this
      // window is in the top layer and the page it opens is behind it, so it is put away and the page is in view.
      if(event.target.closest('a[href^="#memory"]')) { if(!minimised)minimise(); return; }
      // A link that opens one of the drawer's tabs: this window is put away when it would cover the drawer it opened (its
      // place and size are its own, not the drawer's entrance, so the drawer is measured where it comes to rest).
      if(event.target.closest('a[data-assistant-tab-open]')) {
        requestAnimationFrame(()=>{
          const drawer=document.getElementById('assistant-panel');if(!bubble||!drawer||drawer.hidden)return;
          const box=bubble.getBoundingClientRect();
          if(box.right>drawer.offsetLeft&&box.left<drawer.offsetLeft+drawer.offsetWidth&&box.bottom>drawer.offsetTop&&box.top<drawer.offsetTop+drawer.offsetHeight)minimise();
        });
        return;
      }
      const target=event.target.closest('button');if(!target)return;
      if(target.hasAttribute('data-area-close')) { if(discard())close();return; }
      if(target.hasAttribute('data-area-minimise')) { if(minimised)expand();else minimise();return; }
      if(target.hasAttribute('data-area-again')) { void begin();return; }
      if(target.hasAttribute('data-area-changes-hide')) { changesHidden=true;draw();return; }
      if(target.hasAttribute('data-area-changes-show')) { changesHidden=false;draw();return; }
      if(target.dataset.cardShow) { showCard(target.dataset.cardShow);return; }
      if(target.hasAttribute('data-area-stop')) { composer.giveBack();try{const result=await api(`${BASE}/conversations/${conversation.id}/stop`,'POST',{});if(token===generation)receive(result,token);}catch(error){if(token===generation)showError(errorMessage(error));}return; }
      if(target.hasAttribute('data-area-open')) {
        if(!conversation || !discard())return;
        const id=conversation.id,host=parent;
        try{if(await openConversation(id,host,releaseFocus)!==false&&token===generation){releaseFocus=null;close({stop:false,restore:false});}}
        catch(error){toast(errorMessage(error),true);}return;
      }
      if(target.hasAttribute('data-question-option')) {chooseQuestionOption(target);return;}
      if(batchClick(target,{api,toast,redraw:draw,refresh:async()=>{if(await refreshActions(token)){draw();void read(token);}}}))return;
      const approve=target.dataset.assistantApprove,decline=target.dataset.assistantDecline;
      if(approve||decline) {
        target.disabled=true;
        // submitActionReview announces the change to the drawer and the shell itself. The cards are read again whatever came of
        // the click, as the drawer does: a lost answer, or a failed read after a done one, left an approved Canvas message showing
        // "Send to 334 students" under "Can't reach your server" (2026-10-28). Faculty refuses a card that no longer waits.
        let lost=false;
        try { const result=await submitActionReview(api,approve||decline,Number(target.closest('[data-action-id]').dataset.seen),Boolean(approve));toast(approve?actionReviewMessage(result):'Declined.'); }
        catch(error){if(token!==generation)return;lost=!error?.status;showError(errorMessage(error)+(lost?' It may already be done: the card shows what Faculty recorded once Faculty answers again.':''));}
        finally{target.disabled=false;}
        void rereadCards(token,lost);
      }
    });
    draw();field.focus({preventScroll:true});
  }
  /**
   * Sends one typed message (assistant-chat.js sendMessage): a definite refusal keeps the draft; a lost or ambiguous response is
   * settled by reading the conversation once and is never resent. What did not go returns to the box.
   */
  async function deliver(entry,token) {
    if(!bubble||!conversation?.id||token!==generation)return;
    bubble.querySelector('[data-area-error]').hidden=true;reconnecting=false;
    sending=true;
    try {
      const result=await sendMessage(api,BASE,conversation.id,{text:entry.text,...(conversation.capture?{capture_id:conversation.capture.id}:{}),...(entry.files.length?{files:entry.files}:{}),page:entry.page,view:entry.view,after},after);
      if(token!==generation)return;
      composer.sent(entry.text);startedAt=Date.now();receive(result.view,token);
    } catch(error) {
      if(token!==generation)return;
      if(error?.view)receive(error.view,token,false);
      composer.restore(entry);
      showError(errorMessage(error)+(error?.uncertain?' It may already have been sent. Check the chat before sending again.':''));
    } finally { if(token===generation){sending=false;draw();} }
  }
  /** Sends a selection to `id` for its read-only first analysis; a lost response is settled by one read, never resent. */
  async function sendCapture(id,rect,selection,blob,chosen,token,own) {
    const metadata={request_id:crypto.randomUUID(),rect,captured_at:chosen.captured_at,page:chosen.page,view:chosen.view,selection};
    let data=metadata;
    if(blob){data=new FormData();data.append('image',blob,'selected-area.'+(blob.type==='image/png'?'png':'jpg'));data.append('metadata',JSON.stringify(metadata));}
    const abandon=()=>{if(own)void api(`${BASE}/conversations/${id}/close`,'POST',{}).catch(()=>{});};
    // A typed message waits while the selection is on its way (QUEUE ONE); if the selection fails it returns to the box.
    sendingCapture=true;
    try { const result=await api(`${BASE}/conversations/${id}/captures`,'POST',data); if(token!==generation){abandon();return;}receive(result,token);sendingCapture=false; }
    catch(error) {
      if(token!==generation){abandon();return;}
      sendingCapture=false;
      // A lost response may have started analysis. Read its receipt once; never resend it automatically.
      try{const result=await api(`${BASE}/conversations/${id}`);if(token!==generation)return;if(result.capture?.id===metadata.request_id){receive(result,token);return;}
        // ONE CHAT: not taken because the open chat had just begun answering or asking: it waits for that, as any box does.
        if(!own&&!queued&&(result.busy||result.question)){queued={rect,selection,blob,chosen,token};receive(result,token);return;}}catch{}
      composer?.giveBack();
      showError(errorMessage(error)+' Select the area again to start a new analysis.');
    }
  }
  async function selected(rect,pick) {
    let content;
    try {content=await collectSelection(rect,prepared?.root||pickParent||document.body);}
    catch(error) {if(pick===picking){closeSelection();toast(errorMessage(error),true);}return;}
    if(pick!==picking)return;
    const {selection,blob,preview}=content;
    // ASSISTANT "Selected-area conversations", ONE CHAT (requirement, 2026-10-09): a box sent while a chat is open goes to that chat,
    // whichever gesture opened it, its unsent message kept; while it answers or asks, the box waits for it (`flush`). Only a chat
    // that never opened or has ended is replaced, ended now that a box is sent (asking first, after Command is released, if it
    // holds a draft).
    const join=stashed&&Boolean(conversation?.id)&&!gone;
    if(stashed&&!join&&!discard()){closeSelection();return;}
    const hold=pickRelease,host=pickParent,chosen=pickView,byPointer=pickPointer;
    pickRelease=null;pickParent=null;pickView=null;stashed=false;
    restoreContent();drop(selector);selector=null;
    if(join) {
      if(hold){releaseFocus?.({focus:false});releaseFocus=hold;}
      const token=generation;
      if(host!==parent)adopt(host,token);
      // A selection chat takes the new box's page and place (unless the instructor moved it); the floating chat keeps its own.
      if(chatMode!=='chat'){view=chosen;anchor=rect;}
      reveal();expand({focus:false});showSelected(selection,preview);bubble.querySelector('[data-area-error]').hidden=true;
      if(idle())await sendCapture(conversation.id,rect,selection,blob,chosen,token,false);
      else{queued={rect,selection,blob,chosen,token};draw();}
      return;
    }
    const kept=releaseFocus;close({restore:false});
    if(hold){kept?.({focus:false});releaseFocus=hold;}else releaseFocus=kept;
    parent=host;chatMode='selection';view=chosen;
    const token=generation;showBubble(rect,preview,selection,token,byPointer?'click':'');
    if(host)host.addEventListener('close',()=>{if(token===generation&&parent===host)close();},{once:true});
    let created;
    try {created=await api(`${BASE}/conversations`,'POST',{page:view.page});}
    catch(error) {if(token===generation){close();toast(errorMessage(error),true);}return;}
    if(token!==generation){void api(`${BASE}/conversations/${created.id}/close`,'POST',{}).catch(()=>{});return;}
    conversation=created;
    await sendCapture(created.id,rect,selection,blob,chosen,token,true);
  }
  /** A Command+click without a drag during a hold is an ordinary Command+click: the selector steps aside and passes it on. */
  function passThrough(event) {
    // The target is what the instructor saw under the selector: found before a hidden bubble comes back.
    const {clientX:x,clientY:y}=event;drop(selector);selector=null;restoreContent();
    const target=document.elementFromPoint(x,y);closeSelection({toChat:false});if(!target)return;
    const link=target.closest('a[href]');
    if(link){window.open(link.href,'_blank','noopener');return;}
    target.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,composed:true,view:window,clientX:x,clientY:y,button:0,metaKey:true,shiftKey:event.shiftKey,altKey:event.altKey,ctrlKey:event.ctrlKey}));
  }
  function select(pick) {
    const held=holdSelecting;
    selector=document.createElement('div');selector.className='assistant-area-selector';selector.setAttribute('aria-label',held?'Draw an area; release Command to send; Escape cancels':'Drag to select an area; Escape cancels');
    const hint=document.createElement('p');
    hint.textContent=held?(prepared?'Scroll to a page, draw a box · Release ⌘ to send · Esc cancels':'Draw a box · Release ⌘ to send · Esc cancels'):prepared?'Scroll to a page, then drag a box · Esc cancels':'Drag a box over text, records, or images · Esc cancels';
    selector.append(hint);
    const box=document.createElement('div');box.className='assistant-area-box';box.hidden=true;selector.append(box);topLayer(selector,pickParent);
    let start=null,submitted=false,rect=null;
    const submit=()=>{
      if(submitted)return;
      if(!rect||rect.width<8||rect.height<8){if(held)closeSelection();else hint.textContent='Drag a larger box · Esc cancels';return;}
      submitted=true;holdSelecting=false;finishSelection=null;hint.textContent='Reading selected content…';
      void selected(rect,pick).catch(error=>toast(errorMessage(error),true));
    };
    finishSelection=submit;
    selector.onpointerdown=event=>{event.preventDefault();if(event.button!==0||submitted)return;rect=null;start={x:event.clientX,y:event.clientY};selector.setPointerCapture(event.pointerId);};
    const drawBox=event=>{if(!start)return null;rect=selectionRect(start,{x:event.clientX,y:event.clientY},innerWidth,innerHeight);box.hidden=false;
      Object.assign(box.style,{left:rect.x+'px',top:rect.y+'px',width:rect.width+'px',height:rect.height+'px'});return rect;};
    selector.onpointermove=drawBox;
    selector.onpointerup=event=>{
      event.preventDefault();if(!start||submitted)return;
      if(held&&Math.hypot(event.clientX-start.x,event.clientY-start.y)<8){start=null;passThrough(event);return;}
      drawBox(event);start=null;if(!held)submit();
    };
    selector.onpointercancel=()=>{start=null;rect=null;box.hidden=true;};
    selector.addEventListener('wheel',event=>{event.preventDefault();if(prepared&&!start&&!submitted)prepared.scroll(event.deltaY,event.deltaX);},{passive:false});
  }
  async function begin(mode='pointer') {
    if(selector || starting)return;
    if(!HTMLElement.prototype.showPopover) {toast('Area selection needs a recent Chrome or Safari.',true);return;}
    const focus=document.activeElement;
    const pick=++picking;starting=true;holdSelecting=mode==='hold';pickPointer=mode==='pointer';pickParent=activeDialog();
    // An open bubble is hidden, not closed: it returns unchanged if this selection is cancelled.
    if(bubble){stashed=true;try{bubble.hidePopover();}catch{}}
    if(focus&&focus!==document.body&&!bubble?.contains(focus))pickRelease=holdCaptureFocus(/** @type {HTMLElement} */ (focus));
    pickView={page:location.hash || '#overview',view:context(),captured_at:new Date().toISOString()};
    try {
      selector=document.createElement('div');selector.className='assistant-area-selector';
      const progress=document.createElement('p');progress.textContent='Preparing content for selection…';selector.append(progress);topLayer(selector,pickParent);
      if(pickParent)pickParent.addEventListener('close',()=>{if(pick===picking)closeSelection();},{once:true});
      const content=await prepareSelection();
      if(pick!==picking){content?.restore();return;}
      prepared=content;
      drop(selector);selector=null;starting=false;select(pick);
    }catch(error){if(pick===picking){closeSelection();toast(errorMessage(error),true);}}
  }
  async function openChat() {
    if(bubble){if(!stashed){if(minimised)expand();else bubble.querySelector('#area-question').focus({preventScroll:true});}return;}
    if(selector||starting)return;
    if(!HTMLElement.prototype.showPopover){toast('The floating Assistant needs a recent Chrome or Safari.',true);return;}
    const focus=document.activeElement;
    close();parent=activeDialog();chatMode='chat';
    if(focus&&focus!==document.body)releaseFocus=holdCaptureFocus(/** @type {HTMLElement} */ (focus));
    const token=generation,owner=parent;view=liveView();
    if(owner)owner.addEventListener('close',()=>{if(parent===owner&&token===generation)close();},{once:true});
    showBubble({x:0,y:0,width:0,height:0},'',null,token);
    try {
      const created=await api(`${BASE}/conversations`,'POST',{page:view.page});
      if(token!==generation){void api(`${BASE}/conversations/${created.id}/close`,'POST',{}).catch(()=>{});return;}
      absorb(created);
    }catch(error){if(token===generation)showError(errorMessage(error));}
  }
  document.addEventListener('pointerdown',event=>{if(event.target.closest?.('[data-assistant-capture]'))event.preventDefault();},true);
  document.addEventListener('click',event=>{if(event.target.closest?.('[data-assistant-capture]')){event.preventDefault();void begin();}});
  // Escape belongs to the assistant only for a selection, or when it is pressed inside the bubble; page controls keep theirs.
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    if(selector||starting){event.preventDefault();event.stopImmediatePropagation();closeSelection();return;}
    // An open "/" or "@" picker takes Escape first (it closes the picker, not the chat).
    if(bubble&&!stashed&&event.target instanceof Node&&bubble.contains(event.target)){if(composer?.pickerOpen())return;event.preventDefault();event.stopImmediatePropagation();if(discard())close();}
  },true);
  // Navigation releases a selection and ends a selection chat; the floating chat stays with its conversation (like the
  // drawer), takes the new page for its next message and lets go of the previous page's editor.
  window.addEventListener('hashchange',()=>{
    if(selector||starting)closeSelection({focus:false});
    if(bubble&&chatMode==='chat'){releaseFocus?.({focus:false});releaseFocus=null;view=liveView();draw();return;}
    close();
  });
  document.addEventListener('visibilitychange',()=>{
    if(!document.hidden)return;
    if(selector||starting)closeSelection();
    if(bubble&&chatMode!=='chat'&&unfinished())close();
  });
  window.addEventListener('pagehide',()=>close());
  window.addEventListener('resize',()=>{if(selector){closeSelection();toast('The window changed size. Select the area again.');}else position();});
  registerShortcut({key:'Hold Meta',label:'Draw an area; release Command to send',allowInInput:true,allowInGrid:true,allowInDialog:true,scope:'global',
    // Command chords typed in the chat never become a selection.
    when:()=>!(bubble&&!stashed&&bubble.contains(document.activeElement)),
    run:()=>void begin('hold'),release:()=>{if(holdSelecting){if(starting)closeSelection();else finishSelection?.();}},cancel:()=>{if(holdSelecting)closeSelection();}});
  // A memory changed in Saved: the chat is read once, so a note about it shows the words that stand.
  window.addEventListener('faculty:assistant-memory-changed',()=>{if(bubble&&conversation&&!busy)void read(generation);});
  registerShortcut({key:'Meta Meta',label:'Open floating Assistant chat',allowInInput:true,allowInGrid:true,allowInDialog:true,scope:'global',run:()=>void openChat()});
  /**
   * BOTH WAYS: the drawer's conversation becomes this window's (it was moved, not copied: the drawer lets go of it), with the
   * instructor's unsent words and files. Without an `id` it is a new floating chat holding that draft. false when a chat
   * window is already open (ONE CHAT) or the conversation cannot be read.
   */
  async function adoptChat(id,{text='',kept=[]}={}) {
    if(bubble||selector||starting){toast('A chat window is already open. Close it first.');return false;}
    if(!HTMLElement.prototype.showPopover){toast('The floating Assistant needs a recent Chrome or Safari.',true);return false;}
    close();parent=activeDialog();chatMode='chat';
    const token=generation,owner=parent;view=liveView();
    if(owner)owner.addEventListener('close',()=>{if(parent===owner&&token===generation)close();},{once:true});
    showBubble({x:0,y:0,width:0,height:0},'',null,token,'click');
    try {
      const found=id?await api(`${BASE}/conversations/${encodeURIComponent(id)}`):await api(`${BASE}/conversations`,'POST',{page:view.page});
      if(token!==generation){if(!id)void api(`${BASE}/conversations/${found.id}/close`,'POST',{}).catch(()=>{});return false;}
      if(bringsCard(found)&&!await refreshActions(token))return false;
      absorb(found);
      if(text){bubble.querySelector('#area-question').value=text;composer.grow();}
      if(kept.length)attach.restore(kept);
      if(busy)void read(token);
      pane.toEnd();
      return true;
    }catch(error){
      // The drawer keeps the conversation: this window only goes away.
      if(token===generation)close({stop:false});
      toast(errorMessage(error),true);return false;
    }
  }
  return {begin,openChat,close,adopt:adoptChat};
}
