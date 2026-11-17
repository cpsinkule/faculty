"""FRONTEND: late responses preserve Canvas choices and assignment identity; no outside service is used."""
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class Assets(http.server.SimpleHTTPRequestHandler):
    PAGE = b'<!doctype html><dialog id="dialog"></dialog>'  # the lifecycle scripts that share this handler serve their own page

    def do_GET(self):
        if self.path == "/":
            body = self.PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, *_args):
        pass


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Assets, directory=str(ROOT / "static")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            # Retained font faces still need a valid CSS source for older asset references.
            fonts = page.evaluate("""async () => {
              const sheet=new CSSStyleSheet(); sheet.replaceSync(await (await fetch('/fonts/fonts.css')).text());
              return [...sheet.cssRules].filter(rule=>rule instanceof CSSFontFaceRule).map(rule=>({family:rule.style.fontFamily,src:rule.style.src}));
            }""")
            assert fonts and all(font["src"] for font in fonts), fonts
            page.evaluate("""async () => {
              const {createCanvasPush} = await import('/canvas-push.js');
              const esc = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
              window.pending = []; window.started = [];
              window.openDialog = html => {const d = document.querySelector('#dialog'); d.innerHTML = html; if (!d.open) d.showModal();};
              window.controller = createCanvasPush({
                api: (path, method, body) => new Promise(resolve => pending.push({path, body, resolve})), escape: esc, toast() {},
                showDialog: openDialog, dialogHeader: title => `<h2>${esc(title)}</h2>`
              });
              window.options = title => ({title, subtitle: '', courses: [{course_id:'course', sections:[{number:501}], name:'Course'}],
                pushed:['course'], previewPath:`/${title}/preview`, pushPath:`/${title}/push`, onStarted: result => started.push([title,result])});
              window.preview = (replace = false) => ({fingerprint: replace ? 'replace' : 'keep', choices:{course:{replace:replace?['page:p1']:[],remove:[]}},
                courses:[{course_id:'course',name:'Course',sections:[{number:501}],attention:[{key:'page:p1',label:'Page',kind:'page',verdict:'differs'}],
                  new:[],blocked:[],extra:[],remove:[],unclaimed:{},counts:{},ops:replace?[{op:'update_page',label:'Page'}]:[]} ]});
              window.start = title => {void controller.open(options(title));};
              start('first');
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("pending[0].resolve(preview())")
            replace = page.locator("[data-cc-replace]")
            expect(replace).not_to_be_checked()
            replace.check()
            page.wait_for_function("pending.length === 2")
            expect(page.locator("[data-cc-push]")).to_be_disabled()
            replace.uncheck()
            page.wait_for_function("pending.length === 3")
            page.evaluate("pending[2].resolve(preview(false))")
            expect(replace).not_to_be_checked()
            page.evaluate("pending[1].resolve(preview(true))")
            page.wait_for_timeout(50)
            expect(replace).not_to_be_checked()
            expect(page.locator("[data-cc-push]")).to_have_text("Nothing to push")

            # A preview from an earlier window must not paint a newly opened window.
            page.evaluate("start('old'); start('new')")
            page.wait_for_function("pending.length === 5")
            page.evaluate("pending[4].resolve(preview(false)); pending[3].resolve(preview(true))")
            expect(page.locator("h2")).to_have_text("new")
            expect(replace).not_to_be_checked()

            # Closing the window before its read finishes never reopens it.
            page.evaluate("start('closed'); document.querySelector('#dialog').close()")
            page.wait_for_function("pending.length === 6")
            page.evaluate("pending[5].resolve(preview(true))")
            page.wait_for_timeout(50)
            expect(page.locator("#dialog")).not_to_be_visible()

            # The receipt goes to its original callback without closing a replacement dialog.
            page.evaluate("start('sending')")
            page.wait_for_function("pending.length === 7")
            page.evaluate("pending[6].resolve(preview(true))")
            page.locator("[data-cc-push]").click()
            page.wait_for_function("pending.length === 8")
            assert page.evaluate("pending[7].body.fingerprint") == "replace"
            page.evaluate("openDialog('<h2>Another window</h2>'); pending[7].resolve({id:'sent'})")
            page.wait_for_function("started.length === 1")
            assert page.evaluate("started") == [["sending", {"id": "sent"}]]
            expect(page.locator("#dialog")).to_be_visible()
            expect(page.locator("h2")).to_have_text("Another window")

            page.evaluate("""async () => {
              const {createCanvasMessage}=await import('/canvas-message.js');
              window.pending=[]; window.messageReceipts=0;
              window.canvasMessages=createCanvasMessage({api:(path,method,body)=>new Promise((resolve,reject)=>pending.push({path,method,body,resolve,reject})),
                escape: value=>String(value),toast() {},showDialog:openDialog,dialogHeader:title=>`<h2>${title}</h2>`,onSent:()=>messageReceipts++});
              window.messageOptions={canvas:{configured:true},terms:[{term_id:'t',code:'MATH',term_label:'Fall',
                sections:[{section_id:'s',number:501,students:1,course:{course_id:'c',name:'Course'}}]}]};
              window.messagePreview={fingerprint:'fingerprint',recipients:1,courses:[],unlinked:[],not_reached:[],left_out:[],subject:'Subject',body:'Body'};
              void canvasMessages.open();
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("pending[0].resolve(messageOptions)")
            page.locator(".cm-compose [name=subject]").fill("Subject")
            page.locator(".cm-compose [name=body]").fill("Body")
            page.evaluate("document.querySelector('.cm-compose').requestSubmit()")
            page.wait_for_function("pending.length === 2")
            page.evaluate("openDialog('<h2>Keep this replacement</h2>'); pending[1].resolve(messagePreview)")
            page.wait_for_timeout(50)
            expect(page.locator("h2")).to_have_text("Keep this replacement")
            page.evaluate("void canvasMessages.open()")
            page.wait_for_function("pending.length === 3")
            page.evaluate("pending[2].resolve(messageOptions)")
            page.locator(".cm-compose [name=subject]").fill("Subject")
            page.locator(".cm-compose [name=body]").fill("Body")
            page.evaluate("document.querySelector('.cm-compose').requestSubmit()")
            page.wait_for_function("pending.length === 4")
            page.evaluate("pending[3].resolve(messagePreview)")
            page.wait_for_function("pending.length === 5")
            page.evaluate("pending[4].resolve({}); document.querySelector('.cm-review').requestSubmit()")
            page.wait_for_function("pending.length === 6")
            page.evaluate("openDialog('<h2>Keep this send replacement</h2>'); pending[5].resolve({status:'accepted',recipients:1,courses:[]})")
            page.wait_for_function("messageReceipts === 1")
            expect(page.locator("h2")).to_have_text("Keep this send replacement")
            expect(page.locator("#dialog")).to_be_visible()

            # Cancelling this flow's sign-in keeps its draft; a stale 401 cannot launch sign-in over another dialog.
            page.evaluate("void canvasMessages.open()")
            page.wait_for_function("pending.length === 7")
            page.evaluate("pending[6].resolve(messageOptions)")
            page.locator(".cm-compose [name=subject]").fill("Keep this draft")
            page.locator(".cm-compose [name=body]").fill("Keep this body")
            page.evaluate("document.querySelector('.cm-compose').requestSubmit()")
            page.wait_for_function("pending.length === 8")
            page.evaluate("pending[7].reject(Object.assign(new Error('Sign in'),{status:401}))")
            expect(page.locator("[data-aim-signin]")).to_be_visible()
            page.wait_for_function("pending.length === 9")
            page.evaluate("document.querySelector('#dialog').close(); pending[8].resolve({saved:false})")
            expect(page.locator(".cm-compose [name=subject]")).to_have_value("Keep this draft")
            expect(page.locator(".cm-compose [name=body]")).to_have_value("Keep this body")
            page.evaluate("document.querySelector('.cm-compose').requestSubmit()")
            page.wait_for_function("pending.length === 10")
            page.evaluate("openDialog('<h2>Keep this instead of sign-in</h2>'); pending[9].reject(Object.assign(new Error('Sign in'),{status:401}))")
            page.wait_for_timeout(50)
            expect(page.locator("h2")).to_have_text("Keep this instead of sign-in")
            assert page.evaluate("pending.length") == 10

            page.evaluate("""async () => {
              const {createCanvasHub}=await import('/canvas-hub.js');
              const host=document.createElement('section'); host.id='canvas-section'; document.body.append(host);
              window.pending=[]; window.canvasHeadings=0; location.hash='#canvas';
              window.hub=createCanvasHub({api:path=>new Promise(resolve=>pending.push({path,resolve})),escape:value=>String(value),
                header:{set:()=>canvasHeadings++}});
              window.hubData=revision=>({canvas:{configured:true},terms:[{group:'group',term:{code:'MATH',term_label:'Fall'},next:'up_to_date',
                copy:{revision},sections:[{number:501,course:{course_id:'course',name:'Course'}}],courses:[{course_id:'course',pushed:true}]}]});
              void hub.render(); location.hash='#rooms';
            }""")
            page.wait_for_function("pending.length === 2")
            page.evaluate("pending[0].resolve(hubData(1)); pending[1].resolve({sent:[]})")
            page.wait_for_timeout(50)
            assert page.evaluate("canvasHeadings") == 1
            page.locator("#canvas-section details").evaluate("node=>node.open=true")
            page.evaluate("location.hash='#canvas'; void hub.render(true)")
            page.wait_for_function("pending.length === 4")
            page.evaluate("pending[2].resolve(hubData(2)); pending[3].resolve({sent:[]})")
            expect(page.locator("#canvas-section details")).to_have_attribute("open", "")

            page.evaluate("""async () => {
              document.querySelector('#dialog').close();
              const {createCollectionReview} = await import('/assignment-collection.js');
              const host = document.createElement('main'); document.body.append(host);
              window.pending = []; window.currentExam = 'a';
              const controller = createCollectionReview({
                api: (path, method, body) => new Promise(resolve => pending.push({path,method,body,resolve})),
                escape: value => String(value), toast() {}
              });
              window.exam = (id, reason = '') => ({participants:[{section_id:id,enrollment_id:id,name:`Student ${id}`,section_number:501,version:1,expectation:'expected',reason}],
                reconciliation:{students:[{section_id:id,enrollment_id:id,status:'complete'}]}});
              window.showCollection = id => {window.currentExam=id; void controller.show(host,id,()=>window.currentExam===id,async()=>{});};
              showCollection('a');
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("pending[0].resolve(exam('a'))")
            reason = page.locator("[name=reason]")
            expect(reason).to_be_visible()
            reason.fill("First exam decision")
            page.get_by_role("button", name="Save expectation").click()
            page.wait_for_function("pending.length === 2")
            page.evaluate("showCollection('b')")
            page.wait_for_function("pending.length === 3")
            page.evaluate("pending[2].resolve(exam('b'))")
            expect(page.locator(".collection-student h3")).to_have_text("Student b")
            page.evaluate("pending[1].resolve(exam('a','First exam decision'))")
            page.wait_for_timeout(50)
            expect(page.locator(".collection-student h3")).to_have_text("Student b")
            expect(reason).to_have_value("")

            # A poll begun before the save returned cannot replace its new participant version/words.
            reason.fill("Saved second decision")
            page.get_by_role("button", name="Save expectation").click()
            page.wait_for_function("pending.length === 4")
            page.evaluate("showCollection('b')")
            page.wait_for_function("pending.length === 5")
            page.evaluate("pending[3].resolve(exam('b','Saved second decision'))")
            expect(reason).to_have_value("Saved second decision")
            page.evaluate("pending[4].resolve(exam('b','Obsolete poll'))")
            page.wait_for_timeout(50)
            expect(reason).to_have_value("Saved second decision")
            expect(page.locator("[data-collection-dirty]")).to_have_count(0)

            # PERFORMANCE / FRONTEND: the delayed first-paint grading read belongs only to a still-visible Home page.
            page.evaluate("""async () => {
              const {createHome} = await import('/home.js');
              const host = document.createElement('div'); host.id='overview-content'; document.body.append(host);
              document.body.dataset.workspaceView='overview';
              window.homeReads=[];
              window.home = createHome({api: async path => {homeReads.push(path);return {events:[],today:'2026-10-24'};},
                escape: value => String(value), icon: () => '', courseHref: () => '#current-courses'});
              home.renderCourses([]);
              document.body.dataset.workspaceView='rooms';
            }""")
            page.wait_for_timeout(350)
            assert "/api/grading?health=0" not in page.evaluate("homeReads")

            # ASSISTANT: task reads and follow-up drafts stay with the task that owns them.
            page.evaluate("""async () => {
              const {createCodeTab} = await import('/assistant-code.js');
              const host = document.createElement('section'); host.id='code-tab'; document.body.append(host);
              window.pending=[];
              window.code = createCodeTab({host, toast() {}, api: path => new Promise(resolve => pending.push({path,resolve}))});
              window.codeList = {tasks:['a','b','c'].map(id => ({id,title:`Task ${id}`,state:'ready'})),coder:null};
              window.task = id => ({id,title:`Task ${id}`,state:'ready',request:'Change this task',files:[{path:'file.js',added:1,removed:0}],diff:'+new',rounds:[]});
              code.show('a');
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("pending[0].resolve(codeList)")
            page.wait_for_function("pending.length === 2")
            page.evaluate("pending[1].resolve(task('a'))")
            followup = page.locator("#code-tab textarea")
            followup.fill("Guidance for task A")
            page.evaluate("code.show('b')")
            page.wait_for_function("pending.length === 3")
            page.evaluate("pending[2].resolve(codeList)")
            page.wait_for_function("pending.length === 4")
            page.evaluate("code.show('c')")
            page.wait_for_function("pending.length === 5")
            page.evaluate("pending[4].resolve(codeList)")
            page.wait_for_function("pending.length === 6")
            page.evaluate("pending[5].resolve(task('c'))")
            expect(page.locator("[data-code-task]")).to_have_attribute("data-code-task", "c")
            expect(followup).to_have_value("")
            followup.fill("Guidance for task C")
            page.evaluate("pending[3].resolve(task('b'))")
            page.wait_for_timeout(50)
            expect(page.locator("[data-code-task]")).to_have_attribute("data-code-task", "c")
            expect(followup).to_have_value("Guidance for task C")
            page.evaluate("code.show('a')")
            page.wait_for_function("pending.length === 7")
            page.evaluate("pending[6].resolve(codeList)")
            page.wait_for_function("pending.length === 8")
            page.evaluate("pending[7].resolve(task('a'))")
            expect(followup).to_have_value("Guidance for task A")
            page.locator("#code-tab details").click()
            followup.focus()
            followup.press("Home")
            followup.press("ArrowRight")
            page.evaluate("code.show('a')")
            page.wait_for_function("pending.length === 9")
            page.evaluate("pending[8].resolve(codeList)")
            page.wait_for_function("pending.length === 10")
            page.evaluate("pending[9].resolve(task('a'))")
            expect(followup).to_be_focused()
            assert followup.evaluate("field => field.selectionStart") == 1
            expect(page.locator("#code-tab details")).to_have_attribute("open", "")
            page.evaluate("code.hide()")

            page.evaluate("""async () => {
              const {createWorkspaceTab} = await import('/assistant-workspace.js');
              const host=document.createElement('section'); host.id='saved-tab'; document.body.append(host);
              window.pending=[]; window.currentChat={id:'first',saved:{title:'First chat'}};
              window.saved=createWorkspaceTab({host,toast() {},current:()=>currentChat,ensure:async()=>currentChat,opened() {},
                api:path=>new Promise(resolve=>pending.push({path,resolve}))});
              window.savedData={conversations:[],memory:{memories:[{id:'memory',content:'Remember this',status:'current',revision:1,scope:'workspace'}]}};
              saved.show();
            }""")
            page.wait_for_function("pending.length === 2")
            page.evaluate("pending[0].resolve(savedData); pending[1].resolve({})")
            saved_title = page.locator("#saved-tab [name=title]")
            page.locator("#saved-tab [data-patch-key=saved-first]").evaluate("node=>node.open=true")
            saved_title.fill("Unsent first-chat title")
            page.evaluate("currentChat={id:'second',saved:{title:'Second chat'}}; void saved.show()")
            page.wait_for_function("pending.length === 4")
            page.evaluate("pending[2].resolve(savedData); pending[3].resolve({})")
            expect(saved_title).to_have_value("Second chat")
            # The memories themselves are the Memory page's (ASSISTANT-MEMORY "ONE PAGE"): the tab counts them and links there.
            expect(page.locator("#saved-tab")).to_contain_text("1 memory kept.")
            expect(page.locator("#saved-tab [data-memory-remove], #saved-tab form[data-memory]")).to_have_count(0)
            expect(page.locator('#saved-tab a[href="#memory"]')).to_have_text("Open Memory")
            page.evaluate("saved.hide()")
            page.wait_for_timeout(50)
            assert page.evaluate("pending.length") == 4

            page.evaluate("""async () => {
              const {createAreaAssistant} = await import('/assistant-area.js');
              window.pending=[];
              window.area=createAreaAssistant({api:(path,method,body)=>new Promise(resolve=>pending.push({path,method,body,resolve})),
                toast() {},icon:()=>'',registerShortcut() {},openConversation:async()=>true});
              window.chat=id=>({id,events:[],busy:false});
              void area.openChat();
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("pending[0].resolve(chat('old'))")
            page.locator("#area-question").fill("Old chat message")
            page.evaluate("document.querySelector('.assistant-area-compose').requestSubmit()")
            page.wait_for_function("pending.length === 2")
            page.evaluate("area.close(); void area.openChat()")
            page.wait_for_function("pending.length === 4")
            page.evaluate("pending[3].resolve(chat('new'))")
            page.locator("#area-question").fill("New chat message")
            page.evaluate("document.querySelector('.assistant-area-compose').requestSubmit()")
            page.wait_for_function("pending.length === 5")
            page.evaluate("pending[1].resolve(chat('old'))")
            page.wait_for_timeout(50)
            page.locator("#area-question").fill("Queue behind the new message")
            page.evaluate("document.querySelector('.assistant-area-compose').requestSubmit()")
            page.wait_for_timeout(50)
            assert page.evaluate("pending.length") == 5
            expect(page.locator("[data-chat-queued]")).to_contain_text("Queue behind the new message")
            page.evaluate("area.close()")

            page.evaluate("""async () => {
              const {createAssistant} = await import('/assistant.js');
              sessionStorage.removeItem('faculty:assistant:conversation');
              window.pending=[];
              const api=(path,method,body)=>path.endsWith('/status')?Promise.resolve({available:true,pending:0})
                :path.endsWith('/actions')?Promise.resolve({pending:[],recent:[],batches:[]})
                :new Promise(resolve=>pending.push({path,method,body,resolve}));
              window.drawer=createAssistant({api,toast() {},icon:()=>'',registerShortcut() {},showDialog() {},dialogHeader:()=>''});
              await drawer.open();
            }""")
            drawer_box = page.locator("#assistant-text")
            drawer_new = page.locator("[data-assistant=new]")
            drawer_box.fill("Discard this pending creation")
            drawer_box.press("Enter")
            page.wait_for_function("pending.length === 1")
            drawer_new.click()
            drawer_box.fill("Second conversation")
            drawer_box.press("Enter")
            page.wait_for_function("pending.length === 2")
            page.evaluate("pending[0].resolve(chat('a'))")
            page.wait_for_function("pending.length === 3")
            assert page.evaluate("pending[2].path") == "/api/assistant/conversations/a/close"
            page.evaluate("pending[1].resolve(chat('b'))")
            page.wait_for_function("pending.length === 4")
            assert page.evaluate("pending[3].path") == "/api/assistant/conversations/b/messages"
            drawer_new.click()
            drawer_box.fill("Third conversation")
            drawer_box.press("Enter")
            page.wait_for_function("pending.length === 6")
            page.evaluate("pending[5].resolve(chat('c'))")
            page.wait_for_function("pending.length === 7")
            page.evaluate("pending[3].resolve({...chat('b'),events:[{seq:1,kind:'assistant',text:'Old answer'}]})")
            page.wait_for_timeout(50)
            expect(page.locator("#assistant-panel [data-from=assistant]")).to_have_count(0)
            drawer_box.fill("Queue behind the third message")
            drawer_box.press("Enter")
            page.wait_for_timeout(50)
            assert page.evaluate("pending.length") == 7
            expect(page.locator("#assistant-panel [data-chat-queued]")).to_contain_text("Queue behind the third message")
            page.evaluate("void drawer.openConversation('older')")
            page.wait_for_function("pending.length === 8")
            drawer_new.click()
            page.evaluate("pending[7].resolve({...chat('older'),events:[{seq:1,kind:'assistant',text:'Obsolete open'}]})")
            page.wait_for_timeout(50)
            expect(page.locator("#assistant-panel [data-from=assistant]")).to_have_count(0)
            page.evaluate("drawer.close()")

            page.evaluate("""async () => {
              const {createTeachingReview}=await import('/teaching-review.js');
              const section=document.createElement('section');section.id='teaching-review-section';document.body.append(section);
              window.pending=[];
              window.teaching=createTeachingReview({api:path=>new Promise(resolve=>pending.push({path,resolve})),escape:String,toast(){},header:{set(){}},ask(){},showDialog(){},dialogHeader(){}});
              location.hash='#teaching-review/a';void teaching.render();
              location.hash='#teaching-review/b';void teaching.render();
            }""")
            page.wait_for_function("pending.length === 6")
            page.evaluate("pending[3].resolve({assessments:[]});pending[4].resolve({title:'B',certification:null});pending[5].resolve({counts:{open:2}})")
            expect(page.locator("[data-review-regrades] b")).to_have_text("2")
            page.evaluate("pending[0].resolve({assessments:[]});pending[1].resolve({title:'A',certification:null});pending[2].resolve({counts:{open:9}})")
            page.wait_for_timeout(50)
            expect(page.locator("[data-review-regrades] b")).to_have_text("2")

            page.evaluate("""async () => {
              const {createCourseNotes}=await import('/course-notes.js');
              const host=document.createElement('section');host.setAttribute('data-course-notes-host','');document.body.append(host);
              window.pending=[];window.notesHeader=null;
              window.notes=createCourseNotes({api:(path,method,body)=>new Promise(resolve=>pending.push({path,method,body,resolve})),
                escape:String,icon:()=>'',toast(){},showDialog:openDialog,dialogHeader:title=>`<h2>${title}</h2>`,header:{set:spec=>notesHeader=spec}});
              window.noNotes={current:null,earlier:[],removed:[],max_bytes:100000000};
              location.hash='#course/a/home/notes';void notes.render({id:'a',definition_id:'a',code:'A'});
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("pending[0].resolve(noNotes)")
            expect(page.locator("[data-course-notes-host]")).to_contain_text("No lecture notes yet")
            page.evaluate("notes.openUpload()")
            page.locator("#course-notes-upload [type=file]").set_input_files({"name": "notes.pdf", "mimeType": "application/pdf", "buffer": b"fake pdf"})
            page.evaluate("document.querySelector('#course-notes-upload').requestSubmit()")
            page.wait_for_function("pending.length === 2")
            page.evaluate("location.hash='#course/b/home/notes'; void notes.render({id:'b',definition_id:'b',code:'B'})")
            page.wait_for_function("pending.length === 3")
            page.evaluate("pending[2].resolve(noNotes);openDialog('<h2>Keep notes replacement</h2>')")
            page.wait_for_function("notesHeader.context.startsWith('B')")
            page.evaluate("pending[1].resolve({...noNotes,current:{title:'Old notes',sections:1}})")
            page.wait_for_timeout(50)
            expect(page.locator("[data-course-notes-host]")).to_contain_text("No lecture notes yet")
            expect(page.locator("#dialog h2")).to_have_text("Keep notes replacement")
            assert page.evaluate("notesHeader.context") == "B · every term"

            page.evaluate("""async () => {
              document.querySelector('#dialog').close();
              const {createPalette}=await import('/palette.js');
              localStorage.removeItem('faculty:recent-places');window.pending=[];
              window.palette=createPalette({api:path=>new Promise(resolve=>pending.push({path,resolve})),icon:()=>'',
                pages:()=>['A','B','C'].map(letter=>({group:'Pages',label:`Item ${letter}`,href:`#item-${letter}`})),actions:()=>[],
                courses:()=>({courseGroups:[],courseHref:()=>'',rooms:[],seating:[]}),shortcuts:()=>[]});
              palette.open();
            }""")
            palette_input = page.locator("[data-palette-input]")
            palette_input.fill("item")
            palette_input.press("PageUp")
            expect(page.locator(".palette-list [aria-selected=true]")).to_have_text("Item B")
            # A completed source read keeps the item reached with the keyboard.
            page.evaluate("pending.find(p=>p.path==='/api/documents/catalog').resolve({assignments:[]})")
            page.wait_for_timeout(50)
            expect(page.locator(".palette-list [aria-selected=true]")).to_have_text("Item B")
            palette_input.fill("show item now")
            palette_input.press("Escape")
            page.wait_for_timeout(450)
            assert not page.evaluate("pending.some(p=>p.path==='/api/jev/route')")

            page.evaluate("""async () => {
              const {createAssignmentWorkspace}=await import('/assignment-workspace.js');
              const host=document.createElement('section');host.id='assignment-workspace';document.body.append(host);
              window.preparationRequests=0;
              const record={assignment_id:'a',term_id:'t',code:'MATH',term_label:'Fall',title:'Assignment',scans:[],
                workflow:{},lifecycle:{stages:[]},activity:[],needs_you:[{key:'preparation',href:'#setup',label:'Check setup'}]};
              const workspace=createAssignmentWorkspace({api:async path=>path==='/api/grading/settings'?{settings:{}}:record,
                escape:String,toast(){},header:{set(){},reset(){}},documents:()=>({}),showDialog(){},dialogHeader(){},ask:async()=>preparationRequests++});
              location.hash='#assignments/a/t';
              for(let i=0;i<3;i++)await workspace.render('assignments',true);
            }""")
            page.locator("[data-prepare-assignment]").click()
            page.wait_for_timeout(50)
            assert page.evaluate("preparationRequests") == 1
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Frontend request ownership and quiet refresh passed")


if __name__ == "__main__":
    main()
