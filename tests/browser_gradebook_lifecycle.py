"""GRADEBOOK / FRONTEND: delayed reads and writes keep their section, preview and dialog."""
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import browser_frontend_lifecycle as frontend

ROOT = Path(__file__).resolve().parents[1]


class Assets(frontend.Assets):
    PAGE = b'<!doctype html><div data-gradebook-host></div><div id="changes"></div><dialog id="dialog"></dialog>'


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Assets, directory=str(ROOT / "static")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            address = f"http://127.0.0.1:{server.server_port}/"
            page.goto(address)
            page.evaluate("""async () => {
              window.calls=[];
              window.showDialog=html=>{const d=document.querySelector('#dialog');d.innerHTML=html;if(!d.open)d.showModal();};
              window.view={term:{id:'t'},sections:[{id:'s1',number:501}],students:[{row:'s1:a',section_id:'s1',section:501,name:'Alice'}],
                setup:{items:[{id:'q',title:'Quiz',points:'10'}],categories:[],scale:[]},cells:{q:{'s1:a':{id:'old',status:'scored',points:'1'}}},results:{},links:{}};
              window.ctx={termId:'t',scopeSection:'s1',view,escape:v=>String(v??''),icon:()=>'',toast(){},showDialog,
                dialogHeader:t=>`<h2>${t}</h2>`,requestId:()=>crypto.randomUUID(),replaceView(){},applyScores(){},
                api:(path,method,body)=>new Promise((resolve,reject)=>calls.push({path,method,body,resolve,reject}))};
              window.dialogs=await import('/gradebook-dialogs.js');
              dialogs.openPostingChoice(ctx,'q','hide');
              document.querySelector('#gradebook-posting-form').requestSubmit();
              showDialog('<h2>Replacement dialog</h2>');calls[0].resolve(view);
            }""")
            expect(page.locator("#dialog h2")).to_have_text("Replacement dialog")
            expect(page.locator("#dialog")).to_be_visible()

            # A history save must preserve edits made while it was in flight, and a clear must forget the old cell.
            page.evaluate("""() => {
              calls=[];ctx.api=(path,method,body)=>path.includes('/history?')?Promise.resolve({revisions:[]}):new Promise(resolve=>calls.push({path,body,resolve}));
              dialogs.openHistory(ctx,'q','s1:a');
            }""")
            page.locator('#gradebook-history-form [name="points"]').fill("3")
            page.locator('#gradebook-history-form [type="submit"]').click()
            page.wait_for_function("calls.length===1")
            page.locator('#gradebook-history-form [name="points"]').fill("9")
            page.evaluate("calls[0].resolve({cells:{q:{'s1:a':{id:'saved',status:'scored',points:'3'}}},results:{}})")
            expect(page.locator("#gradebook-history-form")).to_have_attribute("data-saving", "false")
            expect(page.locator('#gradebook-history-form [name="points"]')).to_have_value("9")
            page.locator('#gradebook-history-form [name="status"]').select_option("cleared")
            page.locator('#gradebook-history-form [type="submit"]').click()
            page.wait_for_function("calls.length===2")
            page.evaluate("calls[1].resolve({cells:{q:{'s1:a':null}},results:{}})")
            expect(page.locator("#gradebook-history-form")).to_have_attribute("data-saving", "false")
            expect(page.locator('#gradebook-history-form [name="points"]')).to_have_value("")
            page.evaluate("view.setup.items[0].canvas='quiz';dialogs.openHistory(ctx,'q','s1:a')")
            expect(page.locator('#gradebook-history-form [type="submit"]')).to_be_disabled()

            # A source preview cannot restore old fields while the instructor is typing a new title.
            page.evaluate("""async () => {
              window.plan={columns:[{key:'c',title:'New quiz',published:true,mapping:{action:'new',title:'New quiz',points:'10'},counts:{add:1}}],
                new_items:[{title:'New quiz',points:'10'}],changes:[],counts:{add:1},courses:[],options:{},ready:true,fingerprint:'first'};
              calls=[];ctx.scopeSection='';ctx.api=(path,method,body)=>new Promise(resolve=>calls.push({path,body,resolve}));
              void dialogs.openIClickerImport(ctx);calls[0].resolve(structuredClone(plan));
            }""")
            page.locator("[data-row-edit]").click()
            page.locator("[data-missing]").check()
            page.wait_for_function("calls.length===2")
            page.locator("[data-new-title]").fill("My new title")
            page.evaluate("calls[1].resolve({...structuredClone(plan),fingerprint:'obsolete'})")
            expect(page.locator("[data-new-title]")).to_have_value("My new title")
            expect(page.locator('#gradebook-iclicker-form [type="submit"]')).to_be_disabled()

            # Refresh invalidates both cached details and detail reads still in flight.
            page.evaluate("""async () => {
              document.querySelector('#dialog').close();calls=[];
              window.change={id:'r',type:'request',origin:'typed',created_at:'2026-01-01T12:00:00Z',cells:{here:1},items:['q']};
              let first=true;ctx.api=(path)=>first?(first=false,Promise.resolve({changes:[change]})):new Promise(resolve=>calls.push({path,resolve}));
              const {renderChanges}=await import('/gradebook-changes.js');await renderChanges(document.querySelector('#changes'),ctx);
            }""")
            page.locator("#changes summary").click()
            page.wait_for_function("calls.length===1")
            page.locator("[data-gbc-refresh]").click()
            page.wait_for_function("calls.length===2")
            page.evaluate("calls[1].resolve({changes:[change]})")
            page.wait_for_function("calls.length===3")
            page.evaluate("calls[0].resolve({cells:[{item_id:'q',row:'s1:a',after:{status:'scored',points:'1'}}]})")
            expect(page.locator("[data-gbc-cells]")).to_contain_text("Loading 1 score")
            page.evaluate("calls[2].resolve({cells:[{item_id:'q',row:'s1:a',after:{status:'scored',points:'8'}}]})")
            expect(page.locator("[data-gbc-cells] tbody strong")).to_have_text("8")

            # Queued writes keep the original section, and neither their results nor a late refresh replace another section.
            page.goto(address + "#course/g/section/501")
            page.evaluate("""async () => {
              window.posts=[];window.reads=[];window.holdRead=false;
              window.makeView=s=>({term:{id:'t',group_id:'g',code:'TEST'},sections:[{id:'s1',number:501},{id:'s2',number:502}],
                setup:{items:[{id:'q',title:'Quiz',points:'10'}],categories:[],scale:[]},
                students:(s==='s1'?['a','b']:['c']).map(id=>({row:`${s}:${id}`,section_id:s,section:s==='s1'?501:502,name:id})),
                cells:{},results:{students:Object.fromEntries((s==='s1'?['a','b']:['c']).map(id=>[`${s}:${id}`,{percent:'88'}]))},requests:[],links:{},assignments:[]});
              const {createGradebook}=await import('/gradebook.js');
              window.gb=createGradebook({escape:v=>String(v??''),icon:()=>'',toast(){},
                showDialog:html=>{const d=document.querySelector('#dialog');d.innerHTML=html;if(!d.open)d.showModal();},dialogHeader:t=>`<h2>${t}</h2>`,
                api:(path,method,body)=>{
                  if(path==='/api/gradebook')return Promise.resolve({terms:[{id:'t',group_id:'g',sections:[{id:'s1',number:501},{id:'s2',number:502}]}]});
                  if(path.includes('/scores'))return new Promise(resolve=>posts.push({body,resolve}));
                  if(path.includes('/grading-check'))return Promise.resolve({columns:{},section_id:location.hash.endsWith('501')?'s1':'s2'});
                  if(holdRead)return new Promise(resolve=>reads.push({path,resolve}));
                  return Promise.resolve(makeView(path.includes('section=s2')?'s2':'s1'));
                }});await gb.render();
            }""")
            for index, score in enumerate(("3", "4")):
                page.locator("td[data-cell]").nth(index).press("Enter")
                page.locator(".gb-input").fill(score)
                page.locator(".gb-input").press("Enter")
            page.wait_for_function("posts.length===1")
            page.evaluate("async()=>{location.hash='#course/g/section/502';await gb.render();}")
            expect(page.locator("[data-gradebook-grid] tbody tr")).to_have_count(1)
            page.evaluate("posts[0].resolve({cells:{q:{'s1:a':{id:'one',status:'scored',points:'3'}}},results:{students:{'s1:a':{percent:'11'}}},requests:[]})")
            page.wait_for_function("posts.length===2")
            assert page.evaluate("posts.map(p=>p.body.section_id)") == ["s1", "s1"]
            page.evaluate("posts[1].resolve({cells:{q:{'s1:b':{id:'two',status:'scored',points:'4'}}},results:{students:{'s1:b':{percent:'11'}}},requests:[]})")
            expect(page.locator("td.gb-r-overall")).to_have_text("88%")
            page.evaluate("holdRead=true")
            page.locator('[data-gradebook-action="refresh"]').click()
            page.wait_for_function("reads.length===1")
            page.evaluate("async()=>{holdRead=false;location.hash='#course/g/section/501';await gb.render();}")
            expect(page.locator("[data-gradebook-grid] tbody tr")).to_have_count(2)
            page.evaluate("reads[0].resolve(makeView('s2'))")
            expect(page.locator("[data-gradebook-grid] tbody tr")).to_have_count(2)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Gradebook lifecycle regressions passed")


if __name__ == "__main__":
    main()
