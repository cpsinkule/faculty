"""FRONTEND/DOCUMENTS: asynchronous print dialogs keep the instructor's latest intent."""
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
              const {createDocuments}=await import('/documents.js');
              const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
              document.body.insertAdjacentHTML('beforeend','<section id="documents-section" hidden></section><section id="workspace"></section>');
              window.pending=[];
              const catalog={assignments:[{id:'a',code:'MATH',title:'Exam'}],versions:[{id:'v',assignment_id:'a',label:'A',page_count:2}],
                definitions:[],sections:[],lifecycle:[],batches:[{id:'b',assignment_version_id:'v',term_id:'t',origin:'section',export:'b',quantity:2,status:'pending',created_at:'2026-10-01'}]};
              window.docs=createDocuments({escape,toast(){},refresh(){},getSeating(){},header:{set(){}},dialogHeader:title=>`<h2>${escape(title)}</h2>`,
                showDialog:html=>{const dialog=document.querySelector('#dialog');dialog.innerHTML=html;if(!dialog.open)dialog.showModal();},
                api:(path,method='GET',body)=>{
                  if(path==='/api/documents/catalog')return Promise.resolve(catalog);
                  if(path.includes('/assignments/status'))return Promise.resolve({configured:false});
                  return new Promise(resolve=>pending.push({path,method,body,resolve}));
                }});
              window.batch={id:'b',title:'Exam',version_label:'A',status:'pending',quantity:2,page_count:2,origin:'section',copies:[],events:[],code:'MATH',term:'Fall',year:2026};
              window.printer={configured:false,printer:null,two_sided_allowed:true,sends:[],pages:4,outputs:[]};
              window.print=()=>void docs.printAtHome({kind:'job',batchIds:['b'],subtitle:'Exam'},()=>{});
              await docs.renderWorkspace(document.querySelector('#workspace'),{assignment_id:'a',term_id:'t'},()=>true,()=>{});
            }""")
            page.locator('[data-batch="b"]').click()
            page.wait_for_function("pending.length===1")
            page.evaluate("print()")
            page.wait_for_function("pending.length===2")
            page.evaluate("pending[1].resolve(printer)")
            expect(page.locator('#dialog h2')).to_have_text("Print at Home")
            page.evaluate("pending[0].resolve(batch)")
            expect(page.locator('#dialog h2')).to_have_text("Print at Home")
            page.locator('#dialog').evaluate("dialog=>dialog.close()")
            # A completed rename keeps a newer dialog open.
            page.get_by_text('Assignment tools', exact=True).click()
            page.locator('[data-workspace-rename]').click()
            page.locator('#assignment-rename-form input').fill("Renamed exam")
            page.locator('#assignment-rename-form button[type=submit]').click()
            page.wait_for_function("pending.length===3")
            page.evaluate("print()")
            page.wait_for_function("pending.length===4")
            page.evaluate("pending[3].resolve(printer);pending[2].resolve({})")
            expect(page.locator('#dialog h2')).to_have_text("Print at Home")
            # Closing a pending initial read prevents it from reopening over the page.
            page.locator('#dialog').evaluate("dialog=>dialog.close()")
            page.evaluate("print();document.querySelector('#dialog').showModal();document.querySelector('#dialog').close()")
            page.wait_for_function("pending.length===5")
            page.evaluate("pending[4].resolve(printer)")
            expect(page.locator('#dialog')).not_to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Document dialog request ownership passed")


if __name__ == "__main__":
    main()
