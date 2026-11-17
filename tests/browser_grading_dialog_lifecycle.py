"""Grading dialogs keep paired rules and explicit Gradescope previews consistent."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright
from waitress.server import create_server
from test_grading_teams import GradingTeams

fixture = GradingTeams('test_three_distinct_credit_bearing_selections_require_review')
fixture.setUp()
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        review_source = (REPO / 'static/grading-review.js').read_text().replace(
            'import {mountReviewDesk, closeReviewDesk} from "./grading-review-desk.js";',
            'const closeReviewDesk = () => {}; const mountReviewDesk = (work, value, h, options) => { '
            'globalThis.__savedReview = options.onSaved; work.textContent = value.id; return {leave: () => true}; };')
        page.route('**/static/grading-review-sweep.js', lambda r: r.fulfill(
            status=200, content_type='text/javascript', body=review_source))
        page.goto(f'http://127.0.0.1:{server.effective_port}/')
        page.evaluate("""async () => {
          const {bindConcerns, concernActionsMarkup} = await import('/static/grading-review-concerns.js');
          const {createDialog} = await import('/static/grading-gradescope-create.js');
          const assert = (condition, message) => { if (!condition) throw new Error(message); };
          const e = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
          let operation, written;
          const q = {question_id:'q', rubric_id:'r', rubric:{mode:'positive', items:[
            {id:'R1', points:1, description:'First step'}, {id:'R2', points:1, description:'Second step'}]}};
          const kept = {question_id:'q', rubric_id:'r', clarifications:{}, pairs:[['R1','R2']]};
          const status = {state:'confirmed', version:1, context:{questions:[q]}, review:{id:'review', policy:{questions:[kept]}}};
          const h = {e, toast:()=>{}, run:(_, fn) => operation = fn(), api:async (path, method, payload) => {
            if (path.endsWith('/clarify')) { written = payload; return {confirmation:{}}; }
            return path.endsWith('/rubric-links') ? {sets:[]} : status;
          }};
          const host = document.createElement('section');
          document.body.append(host);
          const entry = {question_id:'q'};
          host.innerHTML = concernActionsMarkup(entry, h);
          bindConcerns(host, {id:'plan', questions:[q]}, entry, h, {active:()=>true, certified:false});
          host.querySelector('[data-concern-clarify]').click(); await operation;
          let boxes = [...host.querySelectorAll('[data-concern-pair]')];
          assert(boxes.length === 2 && boxes.every(box => box.checked), 'Both ends show the exclusion');
          boxes[0].click();
          assert(boxes.every(box => !box.checked), 'Removing either end removes the pair');
          host.querySelector('[data-concern-save]').click(); await operation;
          assert(written.questions[0].pairs.length === 0, 'The removed exclusion stays removed');
          kept.pairs = [];
          host.querySelector('[data-concern-clarify]').click(); await operation;
          boxes = [...host.querySelectorAll('[data-concern-pair]')];
          boxes[1].click();
          assert(boxes.every(box => box.checked), 'Adding either end selects the pair');
          host.querySelector('[data-concern-save]').click(); await operation;
          assert(JSON.stringify(written.questions[0].pairs) === '[[' + '"R1","R2"' + ']]', 'The pair is sent once');
          host.remove();

          const dialog = document.createElement('dialog'); document.body.append(dialog);
          let reads = [], creates = 0;
          const preview = title => ({configured:true, ready:true, fingerprint:title, title, courses:[{course:'Course', sections:['1']}], versions:[]});
          const gh = {e, toast:()=>{}, close:()=>dialog.close(), field:(_, name, value) => `<input name="${name}" value="${e(value)}">`,
            check:(label, name) => `<label><input type="checkbox" name="${name}">${e(label)}</label>`,
            dialog:(_, __, body) => { dialog.innerHTML = `<form>${body}<p class="form-error" role="alert" hidden></p><button type="submit">Create</button></form>`; dialog.showModal(); return dialog.firstElementChild; },
            api:async (path, method, payload) => {
              if (path.endsWith('/preview')) return new Promise(resolve => reads.push({title:payload.title, resolve}));
              creates++; return {};
            }};
          const tick = () => new Promise(resolve => setTimeout(resolve, 0));
          const form = createDialog({id:'plan', title:'First'}, gh);
          const title = form.elements.title, submit = form.querySelector('[type="submit"]');
          title.value = 'Second'; title.dispatchEvent(new Event('input'));
          reads[0].resolve(preview('First')); await tick();
          assert(submit.disabled && !form.elements.understood, 'A late preview cannot authorize the edited title');
          title.dispatchEvent(new Event('change')); reads[1].resolve(preview('Second')); await tick();
          assert(!submit.disabled && reads[1].title === 'Second', 'The new title has its own preview');
          form.elements.understood.checked = true;
          title.value = 'Third'; title.dispatchEvent(new Event('input'));
          assert(submit.disabled && !form.elements.understood, 'Typing invalidates the approved preview immediately');
          form.dispatchEvent(new Event('submit', {cancelable:true})); await tick();
          assert(creates === 0, 'Enter cannot create from the previous title preview');
          dialog.close(); dialog.remove();

          // Exercise All responses with a small desk substitute: its saved callback requests a scoped list refresh.
          const {reviewView} = await import('/static/grading-review-sweep.js');
          const reviewHost = document.createElement('section'); document.body.append(reviewHost);
          const rows = [{id:'r1', question_id:'q1', state:'approved'}, {id:'r2', question_id:'q2', state:'approved'}];
          let pending = null;
          const rh = {e, toast:()=>{}, button:(action, text) => `<button data-grading-action="${action}">${e(text)}</button>`,
            select:(label, name, choices, value) => `<label>${e(label)}<select name="${name}">${choices.map(([key,text]) => `<option value="${e(key)}" ${key === value ? 'selected' : ''}>${e(text)}</option>`).join('')}</select></label>`,
            api:async path => {
              if (path.includes('/responses?')) return {responses:rows.filter(row => row.question_id === 'q1')};
              if (path.endsWith('/responses')) return pending ? new Promise(resolve => pending.push(resolve)) : {responses:rows};
              return {id:path.includes('/r2?') ? 'r2' : 'r1', result:null};
            }};
          const plan = {id:'plan', questions:[{question_id:'q1', question_key:'q1', content:{title:'First'}}, {question_id:'q2', question_key:'q2', content:{title:'Second'}}]};
          await reviewView(reviewHost, plan, rh, ()=>{}, ()=>true, {browse:'all', question:'q1'});
          await globalThis.__savedReview('approved');
          pending = [];
          const picker = reviewHost.querySelector('[name="review-question"]');
          picker.value = ''; const first = picker.onchange({target:picker});
          picker.value = 'q2'; const second = picker.onchange({target:picker});
          assert(pending.length === 2, 'Two question changes await their list reads');
          pending[1]({responses:rows}); await second;
          pending[0]({responses:rows}); await first;
          assert(picker.value === 'q2', 'The final question choice remains selected');
          assert(reviewHost.querySelector('[data-review-progress]').textContent.startsWith('1 of 1 final'), 'The response list belongs to the final question choice');
          reviewHost.remove(); delete globalThis.__savedReview;
        }""")
        assert not errors, errors
        browser.close()
        print('Grading lifecycle passed: paired exclusions, title-specific Gradescope authorization and latest question selection.')
finally:
    server.close()
    fixture.doCleanups()
