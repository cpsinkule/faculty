"""FRONTEND/CANVAS: a pending editor request keeps its course, draft and dialog owner."""
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from browser_frontend_lifecycle import Assets


def main():
    root = Path(__file__).resolve().parents[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Assets, directory=str(root / "static")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.evaluate("""async () => {
              const {createCanvasContent}=await import('/canvas-content.js');
              const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
              const host=document.createElement('section');host.dataset.canvasContentHost='';document.body.append(host);
              window.pending=[];window.holdGroup=false;window.running=false;
              window.view=term=>({canvas:{configured:true},term:{code:term,term_label:'Fall'},revision:1,
                courses:[{course_id:term,name:term,sections:[{number:501}]}],sections:[],
                content:{modules:[{id:`${term}-module`,name:`${term} module`,items:[],published:true}],pages:[],assignments:[]}});
              window.openDialog=html=>{const d=document.querySelector('#dialog');d.innerHTML=html;if(!d.open)d.showModal();};
              window.editor=createCanvasContent({escape,icon:()=>'',toast(){},showDialog:openDialog,dialogHeader:t=>`<h2>${escape(t)}</h2>`,
                api:(path,method='GET',body)=>{
                  if(method!=='GET'||path.includes('/pushes/')||holdGroup)return new Promise(resolve=>pending.push({path,method,body:body instanceof FormData?body:structuredClone(body),resolve}));
                  if(path.includes('/group/'))return Promise.resolve({term_id:path.split('/').at(-1)});
                  const result=view(path.split('/').at(-1));if(running)result.push={id:'push-a',status:'running'};
                  return Promise.resolve(result);
                }});
              await editor.render('a');
            }""")
            # New rows receive the server's identity while later typing remains an unsaved draft.
            page.locator("[data-cc-add-module]").click()
            new_module = page.locator('[data-cc-module-name="1"]')
            new_module.fill("Before save")
            page.locator("[data-cc-save]").click()
            page.wait_for_function("pending.length===1")
            new_module.fill("Typed during save")
            page.evaluate("""() => {
              const saved=view('a');saved.revision=2;saved.content=pending[0].body.content;
              delete saved.content.modules[1].ref;saved.content.modules[1].id='assigned-module';pending[0].resolve(saved);
            }""")
            expect(page.locator('[data-cc-module-name="1"]')).to_have_value("Typed during save")
            expect(page.locator("[data-cc-savebar]")).to_contain_text("Unsaved changes")
            page.locator("[data-cc-save]").click()
            page.wait_for_function("pending.length===2")
            assert page.evaluate("pending[1].body.content.modules[1].id") == "assigned-module"
            assert page.evaluate("pending[1].body.content.modules[1].name") == "Typed during save"
            # Its delayed save response cannot replace another course's editor.
            page.evaluate("editor.render('b')")
            page.evaluate("pending[1].resolve({...view('a'),revision:3})")
            expect(page.locator('[data-cc-module-name="0"]')).to_have_value("b module")

            # A file uploaded in the abandoned course cannot attach itself to the replacement draft or close its dialog.
            page.locator('[data-cc-module="0"] summary').click()
            page.locator('[data-cc-add-type="0"]').select_option("File")
            page.locator('[data-cc-add-item="0"]').click()
            page.locator('[data-cc-file-form] input[type=file]').set_input_files({"name": "handout.txt", "mimeType": "text/plain", "buffer": b"example"})
            page.locator('[data-cc-file-form] button[type=submit]').click()
            page.wait_for_function("pending.length===3")
            page.evaluate("""async () => {
              await editor.render('c');openDialog('<h2>Replacement dialog</h2>');
              pending[2].resolve({id:'file-b',name:'handout.txt'});
            }""")
            expect(page.locator('#dialog h2')).to_have_text("Replacement dialog")
            expect(page.locator('[data-cc-module-name="0"]')).to_have_value("c module")
            assert not page.evaluate("editor.dirty()")
            page.locator('#dialog').evaluate("dialog=>dialog.close()")

            # A push read that finishes after navigation does not write its receipt to the new course.
            page.evaluate("""async () => {running=true;await editor.render('d');running=false;await editor.render('e');}""")
            page.wait_for_function("pending.length===4")
            page.evaluate("pending[3].resolve({id:'push-a',status:'done',progress:{courses:{}}})")
            expect(page.locator('[data-cc-module-name="0"]')).to_have_value("e module")
            expect(page.locator('[data-cc-push-note]')).to_have_count(0)

            # A replacement host joins only its own read, even when it opens the same course.
            page.evaluate("""() => {
              holdGroup=true;void editor.render('f');
              document.querySelector('[data-canvas-content-host]').remove();
              const next=document.createElement('section');next.dataset.canvasContentHost='';document.body.append(next);
              void editor.render('f');
            }""")
            page.wait_for_function("pending.length===6")
            page.evaluate("holdGroup=false;pending[5].resolve({term_id:'f'});pending[4].resolve({term_id:'abandoned'})")
            expect(page.locator('[data-cc-module-name="0"]')).to_have_value("f module")
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Canvas editor request ownership passed")


if __name__ == "__main__":
    main()
