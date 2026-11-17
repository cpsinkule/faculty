"""FRONTEND / SEATING: delayed writes retain their chart; isolated synthetic fixture only."""
import functools
import http.server
import threading

from playwright.sync_api import expect, sync_playwright

from browser_course_lifecycle import Assets, ROOT
from test_seating import SeatingTests


def main():
    fixture = SeatingTests()
    fixture.setUp()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Assets, directory=str(ROOT / 'static')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        response = fixture.create()
        assert response.status_code == 201, response.json
        base = response.json
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.evaluate("""async base => {
              const {createSeating}=await import('/seating.js');const {escape}=await import('/ui.js');
              document.body.insertAdjacentHTML('beforeend','<section id="seating-section"></section>');
              window.base=base;window.pending=[];window.charts={[base.id]:base,other:{...structuredClone(base),id:'other',title:'Other chart'}};
              window.openDialog=html=>{const d=document.querySelector('#dialog');d.innerHTML=html;if(!d.open)d.showModal();};
              const api=(path,method)=>{
                if(method)return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
                if(path.startsWith('/api/seating/options'))return Promise.resolve({dr:{excluded_students:0,others:[]}});
                if(path.startsWith('/api/exams'))return Promise.resolve({exams:[]});
                const value=charts[decodeURIComponent(path.split('/').pop())];
                if(value)return Promise.resolve(structuredClone(value));
                throw Error('Unexpected read '+path);
              };
              window.seating=createSeating({api,escape,icon:()=>'',toast(){},showDialog:openDialog,
                dialogHeader:title=>`<h2>${title}</h2>`,header:{set(){}},getCharts:()=>Object.values(charts),refresh:async()=>{}});
              location.hash='seating/'+base.id;
            }""", base)
            page.evaluate('seating.render(true)')
            page.locator('[data-seating-action="update"]').click()
            page.wait_for_function('pending.length === 1')
            page.evaluate("location.hash='seating/other'")
            page.evaluate('seating.render(true)')
            page.wait_for_function("document.querySelector('.seating-workflow').textContent.includes('Other') || document.querySelector('#seating-section .seat-grid')")
            page.evaluate('pending[0].resolve({...base,version:base.version+1})')
            page.wait_for_timeout(50)
            assert page.evaluate("document.querySelector('#seating-section .seat-grid').getAttribute('aria-label')") == 'Other chart seating chart'

            page.evaluate("location.hash='seating/'+base.id")
            page.evaluate('seating.render(true)')
            page.get_by_role('button', name='Chart settings', exact=True).click()
            page.locator('#seating-settings [name="notes"]').fill('Retained note')
            page.locator('#seating-settings').evaluate('(form)=>form.requestSubmit()')
            page.wait_for_function('pending.length === 2')
            page.evaluate("openDialog('<h2>Keep this new dialog</h2>');pending[1].resolve({...base,version:base.version+1,notes:'Retained note'})")
            expect(page.locator('#dialog h2')).to_have_text('Keep this new dialog')
            expect(page.locator('#dialog')).to_be_visible()

            page.evaluate("document.querySelector('#dialog').close()")
            search = page.locator('#seating-search')
            search.fill('Example')
            search.focus()
            page.evaluate('charts[base.id].version++;seating.render(true)')
            expect(search).to_have_value('Example')
            expect(search).to_be_focused()
            assert not errors, errors
            browser.close()
            print('Seating lifecycle: delayed update/settings and search retention passed.')
    finally:
        server.shutdown()
        server.server_close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
