"""FRONTEND: delayed seating writes keep their original chart and dialog; fake services only."""
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import browser_frontend_lifecycle as frontend

ROOT = Path(__file__).resolve().parents[1]


class Assets(frontend.Assets):
    PAGE = b'<!doctype html><section id="seating-section"></section><dialog id="dialog"></dialog>'


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Assets, directory=str(ROOT / "static")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.evaluate("""async () => {
              const {createSeatingInteractions}=await import('/seating-interactions.js');
              window.pending=[]; window.saved=[]; window.draws=0; window.refreshed=0;
              window.chart={id:'original',version:1,students:[{enrollment_id:'student',name:'Example',status:'unseated',section_number:501}]};
              window.openDialog=html=>{const d=document.querySelector('#dialog');d.innerHTML=html;if(!d.open)d.showModal();};
              window.controller=createSeatingInteractions({
                api:(path,method,body)=>new Promise(resolve=>pending.push({path,method,body,resolve})),
                escape:value=>String(value),toast(){},showDialog:openDialog,dialogHeader:title=>`<h2>${title}</h2>`,
                getChart:()=>chart,setChart:value=>{saved.push(value);chart=value;},chartId:()=>chart.id,
                isVersionView:()=>false,invalidateRender(){},renderDetail:()=>draws++,render(){},
                refresh:()=>{refreshed++;},closeSeatPopover(){}
              });
              controller.openSeatHere({id:'seat',label:'A1'});
              document.querySelector('#seating-fill').requestSubmit();
            }""")
            page.wait_for_function("pending.length === 1")
            assert page.evaluate("pending[0].path") == "/api/seating/original/move"
            page.evaluate("chart={id:'new-chart',version:2};openDialog('<h2>Keep this dialog</h2>');pending[0].resolve({id:'original',version:2})")
            page.wait_for_function("refreshed === 1")
            assert page.evaluate("chart.id") == "new-chart"
            assert page.evaluate("saved.length === 0 && draws === 0")
            expect(page.locator("#dialog")).to_be_visible()
            expect(page.locator("h2")).to_have_text("Keep this dialog")

            page.evaluate("""chart={id:'original',version:2,students:[{enrollment_id:'student',name:'Example',status:'unseated',section_number:501}]};
              controller.openSeatHere({id:'seat',label:'A1'});document.querySelector('#seating-fill').requestSubmit();
              document.querySelector('#dialog').close();pending[1].resolve({id:'original',version:3});""")
            page.wait_for_function("refreshed === 2")
            assert page.evaluate("saved.length === 0 && chart.version === 2")
            expect(page.locator("#dialog")).not_to_be_visible()

            page.evaluate("controller.openSeatHere({id:'seat',label:'A1'});document.querySelector('#seating-fill').requestSubmit();pending[2].resolve({id:'original',version:3})")
            page.wait_for_function("refreshed === 3")
            assert page.evaluate("saved.length === 1 && chart.version === 3 && draws === 1")
            expect(page.locator("#dialog")).not_to_be_visible()

            # A saved-login lookup that completes after another dialog opened must not start a browser.
            page.evaluate("""async () => {
              const {openCanvasSignIn}=await import('/aim-signin.js');
              window.pending=[];window.logins=[];
              window.loginOptions={api:(path,method,body)=>new Promise(resolve=>pending.push({path,method,body,resolve})),
                showDialog:openDialog,dialogHeader:title=>`<h2>${title}</h2>`,escape:value=>String(value)};
              window.startLogin=()=>{void openCanvasSignIn(loginOptions).then(value=>logins.push(value));};
              startLogin();
            }""")
            page.wait_for_function("pending.length === 1")
            page.evaluate("openDialog('<h2>Keep this replacement</h2>');pending[0].resolve({saved:true,netid:'test'})")
            page.wait_for_function("logins.length === 1")
            assert page.evaluate("logins[0] === null && pending.length === 1")
            expect(page.locator("h2")).to_have_text("Keep this replacement")

            # Cancel while the server is starting: close the session once its ID arrives, leaving newer UI alone.
            page.evaluate("startLogin();pending[1].resolve({saved:true,netid:'test'})")
            page.wait_for_function("pending.length === 3")
            page.evaluate("document.querySelector('#dialog').close();openDialog('<h2>Keep after cancellation</h2>');pending[2].resolve({id:'late',state:'done',user:'test'})")
            page.wait_for_function("pending.length === 4 && logins.length === 2")
            assert page.evaluate("pending[3].path.endsWith('/late/close') && logins[1] === null")
            expect(page.locator("#dialog")).to_be_visible()
            expect(page.locator("h2")).to_have_text("Keep after cancellation")
            page.evaluate("pending[3].resolve({})")

            # A transient open/close must invalidate a read even when the retained contents are unchanged.
            assert page.evaluate("""async () => {
              const {captureDialogIntent}=await import('/ui.js');
              const dialog=document.querySelector('#dialog');dialog.close();
              const current=captureDialogIntent(dialog);dialog.showModal();dialog.close();return !current();
            }""")

            # Term saves belong to the term that started them; clean refreshes accept the latest saved revision.
            page.evaluate("""async () => {
              const {createTermSchedule}=await import('/term-schedule.js');
              document.body.insertAdjacentHTML('beforeend','<section data-term-schedule-host></section>');
              window.pending=[];window.termHeader=null;
              window.termViews={one:{term_id:'one',revision:0,first_day:'2026-01-01',last_day:'2026-05-01',entries:[],days_off:[],sections:[]},
                two:{term_id:'two',revision:0,first_day:'2026-02-01',last_day:'2026-06-01',entries:[],days_off:[],sections:[]}};
              window.termController=createTermSchedule({api:(path,method)=>method==='POST'?new Promise(resolve=>pending.push({path,resolve})):
                Promise.resolve(path.includes('/office-hours')?{available:false}:structuredClone(termViews[path.split('/').at(-1)])),
                escape:value=>String(value),icon:()=>'',toast(){},header:{set:value=>termHeader=value},showDialog:openDialog,dialogHeader:title=>title});
              location.hash='#course/one/home/schedule';await termController.render({id:'one',code:'ONE'});
              const first=document.querySelector('[data-term="first_day"]');first.value='2026-01-02';first.dispatchEvent(new Event('input',{bubbles:true}));
              termHeader.action.run();
              location.hash='#course/two/home/schedule';await termController.render({id:'two',code:'TWO'});
              pending[0].resolve({...termViews.one,revision:1,first_day:'2026-01-02'});
            }""")
            page.wait_for_timeout(50)
            assert page.locator('[data-term="first_day"]').input_value() == "2026-02-01"
            page.evaluate("termViews.two={...termViews.two,first_day:'2026-02-02'};termController.render({id:'two',code:'TWO'},'',true)")
            expect(page.locator('[data-term="first_day"]')).to_have_value("2026-02-02")

            # An exam-day save may finish after the instructor has typed more instructions.
            page.evaluate("""async () => {
              const {createExamDay}=await import('/exam-day.js');
              document.body.innerHTML='<section id="exam-day-section"></section><dialog id="dialog"></dialog>';
              location.hash='#exam-day/assignment/term';window.pending=[];
              window.examDayView={assignment:{title:'Exam',code:'MATH'},term:{term_label:'Fall'},revision:0,
                instructions:'Original',default_instructions:'Default',rooms:[],others:[],exam_href:'#exams/exam'};
              window.dayController=createExamDay({api:(path,method,body)=>method==='POST'?new Promise(resolve=>pending.push({body,resolve})):Promise.resolve(structuredClone(examDayView)),
                escape:value=>String(value),icon:()=>'',toast(){}});
              await dayController.render();
            }""")
            page.locator("[data-instructions]").fill("Sent instructions")
            page.locator("[data-save]").click()
            page.wait_for_function("pending.length === 1")
            page.locator("[data-instructions]").fill("Typed while saving")
            page.evaluate("pending[0].resolve({...examDayView,revision:1,instructions:pending[0].body.instructions,saved_at:'2026-10-24T10:00:00Z'})")
            expect(page.locator("[data-save]")).to_be_enabled()
            expect(page.locator("[data-instructions]")).to_have_value("Typed while saving")

            # Class topics typed during Save stay editable against the newly saved revision.
            page.evaluate("""async () => {
              const {createClassSchedule}=await import('/class-schedule.js');
              document.body.innerHTML='<section data-class-schedule-host></section><dialog id="dialog"></dialog>';
              location.hash='#course/one/section/501/schedule';window.pending=[];
              window.classView={definition_id:'one',term_id:'one',section_id:'section',today:'2026-10-26',revision:0,
                term_schedule:{revision:1,first_day:'2026-10-26',last_day:'2026-12-01'},sections:[{id:'section',number:501}],
                plan:{items:[],open:[],topics:{}},differs:[],unclear:[],undated:[],
                days:[{date:'2026-10-26',meetings:[{time:'08:00',end_time:'08:50'}],topics:{notes:[],note:''},work:[]}]};
              window.classController=createClassSchedule({api:(path,method,body)=>method==='POST'?new Promise(resolve=>pending.push({body,resolve})):
                Promise.resolve(path.includes('/course-notes/')?{current:{parts:[]}}:structuredClone(classView)),
                escape:value=>String(value),toast(){},header:{set:value=>window.classHeader=value}});
              await classController.render({id:'one',code:'ONE'},'Fall',501);classHeader.secondary.find(action=>action.label==='Edit topics').run();
            }""")
            page.locator('[data-topic="2026-10-26"]').fill("2.1")
            page.evaluate("classHeader.action.run()")
            page.wait_for_function("pending.length === 1")
            page.locator('[data-topic="2026-10-26"]').fill("2.2")
            page.evaluate("classView.revision=1;classView.plan.topics=pending[0].body.topics;pending[0].resolve({revision:1})")
            page.wait_for_function("classHeader.action?.label === 'Save topics'")
            expect(page.locator('[data-topic="2026-10-26"]')).to_have_value("2.2")

            # A two-part policy save keeps its original term even if the term selector changes between requests.
            page.evaluate("""async () => {
              const {createStudentSupport}=await import('/student-support.js');
              document.body.innerHTML='<section id="student-support-section"></section><dialog id="dialog"></dialog>';
              location.hash='#follow-ups';window.pending=[];
              const term=(id,guidance)=>({term_id:id,code:id,term_label:'Fall',revision:1,updated_at:'2026-10-01',
                areas:{additional:{mode:'guidance',guidance}},meeting_hours:{revision:0,windows:[],makeup_rooms:[]}});
              window.policyData={terms:[term('one','One'),term('two','Two')],office_rooms:[],
                areas:[{id:'additional',label:'Additional',group:'Other',prompt:'Guidance'},{id:'appointments',label:'Appointments',group:'Other',prompt:'Times'}],
                modes:[{id:'unset',label:'Unset'},{id:'guidance',label:'Guidance'}]};
              window.policyController=createStudentSupport({api:(path,method,body)=>{
                if(method==='POST')return new Promise(resolve=>pending.push({path,body,resolve}));
                if(path.includes('/policies?'))return Promise.resolve(structuredClone(policyData));
                if(path.startsWith('/api/calendar'))return Promise.resolve({events:[]});
                return Promise.resolve({items:[],suggestion_settings:{paused:false},capture:{}});
              },escape:value=>String(value),toast(){},showDialog:openDialog,dialogHeader:title=>`<h2>${title}</h2>`});
              await policyController.render();document.querySelector('[data-support-policies]').click();
            }""")
            page.locator('[name="policy_guidance"]').fill("One revised")
            page.locator('[data-policy-area="meeting_hours"]').click()
            page.locator('[data-meeting-add]').click()
            page.locator('#support-policies').evaluate("form=>form.requestSubmit()")
            page.wait_for_function("pending.length === 1")
            page.locator('[name="term_id"]').select_option("two")
            page.evaluate("pending[0].resolve({...pending[0].body,revision:1})")
            page.wait_for_function("pending.length === 2")
            assert page.evaluate("pending[1].path.endsWith('/one') && pending[1].body.areas.additional.guidance === 'One revised'")
            page.evaluate("pending[1].resolve({areas:pending[1].body.areas,revision:2,content:'One revised',updated_at:'2026-10-24'})")
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Integration dialog, login, schedule and exam-day lifecycle checks passed.")


if __name__ == "__main__":
    main()
