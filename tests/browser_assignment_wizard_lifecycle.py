"""FRONTEND/DOCUMENTS: late creation responses cannot take over a newer or closed wizard."""
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
              const {createAssignmentWizard}=await import('/assignment-wizard.js');
              const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
              window.pending=[];window.writes=[];
              window.catalog=id=>({definitions:[{id,code:id}],assignments:[],versions:[],batches:[],lifecycle:[]});
              window.wizard=createAssignmentWizard({escape,toast(){},dialogHeader:t=>`<h2>${escape(t)}</h2>`,getSeating(){},refresh(){},onChanged(){},openBatch(){},
                showDialog:html=>{const dialog=document.querySelector('#dialog');dialog.innerHTML=html;if(!dialog.open)dialog.showModal();},
                api:(path,method='GET',body)=>{
                  if(path==='/api/documents/catalog')return new Promise(resolve=>pending.push({path,resolve}));
                  if(path==='/api/seating/catalog')return Promise.resolve({rooms:[],sections:['old','new'].map(id=>({id:`${id}-section`,definition_id:id,term_id:id,term_label:'Fall',is_current:true,number:501}))});
                  if(path==='/api/canvas/status')return Promise.resolve({configured:false});
                  if(method!=='GET')return new Promise(resolve=>writes.push({path,body,resolve}));
                  return Promise.resolve({rows:[]});
                }});
              void wizard.open({definitionId:'old'});void wizard.open({definitionId:'new'});
            }""")
            page.wait_for_function("pending.length===2")
            page.evaluate("pending[1].resolve(catalog('new'));pending[0].resolve(catalog('old'))")
            expect(page.locator('select[name=definition_id]')).to_have_value("new")
            page.locator('input[name=title]').fill("New assignment")
            page.locator('#assignment-wizard button[type=submit]').click()
            page.wait_for_function("writes.length===1")
            assert page.evaluate("writes[0].body.definition_id") == "new"
            # Closing the active creation cancels further steps; its first accepted write keeps its receipt on the server.
            page.locator('#dialog').evaluate("dialog=>dialog.close()")
            page.evaluate("writes[0].resolve({id:'created',assignment_key:'new-assignment',title:'New assignment'})")
            page.wait_for_timeout(50)
            expect(page.locator('#dialog')).not_to_be_visible()
            assert page.evaluate("writes.length") == 1
            assert page.evaluate("pending.length") == 2
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Assignment wizard session ownership passed")


if __name__ == "__main__":
    main()
