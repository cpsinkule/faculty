"""FRONTEND / IDENTITY: course reads and roster actions retain their owners; synthetic data only."""
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import browser_frontend_lifecycle as frontend

ROOT = Path(__file__).resolve().parents[1]


class Assets(frontend.Assets):
    PAGE = b'<!doctype html><section id="courses-section"></section><section id="course-detail"></section><dialog id="dialog"></dialog>'


def main():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Assets, directory=str(ROOT / 'static')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.evaluate("""async () => {
              const {createCourses}=await import('/courses.js');
              const {escape}=await import('/ui.js');
              const group=id=>({id,definition_id:'definition-'+id,name:'Course '+id,code:'MATH 140',term:'Fall',term_other:'',year:2026,
                is_current:true,all_current:true,sections:[501],current_sections:[501],offerings:[{id,name:'Course',sections:[501],is_current:true}]});
              window.state={view:'course',loaded:true,courseId:'one',courseSection:null,courseSub:'overview',courseBackView:'all-courses',
                courseGroups:[group('one'),group('two')],storage:{enabled:false},query:''};
              window.pending=[];window.overviews=[];
              window.roster={group_id:'two',section_id:'section-two',section:501,headers:['Name','UIN','Email'],count:0,dropped_count:1,
                students:[{id:'student',uin:'123456789',fields:{Name:'Example, Alice',UIN:'123456789',Email:'alice@example.test'},
                  dropped:{source:'roster',at:'2026-10-20'}}]};
              window.openDialog=html=>{const d=document.querySelector('#dialog');d.innerHTML=html;if(!d.open)d.showModal();};
              window.api=(path,method)=>{
                if(method==='POST')return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
                if(path==='/api/course-groups/summary')return Promise.resolve({groups:{one:{term_id:'one',sections:{501:{section_id:'section-one',students:1}}},two:{term_id:'two',sections:{501:{section_id:'section-two',students:0}}}}});
                if(path==='/api/canvas/overview')return new Promise(resolve=>overviews.push(resolve));
                if(path.endsWith('/roster'))return Promise.resolve(roster);
                if(path.startsWith('/api/teaching-assistants/'))return Promise.resolve({contacts:[],others:[]});
                if(path.includes('/gradescope/')||path.startsWith('/api/canvas/sections/'))return Promise.resolve({configured:false});
                if(path.startsWith('/api/grading'))return Promise.resolve({assignments:[]});
                if(path.startsWith('/api/exams'))return Promise.resolve({exams:[]});
                if(path.startsWith('/api/state'))return Promise.resolve({seating_charts:[]});
                if(path.startsWith('/api/response-cards/'))return Promise.resolve({issues:[]});
                throw Error('Unexpected read '+path);
              };
              window.courses=createCourses({api,state,escape,icon:()=>'',toast(){},showDialog:openDialog,
                dialogHeader:title=>`<h2>${escape(title)}</h2>`,saveForm(){},courseResources:async()=>{}});
              courses.renderCourseDetail();
            }""")
            page.wait_for_function('overviews.length === 1')
            page.evaluate("state.courseId='two'; courses.renderCourseDetail()")
            page.wait_for_function('overviews.length === 2')
            page.evaluate("overviews[1]({canvas:{configured:true},terms:[]});overviews[0]({canvas:{configured:false},terms:[]})")
            page.wait_for_timeout(50)
            expect(page.locator('[data-canvas-home-row]')).to_have_count(1)

            page.evaluate("state.courseSection=501;state.courseSub='roster';courses.renderCourseDetail()")
            expect(page.locator('[data-student="student"]')).to_have_count(1)
            page.get_by_role('button', name='Dropped', exact=False).click()
            page.locator('[data-roster-student="student"]').click()
            page.locator('[data-student-restore]').click()
            page.wait_for_function('pending.length === 1')
            page.evaluate("openDialog('<h2>Keep this dialog</h2>');pending[0].resolve({changed:1})")
            page.wait_for_timeout(50)
            expect(page.locator('#dialog')).to_be_visible()
            expect(page.locator('#dialog h2')).to_have_text('Keep this dialog')

            page.evaluate("""document.querySelector('#dialog').close();roster.count=1;roster.dropped_count=0;
              delete roster.students[0].dropped;roster.leaving=[{id:'student',name:'Example, Alice',reason:'Q drop'}];
              courses.loadRosterPanel();""")
            page.locator('[data-roster-leaving-drop]').click()
            confirm = page.locator('[data-roster-leaving-yes]')
            confirm.click()
            page.wait_for_function('pending.length === 2')
            page.evaluate("pending[1].reject(Error('Synthetic failure'))")
            expect(confirm).to_be_enabled()
            assert not errors, errors
            browser.close()
            print('Course lifecycle: stale reads, all-dropped roster and delayed/failed actions passed.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
