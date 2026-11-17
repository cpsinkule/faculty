"""ASSISTANT: actual DOM and image selection, without screen sharing or real model calls."""
import io
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from flask import Response
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(REPO),str(REPO/'tests')]
from test_scheduling_service import ScheduleBase

INIT="""(() => {
  Object.defineProperty(navigator,'pdfViewerEnabled',{get:()=>true});
  window.captureCalls=0;window.cspViolations=[];
  document.addEventListener('securitypolicyviolation',e=>cspViolations.push({directive:e.violatedDirective,source:e.sourceFile,line:e.lineNumber,blocked:e.blockedURI}));
  if(navigator.mediaDevices)Object.defineProperty(navigator.mediaDevices,'getDisplayMedia',{value:()=>{captureCalls++;throw Error('ASSISTANT: screen capture must not be used');}});
})();"""


# Toast text lets clicks through; its buttons take them. Reachable: the stack is in the top layer and its buttons are hit first.
TOAST_ON_TOP="""() => {const host=document.getElementById('toast'),item=[...host.querySelectorAll('.toast-item')].at(-1);
  if(!item||!host.matches(':popover-open'))return false;const button=item.querySelector('button');if(!button)return true;
  const r=button.getBoundingClientRect();return button.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}"""


def floating_chat_beside_page_work(page,fixture,calls,writes,bodies,dialogs,decisions,answer,drag,floating):
    """ASSISTANT/GRADEBOOK (2026-10-09 review): the non-modal floating chat leaves the page's own work alone."""
    conversations=fixture.app.extensions['assistant'].conversations
    selector=page.locator('.assistant-area-selector');field=floating.get_by_label('Message the assistant')
    scores=lambda:len([url for url in writes if '/api/gradebook/' in url])
    closes=lambda:len([url for url in writes if url.endswith('/close')])
    def settle(count):
        for _ in range(100):
            if count():return
            page.wait_for_timeout(100)
        raise AssertionError('timed out')
    # A wide window keeps the centred chat clear of the grid, so cells are clicked as the instructor would.
    page.set_viewport_size({'width':2400,'height':1000})
    cells=page.locator('[data-cell][data-row]');a,b=cells.nth(0),cells.nth(1)
    assert a.get_attribute('data-row')!=b.get_attribute('data-row')
    # An edit open when the chat appears is kept while focus is in the chat, and saves once the instructor clicks another cell.
    saved=scores();a.click();page.keyboard.type('6');expect(a.locator('input.gb-input')).to_have_value('6')
    page.keyboard.press('Meta',delay=120);page.keyboard.press('Meta',delay=120)
    expect(floating).to_be_visible();expect(field).to_be_focused();expect(a.locator('input.gb-input')).to_have_value('6')
    b.click();page.keyboard.type('5');expect(b.locator('input.gb-input')).to_have_value('5')
    settle(lambda:scores()==saved+1);expect(a).to_contain_text('6')
    # Escape in a page control belongs to it: the cell edit is cancelled, the chat and its conversation stay.
    ended=closes();page.keyboard.press('Escape');expect(b.locator('input.gb-input')).to_have_count(0)
    expect(floating).to_be_visible();assert scores()==saved+1 and closes()==ended
    a.click();field.fill('Help me plan a class.');field.press('Enter')
    expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000);expect(floating).to_contain_text('The selected content is ready')
    ident=calls[-1]['id'];assert bodies[-1]['view']['context']['record']['row']==a.get_attribute('data-row')
    # A slow Command chord typed in the chat (Command held past 600 ms) never becomes a selection.
    field.click();page.keyboard.down('Meta');page.wait_for_timeout(800);page.keyboard.press('v');page.keyboard.up('Meta')
    expect(selector).to_have_count(0);expect(floating).to_be_visible();assert ident in conversations._items
    # A hold over the open chat hides it; a cancelled selection gives it back unchanged, draft included, with no question.
    field.fill('Unsent while selecting');page.evaluate('document.activeElement.blur()');asked=len(dialogs)
    page.keyboard.down('Meta');expect(selector).to_be_visible();expect(floating).to_be_hidden()
    page.keyboard.press('c');page.keyboard.up('Meta')
    expect(selector).to_have_count(0);expect(floating).to_be_visible();expect(field).to_have_value('Unsent while selecting')
    assert len(dialogs)==asked and ident in conversations._items and closes()==ended
    # Command+click after the hold has armed is an ordinary Command+click: buttons get it, links open a new tab.
    page.evaluate("""() => {window.metaClicks=[];
      const button=document.createElement('button');button.id='meta-target';button.textContent='Meta target';
      Object.assign(button.style,{position:'fixed',left:'300px',top:'900px',zIndex:'300'});button.addEventListener('click',e=>metaClicks.push(e.metaKey));
      const link=document.createElement('a');link.id='meta-link';link.href='/#absences';link.textContent='Meta link';
      Object.assign(link.style,{position:'fixed',left:'500px',top:'900px',zIndex:'300'});document.body.append(button,link);}""")
    count=len(calls);captures=len([url for url in writes if url.endswith('/captures')])
    page.evaluate('document.activeElement.blur()');page.keyboard.down('Meta');expect(selector).to_be_visible()
    target=page.locator('#meta-target').bounding_box();page.mouse.click(target['x']+target['width']/2,target['y']+target['height']/2)
    expect(selector).to_have_count(0);assert page.evaluate('metaClicks')==[True];page.keyboard.up('Meta')
    page.keyboard.down('Meta');expect(selector).to_be_visible();target=page.locator('#meta-link').bounding_box()
    with page.context.expect_page() as opened:page.mouse.click(target['x']+target['width']/2,target['y']+target['height']/2)
    opened.value.wait_for_load_state();assert opened.value.url.endswith('#absences');opened.value.close();page.keyboard.up('Meta')
    expect(selector).to_have_count(0);expect(floating).to_be_visible();expect(field).to_have_value('Unsent while selecting')
    assert len(calls)==count and len([url for url in writes if url.endswith('/captures')])==captures
    page.evaluate("document.querySelector('#meta-target').remove();document.querySelector('#meta-link').remove()")
    # A selection made from the idle chat joins it: same conversation, the same read-only first analysis, the draft kept.
    floating.get_by_role('button',name='Select an area',exact=True).click();expect(selector).to_be_visible();expect(floating).to_be_hidden()
    drag(page.locator('#lifecycle-selection').bounding_box())
    expect(floating).to_be_visible();expect(floating).to_contain_text('Analyze this selected area')
    expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert calls[-1]['id']==ident and calls[-1]['readonly'] and 'Lifecycle selection' in calls[-1]['text']
    expect(floating.locator('.assistant-area-preview')).to_have_count(1);expect(field).to_have_value('Unsent while selecting')
    assert conversations.get(ident).capture and len(calls)==count+1
    # MOVABLE (requirement, 2026-10-09): the title bar drags the chat like a window. It keeps that place through an answer and a
    # selection, stays whole inside the window, and its buttons still click.
    grip=floating.locator('header > strong').bounding_box();start=floating.bounding_box()
    page.mouse.move(grip['x']+10,grip['y']+5);page.mouse.down();page.mouse.move(grip['x']+310,grip['y']+125,steps=8);page.mouse.up()
    placed=floating.bounding_box();assert abs(placed['x']-start['x']-300)<2 and abs(placed['y']-start['y']-120)<2,(start,placed)
    field.fill('Answer while moved');field.press('Enter');expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert floating.bounding_box()['x']==placed['x'] and floating.bounding_box()['y']==placed['y']
    floating.get_by_role('button',name='Select an area',exact=True).click();expect(floating).to_be_hidden()
    drag(page.locator('#lifecycle-selection').bounding_box());expect(floating).to_be_visible()
    expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert floating.bounding_box()['x']==placed['x'] and floating.bounding_box()['y']==placed['y']
    grip=floating.locator('header > strong').bounding_box()
    page.mouse.move(grip['x']+10,grip['y']+5);page.mouse.down();page.mouse.move(grip['x']+3000,grip['y']+3000,steps=4);page.mouse.up()
    edge=floating.bounding_box();assert edge['x']+edge['width']<=page.viewport_size['width'] and edge['y']+edge['height']<=page.viewport_size['height'],edge
    # RESIZE (2026-10-09): the lower right corner drags the size, the top left staying put; size and place are remembered.
    grip=floating.locator('header > strong').bounding_box()
    page.mouse.move(grip['x']+10,grip['y']+5);page.mouse.down();page.mouse.move(720,180,steps=6);page.mouse.up()
    before=floating.bounding_box()
    page.mouse.move(before['x']+before['width']-3,before['y']+before['height']-3);page.mouse.down()
    page.mouse.move(before['x']+before['width']+117,before['y']+before['height']+37,steps=6);page.mouse.up()
    sized=floating.bounding_box()
    assert abs(sized['width']-before['width']-120)<3 and abs(sized['height']-before['height']-40)<3 and abs(sized['x']-before['x'])<1 and abs(sized['y']-before['y'])<1,(before,sized)
    kept=page.evaluate("JSON.parse(localStorage.getItem('faculty:assistant:window'))")
    assert abs(kept['w']-sized['width'])<=1 and abs(kept['h']-sized['height'])<=1 and abs(kept['x']-sized['x'])<=1 and abs(kept['y']-sized['y'])<=1,(kept,sized)
    # It is never smaller than 360 by 320.
    page.mouse.move(sized['x']+sized['width']-3,sized['y']+sized['height']-3);page.mouse.down();page.mouse.move(sized['x']+20,sized['y']+20,steps=6);page.mouse.up()
    small=floating.bounding_box();assert abs(small['width']-360)<2 and abs(small['height']-320)<2,small
    page.mouse.move(small['x']+small['width']-3,small['y']+small['height']-3);page.mouse.down()
    page.mouse.move(sized['x']+sized['width']-3,sized['y']+sized['height']-3,steps=6);page.mouse.up()
    kept=page.evaluate("JSON.parse(localStorage.getItem('faculty:assistant:window'))")
    # MINIMISE: put away, the chat is a bar at the lower right. Nothing ends and the unsent message stays; a double tap of
    # Command brings it back as it was.
    open_before=closes();field.fill('Kept while put away')
    floating.get_by_role('button',name='Put the chat away',exact=True).click()
    bar=floating.bounding_box();assert bar['height']<90 and bar['x']+bar['width']>page.viewport_size['width']-40 and bar['y']+bar['height']>page.viewport_size['height']-40,bar
    expect(field).to_be_hidden();expect(floating.get_by_role('button',name='Bring the chat back',exact=True)).to_be_visible()
    assert closes()==open_before and ident in conversations._items
    page.keyboard.press('Meta',delay=120);page.keyboard.press('Meta',delay=120)
    expect(field).to_be_visible();expect(field).to_be_focused();expect(field).to_have_value('Kept while put away')
    back=floating.bounding_box();assert abs(back['width']-kept['w'])<2 and abs(back['x']-kept['x'])<2 and abs(back['y']-kept['y'])<2,(back,kept)
    field.fill('')
    # ONE CHAT (2026-10-09): a box sent while the chat is answering waits for that answer, then goes to this same chat.
    answer.clear();field.fill('Take your time');field.press('Enter');expect(floating.locator('[data-area-stop]')).to_be_visible()
    count=len(calls);floating.get_by_role('button',name='Select an area',exact=True).click();expect(floating).to_be_hidden()
    drag(page.locator('#lifecycle-selection').bounding_box());expect(floating).to_be_visible()
    expect(floating.locator('[data-area-state]')).to_have_text('Your selection is sent when this answer finishes.')
    assert len(calls)==count and ident in conversations._items
    answer.set();settle(lambda:len(calls)==count+1);expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert calls[-1]['id']==ident and calls[-1]['readonly'] and 'Lifecycle selection' in calls[-1]['text']
    # Each message carries the page and record on screen when it is sent; the chat's own links keep the conversation.
    b.click();field.fill('Show the absences link');field.press('Enter')
    link=floating.get_by_role('link',name='Absences',exact=True);expect(link).to_be_visible(timeout=15000)
    assert bodies[-1]['view']['context']['record']['row']==b.get_attribute('data-row') and bodies[-1]['view']['context']['unsaved'] is False
    link.click();page.wait_for_function("location.hash==='#absences'")
    expect(floating).to_be_visible();assert ident in conversations._items and closes()==ended
    # Mail drafted in the chat runs its JEV send checks in the instructor's browser, like the drawer. Its card is in the panel
    # beside the conversation (CHANGES BESIDE THE CHAT), whichever answer brings the step that made it.
    checks=[]
    def draft(ident,subject):
        return {'id':ident,'pending':True,'status':'pending','seen':1,'endpoint':'gmail_mail.send','method':'POST','path':'/api/mail/send',
                'summary':f'Email a synthetic student: {subject}','body':{'to':['student@example.test'],'subject':subject,'text':'Hello'},
                'preview':{'data':{'message':{'to':['student@example.test'],'subject':subject,'text':'Hello','sender':'me@example.test'}}}}
    mail=draft('synthetic-mail','Synthetic');again=draft('synthetic-mail-again','Synthetic again');waiting=[mail]
    checked=lambda card:floating.locator(f'[data-area-changes] [data-send-checks="{card["id"]}"]')
    page.route('**/api/assistant/actions',lambda route:route.fulfill(json={'pending':waiting,'recent':[]}))
    page.route('**/api/mail/check',lambda route:(checks.append(route.request.post_data_json),route.fulfill(json={'status':'checked','notes':[]})))
    # A later read brings it: the turn is held until the message's own response is in.
    answer.clear();field.fill('Draft the synthetic mail');field.press('Enter');expect(floating.locator('[data-area-stop]')).to_be_visible()
    assert not any(e.get('action') for e in conversations.get(ident).events);answer.set()
    expect(checked(mail)).to_be_visible(timeout=15000);expect(checked(mail)).to_have_text('Checks found nothing to point out.')
    expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert bodies[-1]['page']=='#absences' and checks==[mail['body']]
    # The message's own response brings it (2026-10-10: a turn quicker than that response left the window with the step's line
    # and no card to send or decline): the response is the server's own, read once the turn has made its card.
    held=[]
    def late(route):
        route.fetch()
        for _ in range(200):
            found=conversations.get(ident)
            if not found.busy and any((e.get('action') or {}).get('action_id')==again['id'] for e in found.events):break
            time.sleep(.05)
        response=page.request.get(route.request.url.removesuffix('/messages')+f"?after={route.request.post_data_json['after']}")
        held.append(response.json());route.fulfill(status=202,response=response)
    waiting.append(again);page.route('**/conversations/*/messages',late)
    field.fill('Draft the synthetic mail again');field.press('Enter')
    expect(checked(again)).to_be_visible(timeout=15000);expect(checked(again)).to_have_text('Checks found nothing to point out.')
    assert len(held)==1 and not held[0]['busy'] and [e['action']['action_id'] for e in held[0]['events'] if e.get('action')]==[again['id']],held
    expect(floating.locator('[data-area-chat] [data-card-show]')).to_have_text([mail['summary'],again['summary']])
    expect(floating.locator('[data-area-changes-note]')).to_have_text('2 wait for you')
    assert checks==[mail['body'],again['body']]
    page.unroute('**/conversations/*/messages',late);page.unroute('**/api/assistant/actions');page.unroute('**/api/mail/check')
    # A lost /messages response is settled by one read: one turn, the draft cleared, nothing sent twice.
    def lost(route):route.fetch();route.abort('failed')
    page.route('**/conversations/*/messages',lost);count=len(calls);sent=len(bodies)
    field.fill('Count this once');field.press('Enter');expect(field).to_have_value('',timeout=15000)
    expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000);page.unroute('**/conversations/*/messages',lost)
    assert len(calls)==count+1 and len(bodies)==sent+1
    assert [e.get('text') for e in conversations.get(ident).events if e['kind']=='user'].count('Count this once')==1
    # A failed read is retried while the turn runs: Stop stays visible and the answer still arrives.
    failures=[]
    def flaky(route):
        if route.request.method=='GET' and len(failures)<2:failures.append(1);route.fulfill(status=503,json={'error':'Synthetic read unavailable'})
        else:route.continue_()
    reads=lambda url:f'/conversations/{ident}?after=' in url
    answer.clear();page.route(reads,flaky);field.fill('Wait for me');field.press('Enter')
    expect(floating.locator('[data-area-error]')).to_contain_text('Reconnecting',timeout=10000)
    expect(floating.locator('[data-area-stop]')).to_be_visible();answer.set()
    expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000);expect(floating.locator('[data-area-error]')).to_be_hidden()
    page.unroute(reads,flaky);assert len(failures)==2
    # ONE CHAT: a box drawn inside a modal dialog brings the open chat into that dialog, where it stays usable above it.
    page.evaluate("""() => {const d=document.createElement('dialog');d.id='join-dialog';d.innerHTML='<p id="dialog-words">Dialog words</p>';
      document.body.append(d);d.showModal();}""")
    count=len(calls);page.keyboard.down('Meta');expect(selector).to_be_visible(timeout=15000);expect(selector).not_to_contain_text('Preparing',timeout=15000)
    box=page.locator('#dialog-words').bounding_box();page.mouse.move(box['x']-4,box['y']-4);page.mouse.down()
    page.mouse.move(box['x']+box['width']+4,box['y']+box['height']+4);page.mouse.up();page.keyboard.up('Meta')
    expect(floating).to_be_visible();settle(lambda:len(calls)==count+1);expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert calls[-1]['id']==ident and 'Dialog words' in calls[-1]['text']
    assert page.evaluate("document.querySelector('#join-dialog').contains(document.querySelector('.assistant-area-bubble'))")
    field.click();field.fill('Typed above the dialog');expect(field).to_have_value('Typed above the dialog')
    # Escape inside the chat closes it and ends its conversation (the dialog keeps its own Escape).
    field.fill('');field.press('Escape');expect(floating).to_have_count(0)
    assert page.evaluate("document.querySelector('#join-dialog').open");page.evaluate("document.querySelector('#join-dialog').remove()")
    page.wait_for_function("async id=>(await fetch('/api/assistant/conversations/'+id)).status===410",arg=ident)
    # Toasts sit left of the open drawer, never under it.
    page.locator('[data-assistant-open]').first.click();expect(page.locator('#assistant-panel')).to_be_visible()
    page.keyboard.down('Meta');expect(selector).to_be_visible()
    page.set_viewport_size({'width':1400,'height':1000});page.keyboard.up('Meta');expect(page.locator('.toast')).to_contain_text('The window changed size')
    assert page.evaluate(TOAST_ON_TOP),'the toast is under the drawer'
    assert page.evaluate("document.querySelector('#toast .toast-item').getBoundingClientRect().right<=innerWidth-480")
    page.set_viewport_size({'width':1440,'height':1000});page.locator('#assistant-panel').get_by_role('button',name='Close the assistant',exact=True).click()
    # REMEMBERED: the next floating chat opens at the size and place the last one was left at.
    page.keyboard.press('Meta',delay=120);page.keyboard.press('Meta',delay=120)
    expect(floating).to_be_visible();again=floating.bounding_box()
    assert abs(again['width']-kept['w'])<2 and abs(again['height']-kept['h'])<2 and abs(again['x']-kept['x'])<2 and abs(again['y']-kept['y'])<2,(again,kept)
    # BOTH WAYS: the chat moves to the drawer and back to a window: the same conversation, nothing ended, the unsent words with it.
    field.fill('Round trip');field.press('Enter');expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    moved=calls[-1]['id'];floating.get_by_role('button',name='Open in Assistant').click()
    panel=page.locator('#assistant-panel');expect(panel).to_be_visible();expect(floating).to_have_count(0)
    expect(panel.locator('[data-assistant-chat]')).to_contain_text('Round trip')
    page.wait_for_timeout(300);open_now=closes()
    panel.locator('#assistant-text').fill('Carried back')
    panel.get_by_role('button',name='Open this chat in a window',exact=True).click()
    expect(floating).to_be_visible();expect(panel).to_be_hidden()
    expect(field).to_have_value('Carried back');expect(floating).to_contain_text('Round trip')
    assert moved in conversations._items and closes()==open_now
    # QUEUE ONE in the window: a message typed while it answers waits, then goes to the same conversation once.
    answer.clear();field.fill('First of two');field.press('Enter');expect(floating.locator('[data-area-stop]')).to_be_visible()
    count=len(calls);field.fill('Second of two');field.press('Enter')
    expect(floating.locator('.assistant-queued')).to_contain_text('Second of two');assert len(calls)==count
    answer.set();settle(lambda:len(calls)==count+1);expect(floating.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
    assert calls[-1]['id']==moved and 'Second of two' in calls[-1]['text']
    assert [e.get('text') for e in conversations.get(moved).events if e['kind']=='user'].count('Second of two')==1
    field.fill('');field.press('Escape');expect(floating).to_have_count(0)
    assert not [d for d in dialogs if d!='Discard the message you have not sent?'],dialogs


def main():
    fixture=ScheduleBase();fixture.setUp();calls=[];answer=threading.Event();answer.set()
    class Engine:
        name='synthetic'
        def close(self): pass
        def stop(self): answer.set()
        def turn(self,c,text,images=()):
            calls.append({'id':c.id,'text':text,'images':images,'readonly':bool(c.session.get('_capture_analysis_only'))})
            answer.wait(15)
            if c.stopping:return
            if 'Show the absences link' in text:c.emit('assistant',text='Open [Absences](#absences).')
            elif 'Draft the synthetic mail' in text:
                c.emit('tool',tool='draft_student_reply',label='Drafted a reply',ok=True,action={'action_id':'synthetic-mail-again' if 'again' in text else 'synthetic-mail'})
                c.emit('assistant',text='The mail draft is waiting for you.')
            else:c.emit('assistant',text='The selected content is ready. I can help explain it.')
    try:
        image=Image.new('RGB',(400,200),'red');ImageDraw.Draw(image).rectangle((200,0,399,199),fill='blue')
        content=io.BytesIO();image.save(content,'PNG')
        # This isolated fixture's setup already reads its own API; add a test-only route before serving the browser.
        fixture.app._got_first_request=False
        fixture.app.add_url_rule('/api/test-selection/image','selection_test.image',lambda:Response(content.getvalue(),mimetype='image/png'))
        fixture.setup_gradebook([{'title':'Synthetic quiz','points':'10'}])
        definition=fixture.client.get('/api/state?include=core').json['course_definitions'][0]['id']
        docs=fixture.app.extensions['documents'];assignment=docs.create_assignment(definition,'selection-paper','Selection paper')
        version=docs.register_version(assignment['id'],'A',(REPO/'document_identity/fixtures/letter.pdf').read_bytes(),{'main.tex':b'% synthetic selection source'}, {})
        server=create_server(fixture.app,host='127.0.0.1',port=0,threads=8)
        threading.Thread(target=server.run,daemon=True).start()
        try:
            with patch('assistant.conversations.config.assistant',return_value={'name':'Synthetic','model':'test'}),patch('assistant.conversations.engine_for',side_effect=lambda _:Engine()),sync_playwright() as p:
                engine=os.environ.get('AREA_BROWSER','chromium');browser=getattr(p,engine).launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000});page.add_init_script(INIT)
                errors=[];writes=[];page.on('pageerror',lambda e:errors.append(str(e)))
                # One dialog handler: each expected confirm takes the next decision; an unexpected one is recorded and dismissed.
                decisions=[];dialogs=[]
                def on_dialog(dialog):
                    dialogs.append(dialog.message);(dialog.accept() if decisions and decisions.pop(0) else dialog.dismiss())
                page.on('dialog',on_dialog)
                bodies=[];page.on('request',lambda r:bodies.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/messages') else None)
                page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
                base=f'http://127.0.0.1:{server.effective_port}'
                page.goto(f'{base}/#gradebook/{fixture.term}?section={fixture.sections[507]}')
                bubble=page.get_by_role('dialog',name='Ask about this area',exact=True)
                gesture={'held':False}
                def drag(rect):
                    before=len(calls)
                    page.mouse.move(rect['x'],rect['y']);page.mouse.down()
                    page.mouse.move(rect['x']+rect['width'],rect['y']+rect['height']);page.mouse.up()
                    if gesture['held']:
                        # Mouse-up only finishes the box. The explicit Command release starts its analysis.
                        assert len(calls)==before
                        expect(page.locator('.assistant-area-selector')).to_be_visible()
                        page.keyboard.up('Meta');gesture['held']=False
                def arm():
                    page.keyboard.down('Meta');gesture['held']=True
                    expect(page.locator('.assistant-area-selector')).to_be_visible(timeout=15000)
                    expect(page.locator('.assistant-area-selector')).not_to_contain_text('Preparing',timeout=15000)
                def complete():
                    expect(bubble).to_contain_text('The selected content is ready',timeout=15000)
                    return calls[-1]
                def close():
                    bubble.get_by_role('button',name='Close selected-area chat',exact=True).click();expect(bubble).to_have_count(0)
                uploads=[]
                def lost(route):
                    uploads.append(route.request.url);route.fetch();route.abort('failed')
                page.route('**/conversations/*/captures',lost)
                cell=page.locator('[data-cell]').first;expect(cell).to_be_visible();cell.click();page.keyboard.type('7.25')
                editor=cell.locator('input.gb-input');expect(editor).to_have_value('7.25')
                # Native Command shortcuts must not become the first tap of area selection.
                page.keyboard.press('Meta+a');page.keyboard.press('Meta')
                expect(page.locator('.assistant-area-selector')).to_have_count(0)
                editor.click()  # pointer activity clears that single tap without blurring the editor
                # A hold without a box, Escape, and losing the window never send a selection or the draft field.
                count=len(calls)
                arm();page.keyboard.up('Meta');gesture['held']=False
                expect(page.locator('.assistant-area-selector')).to_have_count(0);assert len(calls)==count
                arm();page.keyboard.press('Escape');page.keyboard.up('Meta');gesture['held']=False
                expect(page.locator('.assistant-area-selector')).to_have_count(0);expect(editor).to_have_value('7.25')
                arm();page.evaluate("window.dispatchEvent(new Event('blur'))");page.keyboard.up('Meta');gesture['held']=False
                expect(page.locator('.assistant-area-selector')).to_have_count(0);assert len(calls)==count
                box=cell.bounding_box();arm();drag({'x':box['x']+2,'y':box['y']+2,'width':box['width']-4,'height':box['height']-4})
                first=complete();assert not first['images'] and first['readonly']
                assert '7.25' in first['text'] and 'Synthetic quiz' in first['text'] and 'unsaved edit' in first['text']
                assert len(uploads)==1;page.unroute('**/conversations/*/captures',lost)
                expect(editor).to_have_value('7.25');assert not any('/api/gradebook/' in url for url in writes)
                bubble.get_by_label('Message about this area').fill('Explain this score.');bubble.get_by_role('button',name='Send',exact=True).click()
                expect(bubble.locator('[data-area-stop]')).to_be_hidden(timeout=10000)
                assert not calls[-1]['readonly'] and '7.25' in calls[-1]['text']
                for width in (1440,1280):
                    page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    # Playwright 1.49 inserts inline `body {}` when WebKit takes a screenshot. Keep the CSP check free
                    # of that test-runner side effect; both layouts are captured in Chromium and exercised in WebKit.
                    if engine!='webkit':page.screenshot(path=f'assistant-selection-{engine}-{width}.png',caret='initial')
                message=bubble.get_by_label('Message about this area');message.fill('Keep this unsent message')
                # Another selection only hides this chat: cancelling it gives the chat back unchanged, without a question.
                count=len(calls);bubble.get_by_role('button',name='Select another area').click()
                expect(page.locator('.assistant-area-selector')).to_be_visible();expect(bubble).to_be_hidden()
                page.keyboard.press('Escape');expect(bubble).to_be_visible();expect(message).to_have_value('Keep this unsent message')
                assert dialogs==[]
                # ONE CHAT (requirement, 2026-10-09): a sent box goes to this open chat, a selection chat too: the same conversation,
                # a new read-only analysis, the unsent message kept and nothing asked.
                bubble.get_by_role('button',name='Select another area').click();expect(bubble).to_be_hidden()
                box=cell.bounding_box();drag({'x':box['x']+2,'y':box['y']+2,'width':box['width']-4,'height':box['height']-4})
                expect(bubble).to_be_visible();expect(message).to_have_value('Keep this unsent message')
                expect(bubble.locator('[data-area-stop]')).to_be_hidden(timeout=15000)
                assert dialogs==[] and len(calls)==count+1 and calls[-1]['readonly'] and calls[-1]['id']==calls[-2]['id']
                decisions.append(True);close();expect(editor).to_be_focused();expect(editor).to_have_value('7.25');page.keyboard.press('Escape')
                page.evaluate("""() => {
                  const host=document.createElement('div');host.id='selection-fixture';
                  Object.assign(host.style,{position:'fixed',left:'240px',top:'240px',width:'700px',height:'360px',zIndex:'100',background:'white',padding:'20px'});
                  host.innerHTML='<p><span id="chosen-words">Selected explanation</span> <span>UNSELECTED_WORDS</span></p><p hidden>HIDDEN_WORDS</p><label>Draft comment<textarea id="chosen-draft">Only this unsaved draft</textarea></label><label id="choice-label">Grading mode<select id="chosen-mode"><option>Selected mode</option><option>UNSELECTED_CHOICE</option></select></label><img id="chosen-image" src="/api/test-selection/image" alt="Synthetic handwritten graph">';
                  Object.assign(host.querySelector('img').style,{width:'400px',height:'300px',objectFit:'contain',position:'absolute',top:'120px',left:'20px'});
                  Object.assign(host.querySelector('#choice-label').style,{position:'absolute',left:'460px',top:'100px',width:'200px'});
                  document.body.append(host);
                }""")
                box=page.locator('#chosen-words').bounding_box();arm();drag(box);picked=complete()
                assert 'Selected explanation' in picked['text'] and 'UNSELECTED_WORDS' not in picked['text'] and 'HIDDEN_WORDS' not in picked['text'];close()
                # Command can be released before the mouse: it commits the currently drawn box exactly once.
                arm();page.mouse.move(box['x'],box['y']);page.mouse.down()
                page.mouse.move(box['x']+box['width'],box['y']+box['height'])
                page.keyboard.up('Meta');gesture['held']=False;page.mouse.up()
                assert 'Selected explanation' in complete()['text'];close()
                field=page.locator('#chosen-draft');box=field.bounding_box();arm();drag(box);picked=complete()
                assert 'Only this unsaved draft' in picked['text'] and 'may be unsaved' in picked['text'];expect(field).to_have_value('Only this unsaved draft');close()
                box=page.locator('#chosen-mode').bounding_box();arm();drag(box);picked=complete()
                assert 'Selected mode' in picked['text'] and 'UNSELECTED_CHOICE' not in picked['text'];close()
                picture=page.locator('#chosen-image');expect(picture).to_be_visible();page.wait_for_function('document.querySelector("#chosen-image").naturalWidth===400')
                box=picture.bounding_box();arm();drag({'x':box['x']+100,'y':box['y']+100,'width':150,'height':50});picked=complete()
                assert len(picked['images'])==1
                crop=Image.open(io.BytesIO(picked['images'][0][1]));assert crop.size==(150,50),crop.size
                assert crop.getpixel((0,0))==(255,0,0) and crop.getpixel((149,0))==(0,0,255)
                close()
                # SVG graphs retain their actual drawn lines, with no DOM screenshot library.
                page.evaluate("""() => {const host=document.querySelector('#selection-fixture');host.replaceChildren();host.innerHTML='<svg id="selected-graph" width="240" height="160" viewBox="0 0 240 160" aria-label="Selected graph"><rect width="240" height="160" fill="white"/><path d="M20 140 L100 20 L220 120" stroke="black" stroke-width="4" fill="none"/></svg>'; }""")
                box=page.locator('#selected-graph').bounding_box();arm();drag(box);picked=complete()
                graph=Image.open(io.BytesIO(picked['images'][0][1]));assert graph.convert('L').getextrema()[0]<80
                close();page.evaluate('document.querySelector("#selection-fixture").remove()')
                route=f'/api/documents/versions/{version["id"]}/preview'
                page.evaluate("route=>{const a=document.createElement('a');a.href=route;a.dataset.viewFile=route;a.dataset.viewName='Synthetic PDF';document.body.append(a);a.click();a.remove();}",route)
                viewer=page.locator('.file-viewer');expect(viewer).to_be_visible();expect(viewer.locator('iframe')).to_be_visible()
                page.evaluate('window.originalPdf=document.querySelector(".file-viewer iframe")')
                # Releasing during asynchronous PDF preparation restores the original viewer and sends nothing.
                pending=[];count=len(calls)
                def delay_preparation(route):pending.append(route)
                page.route('**/api/viewer/file?*',delay_preparation)
                with page.expect_request('**/api/viewer/file?*'):
                    page.keyboard.down('Meta');expect(page.locator('.assistant-area-selector')).to_contain_text('Preparing',timeout=15000)
                page.keyboard.up('Meta');expect(page.locator('.assistant-area-selector')).to_have_count(0)
                assert len(pending)==1
                with page.expect_response('**/api/viewer/file?*'):pending[0].continue_()
                page.unroute('**/api/viewer/file?*',delay_preparation)
                expect(viewer.locator('iframe')).to_be_visible();expect(page.locator('.file-viewer-selection-pages')).to_have_count(0)
                assert len(calls)==count and page.evaluate('document.querySelector(".file-viewer iframe")===originalPdf')
                # Double Command opens ordinary typed chat over the viewer, without selecting or starting a model turn.
                count=len(calls);captures=len([url for url in writes if url.endswith('/captures')])
                page.keyboard.press('Meta',delay=120);page.keyboard.press('Meta',delay=120)
                floating=page.get_by_role('dialog',name='Assistant chat',exact=True)
                expect(floating).to_be_visible();expect(floating.get_by_role('button',name='Send',exact=True)).to_be_enabled()
                assert len(calls)==count and len([url for url in writes if url.endswith('/captures')])==captures
                expect(viewer.locator('iframe')).to_be_visible();expect(page.locator('.file-viewer-selection-pages')).to_have_count(0)
                field=floating.get_by_label('Message the assistant');field.fill('Keep my unsent question')
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
                expect(field).to_have_value('Keep my unsent question');assert len(calls)==count
                page.keyboard.press('Meta',delay=120);page.keyboard.press('Meta',delay=120)
                expect(field).to_have_value('Keep my unsent question');expect(field).to_be_focused()
                def failed_message(route):route.fulfill(status=503,content_type='application/json',body='{"error":"Synthetic message unavailable"}')
                page.route('**/conversations/*/messages',failed_message)
                field.press('Enter');expect(floating.locator('[data-area-error]')).to_contain_text('Synthetic message unavailable')
                expect(field).to_have_value('Keep my unsent question');assert len(calls)==count
                page.unroute('**/conversations/*/messages',failed_message)
                if engine!='webkit':
                    page.screenshot(path='assistant-floating-chat.png')
                field.fill('Help me plan a class.');field.press('Enter')
                expect(floating).to_contain_text('The selected content is ready',timeout=15000)
                assert len(calls)==count+1 and not calls[-1]['readonly'] and 'Help me plan a class.' in calls[-1]['text']
                ident=calls[-1]['id'];floating.get_by_role('button',name='Open in Assistant').click()
                panel=page.locator('#assistant-panel');expect(panel).to_be_visible()
                expect(panel.locator('[data-assistant-chat]')).to_contain_text('Help me plan a class.')
                assert fixture.app.extensions['assistant'].conversations.get(ident).capture is None
                assert len(calls)==count+1 and len([url for url in writes if url.endswith('/captures')])==captures
                panel.get_by_role('button',name='Close the assistant',exact=True).click();expect(viewer).to_be_visible()
                viewer.get_by_role('button',name='Ask about an area',exact=True).click()
                expect(page.locator('.assistant-area-selector')).not_to_contain_text('Preparing',timeout=15000)
                pdf=page.locator('.file-viewer-selection-pages img').first;expect(pdf).to_be_visible()
                page.wait_for_function('document.querySelector(".file-viewer-selection-pages img").naturalWidth>0')
                box=pdf.bounding_box();drag({'x':box['x']+30,'y':box['y']+50,'width':200,'height':100});picked=complete()
                assert picked['images'] and '/api/viewer/page.png' in picked['text']
                assert page.evaluate('document.querySelector(".file-viewer iframe")===originalPdf')
                expect(viewer.locator('iframe')).to_be_visible();expect(page.locator('.file-viewer-selection-pages')).to_have_count(0)
                count=len(calls);ident=picked['id'];bubble.get_by_role('button',name='Open in Assistant').click()
                panel=page.locator('#assistant-panel');expect(panel).to_be_visible();expect(viewer).to_be_visible()
                expect(panel.locator('[data-assistant-chat]')).to_contain_text('The selected content is ready')
                assert len(calls)==count and fixture.app.extensions['assistant'].conversations.get(ident).capture
                panel.get_by_role('button',name='Close the assistant',exact=True).click();expect(viewer).to_be_visible()
                viewer.get_by_role('button',name='Close the viewer',exact=True).click()
                page.evaluate("() => {const p=document.createElement('p');p.id='lifecycle-selection';p.textContent='Lifecycle selection';Object.assign(p.style,{position:'fixed',left:'250px',top:'250px',zIndex:'200',background:'white'});document.body.append(p);}")
                for ending in ('close','hidden','navigation'):
                    answer.clear();box=page.locator('#lifecycle-selection').bounding_box();arm();drag(box)
                    expect(bubble.locator('[data-area-stop]')).to_be_visible(timeout=10000);ident=calls[-1]['id']
                    if ending=='close':close()
                    elif ending=='hidden':page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));delete document.hidden")
                    else:page.evaluate("window.dispatchEvent(new HashChangeEvent('hashchange'))")
                    expect(bubble).to_have_count(0)
                    # The bubble owned its conversation: releasing it stops the analysis and ends the conversation on the server.
                    page.wait_for_function("async id=>(await fetch('/api/assistant/conversations/'+id)).status===410",arg=ident)
                    assert answer.is_set() and ident not in fixture.app.extensions['assistant'].conversations._items
                # A finished selection chat survives a hidden tab with its unsent follow-up; only unfinished work is released.
                box=page.locator('#lifecycle-selection').bounding_box();arm();drag(box);complete();ident=calls[-1]['id']
                message.fill('Compare with Canvas');closes=len([url for url in writes if url.endswith('/close')])
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
                expect(bubble).to_be_visible();expect(message).to_have_value('Compare with Canvas')
                assert ident in fixture.app.extensions['assistant'].conversations._items and len([url for url in writes if url.endswith('/close')])==closes
                decisions.append(True);close()
                before=len(calls)
                page.evaluate("""() => {const dialog=document.createElement('dialog');dialog.id='private-selection';dialog.innerHTML='<p id="private-text">Private sign-in</p><label>Password<input type="password" value="SYNTHETIC_SECRET"></label>';document.body.append(dialog);dialog.showModal();}""")
                box=page.locator('#private-text').bounding_box();arm();drag(box)
                expect(page.locator('.toast')).to_contain_text('Sign-in and access-key views cannot be sent',timeout=10000)
                # FRONTEND: the toast is above the open modal dialog, and its Dismiss can be reached there.
                assert page.evaluate(TOAST_ON_TOP),'the toast is covered by the modal dialog'
                page.locator('#toast .toast-close').first.click();expect(page.locator('#toast .toast-item')).to_have_count(0)
                assert len(calls)==before;page.evaluate('document.querySelector("#private-selection").close();document.querySelector("#private-selection").remove()')
                with patch('assistant.conversations.config.assistant',return_value=None):
                    box=page.locator('#lifecycle-selection').bounding_box();arm();drag(box)
                    expect(page.locator('.toast')).to_contain_text('Assistant model',timeout=10000)
                    assert len(calls)==before
                    if bubble.count():close()
                floating_chat_beside_page_work(page,fixture,calls,writes,bodies,dialogs,decisions,answer,drag,floating)
                assert page.evaluate('captureCalls')==0
                assert page.evaluate('cspViolations')==[],page.evaluate('cspViolations');assert not errors,errors
                browser.close()
        finally:server.close()
    finally:answer.set();fixture.doCleanups()
    print(f'{engine}: hold/release selection, floating typed chat, drafts, crops, PDF restoration, handoff, no screen sharing, and lifecycle passed.')


if __name__=='__main__':main()
