"""FRONTEND/INBOX: stale searches, uploads and grouping previews keep their owning view."""
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
              const {createInbox}=await import('/inbox.js');
              const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
              document.body.insertAdjacentHTML('beforeend','<section id="inbox-section"></section>');location.hash='#inbox';
              window.pending=[];window.uploads=[];
              window.XMLHttpRequest=class {constructor(){this.upload={};}open(){}setRequestHeader(){}send(){uploads.push(this);}};
              const catalog={actions:[{name:'document.archive',label:'Archive',implemented:true,scopes:['course']}],targets:[]};
              const api=async path=>{
                if(path==='/api/inbox/catalog')return catalog;
                if(path==='/api/inbox/access'||path.startsWith('/api/picture-search/like'))return new Promise(resolve=>pending.push({path,resolve}));
                if(path.startsWith('/api/picture-search'))return {results:[]};
                return {jobs:[],counts:{},kind_counts:{scan:{},command:{}},assignments:[],total:0,has_more:false,worker_heartbeat:new Date().toISOString()};
              };
              window.inbox=createInbox({api,escape,icon:()=>'',toast(){},dialogHeader:title=>`<h2>${escape(title)}</h2>`,
                showDialog:html=>{const dialog=document.querySelector('#dialog');dialog.innerHTML=html;if(!dialog.open)dialog.showModal();}});
              inbox.render();
            }""")
            page.wait_for_selector('[data-inbox-like]')
            page.locator('[data-inbox-like]').set_input_files({"name": "search.png", "mimeType": "image/png", "buffer": b"synthetic"})
            page.wait_for_function("pending.length===1")
            page.locator('#inbox-search').fill("new search")
            page.locator('#inbox-search').press("Enter")
            page.evaluate("pending[0].resolve({results:[]})")
            expect(page.locator('#inbox-search')).to_have_value("new search")
            page.evaluate("inbox.uploadScans()")
            page.locator('#inbox-scan-upload input').set_input_files({"name": "scan.pdf", "mimeType": "application/pdf", "buffer": b"synthetic"})
            page.locator('#inbox-scan-upload button[type=submit]').click()
            page.wait_for_function("uploads.length===1")
            expect(page.locator('#inbox-scan-upload input')).to_be_disabled()
            page.evaluate("inbox.openSubmit()")
            page.wait_for_selector('#inbox-submit-form')
            page.evaluate("uploads[0].status=200;uploads[0].responseText=JSON.stringify({id:'scan',status:'needs_review'});uploads[0].onload()")
            expect(page.locator('#dialog')).to_be_visible()
            expect(page.locator('#inbox-submit-form')).to_be_visible()
            assert page.evaluate("location.hash") == "#inbox"
            assert not errors, errors
            page.close()

            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.evaluate("""async () => {
              const {mountScanReview}=await import('/scan-pages.js');window.pending=[];window.notices=[];
              document.body.insertAdjacentHTML('beforeend','<section id="inbox-section"><article class="inbox-job-detail"></article></section>');
              const item={decode:{status:'complete'},pages:[],assemblies:[],label_modes:[],overview:{state:'check',steps:[],stages:[{key:'papers',page:'papers',label:'Papers',state:'todo'}]},
                sequence:{runs:[],packets:[],labels:1,unidentified:1,suggested_sides:2,max_sides:20}};
              const api=(path,method,body)=>path.endsWith('/papers-to-check')?Promise.resolve({papers:[],ready:0,grouped:0,files:1,sides:2}):new Promise(resolve=>pending.push({path,body,resolve}));
              api.revalidate=async()=>({data:item});
              window.plan=sides=>({sides,fingerprint:`plan-${sides}`,counts:{ready:1,grouped:1,identified:0,complete:0,review:0,unlabeled:0},exams:[],runs:[]});
              await mountScanReview({root:document.querySelector('#inbox-section'),job:{id:'job',message_id:'message',action:'scan.receive'},api,escape:String,toast:text=>notices.push(text),page:'papers'});
            }""")
            sides = page.locator('form[data-sequence] input')
            page.locator('form[data-sequence] button').click()
            page.wait_for_function("pending.length===1")
            sides.fill("3")
            page.evaluate("pending[0].resolve(plan(2))")
            expect(page.locator('[data-sequence-apply]')).to_have_count(0)
            page.locator('form[data-sequence] button').click()
            page.wait_for_function("pending.length===2")
            page.evaluate("pending[1].resolve(plan(3))")
            page.locator('[data-sequence-apply]').click()
            page.wait_for_function("pending.length===3")
            sides.fill("4")
            page.evaluate("pending[2].resolve({})")
            page.wait_for_function("notices.length===1")
            assert "Grouped 1 paper" in page.evaluate("notices[0]")
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Inbox request and grouping preview ownership passed")


if __name__ == "__main__":
    main()
