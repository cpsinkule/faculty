"""End-to-end Inbox forms, queue, review, codes, audit, downloads, and the desktop layout (no horizontal overflow at 1280 px)."""
import io
import json
import re
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from rosters import connect
from browser_course_pages import seed_course
from test_rosters import csv_bytes


# Lets a page be hidden and shown (document.hidden, visibilitychange) and counts the document's visibilitychange listeners.
HIDDEN = """(() => {
  let hidden = false;
  const live = window.__visibilityListeners = new Set();
  Object.defineProperty(Document.prototype, 'hidden', {configurable: true, get: () => hidden});
  Object.defineProperty(Document.prototype, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
  const add = EventTarget.prototype.addEventListener, remove = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.addEventListener = function (type, listener, options) {
    if (this === document && type === 'visibilitychange') live.add(listener);
    return add.call(this, type, listener, options);
  };
  EventTarget.prototype.removeEventListener = function (type, listener, options) {
    if (this === document && type === 'visibilitychange') live.delete(listener);
    return remove.call(this, type, listener, options);
  };
  window.__setHidden = (value) => { hidden = value; document.dispatchEvent(new Event('visibilitychange')); };
})();"""


def fits(page, what, height=1000):
    """The page fits a 1280 px window (DESIGN-LANGUAGE: desktop only, no phone layout): nothing runs off to the right. The window
    goes back to 1440 px afterwards."""
    page.set_viewport_size({'width': 1280, 'height': height})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{what} overflow at 1280'
    page.set_viewport_size({'width': 1440, 'height': 1000})


def nudge(page):
    """The instructor coming back to the window: the Inbox reads its page at once (an idle list otherwise waits up to 15 s, History
    a minute). The same read as the timed poll."""
    page.evaluate("window.dispatchEvent(new Event('focus'))")


# Counts the Inbox list reads the page has asked for and the ones it has finished with. `done` moves one task after an answer's
# body was read: by then the page has done everything it does with the answer (drawn it, set its next poll).
LIST_READS = """(() => {
  const count = window.__listReads = {asked: 0, done: 0};
  const turn = new MessageChannel();
  turn.port1.onmessage = () => { count.done += 1; };
  const finished = () => turn.port2.postMessage(0);
  const real = window.fetch;
  window.fetch = function (input, init) {
    if (!/\\/api\\/inbox\\/jobs\\?/.test(String(input?.url ?? input))) return real.call(this, input, init);
    count.asked += 1;
    return real.call(this, input, init).then((response) => {
      const json = response.json;
      response.json = function () { return json.call(this).finally(finished); };
      return response;
    }, (error) => { finished(); throw error; });
  };
})();"""


def frozen_page(browser, errors, width):
    """A page whose clock stands still from before it loads: its timers run only when the test moves time on, so how fast the
    machine is changes nothing about when they run. The clock is set and stopped on a browser context that has no page yet, where
    no time can pass between the two; stopping a loaded page's clock a fixed 50 ms ahead of it lost to real time on a busy machine
    (Playwright: "Cannot fast-forward to the past")."""
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    now = datetime.now(timezone.utc)
    context.clock.install(time=now)
    context.clock.pause_at(now)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    return page


def check_poll_cadence(browser, address, errors):
    """The Inbox list reads every 4 s while anything listed is being worked on, every 15 s when Current is idle and every minute on
    History; showing the tab again reads at once, and the shell's own redraws read only when the page's poll would by now."""
    probe = frozen_page(browser, errors, 1440)
    probe.add_init_script(LIST_READS)
    lists = re.compile(r'/api/inbox/jobs\?')
    answer = {'running': 0}
    def answered(route):
        counts = {'running': answer['running']} if answer['running'] else {}
        route.fulfill(json={'jobs': [], 'counts': counts, 'kind_counts': {'scan': counts, 'command': {}}, 'assignments': [], 'total': 0,
                            'has_more': False, 'offset': 0, 'worker_heartbeat': None, 'email_configured': False})
    probe.route(lists, answered)
    reads = []
    probe.on('request', lambda request: reads.append(request.url) if lists.search(request.url) else None)

    def quiet(done):
        """Wait, however long the machine takes, until the page has finished its list read number `done` and has none under
        way; then a little longer, for a read it should not make."""
        deadline = time.monotonic() + 30
        while True:
            count = probe.evaluate('window.__listReads')
            if count['done'] >= done and count['asked'] == count['done']: break
            assert time.monotonic() < deadline, (f'the page has not finished list read {done}', count, reads)
            probe.wait_for_timeout(50)
        probe.wait_for_timeout(800)

    @contextmanager
    def reading(expected):
        """What happens inside makes the page read its list exactly `expected` times."""
        before = len(reads)
        done = probe.evaluate('window.__listReads.done')
        yield
        quiet(done + expected)
        assert len(reads) - before == expected, reads
    probe.goto(address + '/#inbox')
    expect(probe.locator('.inbox-empty')).to_be_visible()
    quiet(1)
    with reading(0): probe.clock.run_for(13000)  # an idle Current waits 15 s
    with reading(1): probe.clock.run_for(3000)
    with reading(1): probe.evaluate("document.dispatchEvent(new Event('visibilitychange'))")  # the tab shown again
    answer['running'] = 1
    with reading(1): probe.evaluate("window.dispatchEvent(new Event('focus'))")  # coming back to the window
    with reading(1): probe.clock.run_for(4500)  # something listed is being worked on: every 4 s
    answer['running'] = 0
    with reading(1):
        probe.evaluate("location.hash = '#inbox?status=history'")
        expect(probe.get_by_role('button', name=re.compile(r'^History'))).to_have_attribute('aria-pressed', 'true')
    # History is read once a minute, the shell's 30 s state poll in between included.
    with reading(0): probe.clock.run_for(31000)
    with reading(1): probe.clock.run_for(31000)
    # Coming back from another section is coming back too: History is read at once, well inside its minute, and only once.
    probe.evaluate("location.hash = '#overview'")
    expect(probe.locator('#inbox-section')).to_be_hidden()
    probe.wait_for_timeout(500)
    with reading(1):
        probe.evaluate("location.hash = '#inbox?status=history'")
        expect(probe.locator('#inbox-section')).to_be_visible()
    # A link that opens Upload (#inbox?upload=1) reads the list once, as its plain address would.
    probe.evaluate("location.hash = '#overview'")
    expect(probe.locator('#inbox-section')).to_be_hidden()
    probe.wait_for_timeout(500)
    with reading(1):
        probe.evaluate("location.hash = '#inbox?status=history&upload=1'")
        expect(probe.get_by_role('dialog')).to_contain_text('Upload scanned assignment')
        expect(probe).to_have_url(re.compile(r'#inbox\?status=history$'))
    probe.get_by_role('dialog').get_by_role('button', name='Close', exact=True).click()
    probe.context.close()


def check_missing_scan(browser, address, errors):
    """A link to a scan the server no longer has says so, with the way back, and is not read again until the route changes."""
    probe = frozen_page(browser, errors, 1280)
    missing = '0123abcd-0000-4000-8000-000000000000'
    reads = []
    probe.on('request', lambda request: reads.append(request.url) if request.url.endswith(f'/api/inbox/jobs/{missing}') else None)
    probe.goto(address + f'/#inbox/job/{missing}')
    # Nothing on this page said what the link was, so it is named as any submission (a scan the list showed says "scan").
    expect(probe.locator('#inbox-section')).to_contain_text('This submission is no longer in the Inbox')
    expect(probe.locator('#inbox-section').get_by_role('link', name='Back to Teaching Inbox')).to_have_attribute('href', '#inbox')
    probe.wait_for_timeout(1500)
    seen = len(reads)
    assert 1 <= seen <= 2, reads
    probe.clock.run_for(40000)  # ten of the scan page's polls and the shell's state poll
    probe.wait_for_timeout(1000)
    assert len(reads) == seen, reads
    assert probe.evaluate('document.documentElement.scrollWidth <= innerWidth')
    probe.locator('#inbox-section').get_by_role('link', name='Back to Teaching Inbox').click()
    expect(probe.locator('[data-inbox-list-page]')).to_be_visible()
    probe.context.close()


def check_scan_groups(page, address):
    """Scans of one assignment are one row in History: its counts and state chips, open while one needs you, the instructor's
    open or closed kept through polls, and each scan's own card inside. The rows are the list's own, given an assignment here."""
    history = re.compile(r'/api/inbox/jobs\?.*status=history')
    calls = {'n': 0}
    fall = {'code': 'MATH 140', 'term': 'Fall 2026'}

    def grouped(route):
        if calls.get('fail'):
            return route.fulfill(status=503, json={'error': 'Synthetic restart'})
        # The synthetic assignment is the mock's own: the server is asked for the unfiltered list it is drawn over.
        data = route.fetch(url=re.sub(r'&?assignment=[^&]*', '', route.request.url)).json()
        base = next(job for job in data['jobs'] if job.get('scan'))
        calls.setdefault('sample', json.loads(json.dumps(base)))
        twin = json.loads(json.dumps(base))
        twin['id'] = '00000000' + base['id'][8:]
        linked = {'plan_id': 'plan-1', 'title': 'Exam 1', 'assignment_id': 'assignment-1', 'term_id': 'term-1', **fall}
        base['scan'].update(assignment=linked, pages=120, papers=10)
        twin['scan'].update(assignment=linked, pages=36 + calls['n'], papers=3, state='check')
        calls['n'] += 1
        data['jobs'].insert(data['jobs'].index(base) + 1, twin)
        data['assignments'] = [{**linked, 'scans': 2}]
        calls.setdefault('urls', []).append(route.request.url)
        if calls.get('other_term'):
            other = json.loads(json.dumps(base))
            other['id'] = '11111111' + base['id'][8:]
            other['scan'].update(assignment={'plan_id': 'plan-2', 'title': 'Exam 1', 'code': 'MATH 140', 'term': 'Spring 2026'}, pages=40, papers=4)
            data['jobs'].append(other)
        route.fulfill(json=data)
    page.route(history, grouped)
    page.goto(address + '/#inbox?status=history')
    group = page.locator('.inbox-scan-group')
    expect(group).to_have_count(1)
    summary = group.locator('> summary')
    expect(summary).to_contain_text('Exam 1 · 2 scans')
    expect(summary).to_contain_text('156 pages · 13 papers')
    expect(summary).not_to_contain_text('Fall 2026')  # the only "Exam 1" listed: its term goes without saying
    expect(summary.locator('.chip')).to_have_text(['1 needs you', '1 nothing to do'])
    assert group.evaluate('node => node.open')  # one of them needs you
    expect(group.locator('.inbox-scan-tile [data-scan-card]')).to_have_count(2)
    # The header names the assignment and opens its Collect (I3, 2026-10-18); the rest of the row opens and closes the group.
    expect(summary.locator('[data-inbox-group-link]')).to_have_attribute('href', '#assignments/assignment-1/term-1?area=collect')
    expect(summary.locator('[data-inbox-group-link]')).to_have_text('Exam 1 · 2 scans')
    fits(page, 'Grouped scans')
    summary.locator('.inbox-job-icon').click()
    assert not group.evaluate('node => node.open')
    nudge(page)
    expect(summary).to_contain_text('157 pages', timeout=10000)  # redrawn from a newer read...
    assert not group.evaluate('node => node.open')  # ...and still closed, its cards not drawn
    expect(group.locator('.inbox-scan-tile')).to_have_count(0)
    # Opening it while a read has just failed draws its cards from the last loaded list and keeps the notice that says so.
    calls['fail'] = True
    nudge(page)
    stale = page.locator('#inbox-section > .inbox-stale')
    expect(stale).to_contain_text("Couldn't refresh. Showing the last loaded results.", timeout=10000)
    summary.locator('.inbox-job-icon').click()
    expect(group.locator('.inbox-scan-tile [data-scan-card]')).to_have_count(2)
    expect(stale).to_have_count(1)
    calls['fail'] = False
    # Two terms' "Exam 1" say whose term each is.
    calls['other_term'] = True
    nudge(page)
    expect(stale).to_have_count(0, timeout=10000)
    expect(group).to_have_count(2)
    expect(group.nth(0).locator('> summary')).to_contain_text('MATH 140 · Fall 2026 · ')
    expect(group.nth(1).locator('> summary')).to_contain_text('Exam 1 · 1 scan')
    expect(group.nth(1).locator('> summary')).to_contain_text('MATH 140 · Spring 2026 · 40 pages · 4 papers')
    assert group.nth(0).evaluate('node => node.open')  # the instructor opened it
    # History asks for scans only, and one assignment's scans once it is chosen (a view of the same list).
    assert all('kind=scan' in url for url in calls['urls']), calls['urls']
    expect(page.locator('[data-inbox-needs]')).to_have_count(0)  # Needs you is Current's
    picker = page.locator('[data-inbox-assignment]')
    expect(picker.locator('option')).to_have_text(['All assignments', 'Exam 1 (2)'])
    picker.select_option('plan-1')
    expect(page.locator('#inbox-search-summary')).to_contain_text('Showing 1–')
    expect(picker).to_have_value('plan-1')
    assert 'assignment=plan-1' in calls['urls'][-1], calls['urls'][-1]
    picker.select_option('')
    expect(page.locator('#inbox-search-summary')).to_be_hidden()
    assert 'assignment=' not in calls['urls'][-1], calls['urls'][-1]
    page.unroute(history, grouped)

    # In Current, a group first drawn while its scans are with the worker is closed; once one of them needs you it opens by itself.
    current = re.compile(r'/api/inbox/jobs\?.*status=current')
    second = {'state': 'wait'}

    def working(route):
        data = route.fetch().json()
        for prefix, state in (('aaaaaaaa', 'wait'), ('bbbbbbbb', second['state'])):
            twin = json.loads(json.dumps(calls['sample']))
            twin['id'] = prefix + twin['id'][8:]
            twin['scan'].update(assignment={'plan_id': 'plan-3', 'title': 'Exam 2', **fall}, pages=60, papers=5, state=state)
            data['jobs'].insert(0, twin)
        route.fulfill(json=data)
    asked = []
    page.on('request', lambda request: asked.append(request.url) if current.search(request.url) else None)
    page.route(current, working)
    page.goto(address + '/#inbox')
    working_group = page.locator('.inbox-scan-group')
    expect(working_group).to_have_count(1)
    expect(working_group.locator('> summary')).to_contain_text('Exam 2 · 2 scans')
    expect(working_group.locator('> summary .chip')).to_have_text(['2 working'])
    assert not working_group.evaluate('node => node.open')
    expect(working_group.locator('.inbox-scan-tile')).to_have_count(0)
    second['state'] = 'todo'
    nudge(page)
    expect(working_group.locator('> summary .chip')).to_have_text(['1 needs you', '1 working'], timeout=10000)
    expect(working_group).to_have_js_property('open', True)
    expect(working_group.locator('.inbox-scan-tile [data-scan-card]')).to_have_count(2)
    page.screenshot(path='test-results/inbox-current-group-opened.png', full_page=True)
    # Needs you asks for only what waits for the instructor; All scans goes back.
    page.locator('[data-inbox-needs="needs"]').click()
    expect(page.locator('[data-inbox-needs="needs"]')).to_have_attribute('aria-pressed', 'true')
    assert 'needs=1' in asked[-1], asked[-1]
    page.locator('[data-inbox-needs="all"]').click()
    expect(page.locator('[data-inbox-needs="all"]')).to_have_attribute('aria-pressed', 'true')
    assert 'needs=1' not in asked[-1], asked[-1]
    page.unroute(current, working)


def check_worker_notice(page, address, inbox):
    """A scan waiting on a worker that stopped checking in says so on its own page, as the list does."""
    with connect(inbox.path) as db:
        db.execute("INSERT INTO inbox_worker VALUES (1,?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at", ('2026-01-01T00:00:00+00:00',))
    buffer = io.BytesIO()
    Image.new('L', (850, 1100), 150).save(buffer, format='PDF', resolution=100)
    waiting = inbox.submit({'action': 'scan.receive'}, [{'filename': 'Section 502.pdf', 'content': buffer.getvalue()}], source='web',
                           sender='workspace', idempotency_key='waiting-for-worker', instructor_review_required=True, large_scans=True)
    page.goto(address + f"/#inbox/job/{waiting['id']}")
    notice = page.locator('[data-inbox-worker-slot] .inbox-worker-notice')
    expect(notice).to_contain_text('The Inbox worker has not checked in recently', timeout=15000)
    expect(notice).to_contain_text('This scan waits until it responds.')
    expect(notice.get_by_role('link', name='Workspace health')).to_have_attribute('href', '#overview?operations')
    fits(page, 'Worker notice')
    page.screenshot(path='test-results/inbox-worker-notice.png', full_page=True)


def more(page, item):
    """The list's More menu holds New submission, Use a submission code and Codes & API access (UX overhaul 2026-09-26)."""
    page.locator('.inbox-toolbar').get_by_role('button', name='More', exact=True).click()
    page.get_by_role('menuitem', name=item).click()


def check_search(page, inbox):
    search_form = page.get_by_role('search', name='Search Teaching Inbox')
    search = search_form.get_by_label('Search submissions', exact=True)
    rows = page.locator('.inbox-job-row')

    # Filenames make matches understandable, and Enter submits the search.
    search.fill('quiz7.pdf')
    search.press('Enter')
    expect(rows).to_have_count(1)
    expect(rows).to_contain_text('quiz7.pdf')
    expect(page.get_by_role('button', name='Submissions 1', exact=True)).to_have_attribute('aria-pressed', 'true')
    rows.click()
    expect(page.locator('.inbox-detail-heading')).to_be_visible()
    page.get_by_role('link', name='Back to Teaching Inbox', exact=True).click()
    expect(search).to_have_value('quiz7.pdf')
    expect(rows).to_have_count(1)

    # Status counts describe the search, rather than unrelated submissions.
    page.get_by_role('button', name='Current 0', exact=True).click()
    expect(rows).to_have_count(0)
    expect(page.get_by_role('button', name='Submissions 1', exact=True)).to_be_visible()
    expect(search).to_have_value('quiz7.pdf')
    page.get_by_role('button', name='Submissions 1', exact=True).click()
    expect(rows).to_have_count(1)
    search.fill('math 140')
    search_form.get_by_role('button', name='Search', exact=True).click()
    expect(rows).to_have_count(3)

    # A no-match query is displayed as text, including HTML-like input.
    malicious_query = '<img src=x onerror="window.inboxSearchInjection=true">'
    search.fill(malicious_query)
    search.press('Enter')
    expect(rows).to_have_count(0)
    expect(search).to_have_value(malicious_query)
    expect(page.locator('#inbox-search-summary')).to_be_visible()
    expect(page.locator('#inbox-section img')).to_have_count(0)
    assert not page.evaluate('Boolean(window.inboxSearchInjection)')
    page.get_by_role('button', name='Clear search', exact=True).click()
    expect(search).to_have_value('')
    expect(rows).to_have_count(3)

    # A later submission changes the periodic response while the instructor
    # has an unsubmitted draft. Updating that job must preserve draft and caret. Submissions lists what is unfinished first.
    filename = '<img src=x onerror=window.inboxFilenameInjection=true>.txt'
    term = next(target for target in inbox.catalog()['targets'] if target['level'] == 'term')
    waiting = inbox.submit(
        {'action': 'quiz.ingest', 'target': {'level': 'term', 'id': term['id']}, 'metadata': {}},
        [{'filename': filename, 'content': b'Retained browser fixture'}],
        source='api',
    )
    nudge(page)
    expect(rows).to_have_count(4, timeout=10000)
    expect(rows.first).to_contain_text(filename)
    expect(page.locator('#inbox-search-summary')).to_have_text('1 needs you · 0 working · 0 failed')
    expect(page.locator('#inbox-section img')).to_have_count(0)
    assert not page.evaluate('Boolean(window.inboxFilenameInjection)')
    search.fill('unfinished draft')
    search.focus()
    search.evaluate('(input) => input.setSelectionRange(3, 8)')
    inbox.transition(waiting['id'], 'cancel', waiting['version'])
    nudge(page)
    expect(rows.filter(has_text=filename).locator('.inbox-badge')).to_have_text('Cancelled', timeout=10000)
    expect(page.locator('#inbox-search-summary')).to_have_text('0 need you · 0 working · 0 failed')
    expect(rows).to_have_count(4)
    expect(search).to_have_value('unfinished draft')
    expect(search).to_be_focused()
    assert search.evaluate('(input) => [input.selectionStart, input.selectionEnd]') == [3, 8]
    expect(page.get_by_role('button', name='Submissions 4', exact=True)).to_have_attribute('aria-pressed', 'true')

    search.fill(filename)
    search_form.get_by_role('button', name='Search', exact=True).click()
    expect(rows).to_have_count(1)
    expect(rows).to_contain_text(filename)
    expect(page.get_by_role('button', name='Submissions 1', exact=True)).to_be_visible()
    fits(page, 'Search')
    page.get_by_role('button', name='Clear search', exact=True).click()
    expect(rows).to_have_count(4)


def main():
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(directory)
        seed_course(app.test_client(),sections=[501,502])
        inbox=app.extensions['inbox']
        stop=threading.Event()
        worker=threading.Thread(target=inbox.worker,args=(stop,),daemon=True)
        worker.start()
        server=create_server(app,host='127.0.0.1',port=0)
        threading.Thread(target=server.run,daemon=True).start()
        address=f'http://127.0.0.1:{server.effective_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(address+'/#inbox')
                expect(page.locator('#page-title')).to_have_text('Teaching Inbox')
                expect(page.locator('#inbox-section')).to_contain_text('Nothing needs you right now')
                expect(page.get_by_role('button',name='Current 0',exact=True)).to_have_attribute('aria-pressed','true')
                # One Current list replaces the Needs review, Queued, Running and Failed tabs; Current and History list scans, and every
                # other submission has its own Submissions tab (INBOX "SCANS AND SUBMISSIONS", 2026-10-18).
                expect(page.locator('.inbox-filters button')).to_have_text([re.compile(r'^Current'), re.compile(r'^History'), re.compile(r'^Submissions')])
                # The scan filters: all scans or only those that need you (no assignment choice before any scan is filed under one).
                expect(page.locator('[data-inbox-needs]')).to_have_text(['All scans', 'Needs you'])
                expect(page.locator('[data-inbox-needs="all"]')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('[data-inbox-assignment]')).to_have_count(0)
                expect(page.locator('[data-inbox-status=""]')).to_have_count(0)
                # One toolbar replaces the intro card: the views, search (the '/' target), the worker's dot and More.
                expect(page.locator('.inbox-overview')).to_have_count(0)
                expect(page.locator('#inbox-search')).to_have_attribute('data-page-search', '')
                expect(page.locator('[data-inbox-worker]')).to_have_count(1)
                expect(page.locator('#inbox-search-summary')).to_have_text('0 need you · 0 working · 0 failed')
                more(page, 'New submission…')
                # What can run comes first and Archive documents is the default; the Accommodations imports and what is not built
                # yet sit in their own groups; scans have their own upload, so they are not an action here.
                action = page.get_by_label('Action',exact=True)
                expect(action).to_have_value('document.archive')
                assert action.locator('optgroup').evaluate_all('groups => groups.map(group => group.label)') == ['Use the Accommodations page', 'Not built yet (kept for your review)']
                expect(action.locator('optgroup[label="Use the Accommodations page"] option')).to_have_count(3)  # with AIM form capture
                expect(action.locator('option[value="scan.receive"]')).to_have_count(0)
                expect(page.get_by_role('dialog').get_by_role('button',name='Upload scanned assignment',exact=True)).to_be_visible()
                action.select_option('roster.sync')
                page.get_by_label('Course destination',exact=True).select_option(label='MATH 140 · Fall 2026 · Section 501')
                page.get_by_label('Attachments',exact=True).set_input_files({'name':'roster.csv','mimeType':'text/csv','buffer':csv_bytes()})
                page.get_by_role('button',name='Queue submission',exact=True).click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text('Done',timeout=15000)
                expect(page.locator('.inbox-job-result')).to_contain_text('2 added, 0 updated')
                # Job ID, attempts, storage, the audit history and the raw command wait behind one Technical details fold.
                technical = page.locator('[data-inbox-technical]')
                assert not technical.evaluate('node => node.open')
                expect(page.locator('.inbox-audit li')).to_have_count(0)
                technical.locator('> summary').click()
                expect(technical.locator('.inbox-facts')).to_contain_text('Job ID')
                expect(technical.locator('.inbox-facts')).to_contain_text('Storage')
                expect(technical.locator('.inbox-command-details')).to_have_count(1)
                expect(page.locator('.inbox-audit')).to_contain_text('Worker claimed this job')
                first=page.url
                page.locator('[data-inbox-file-group="original"] > summary').click()
                # A job's file opens in the app's file viewer (VIEWER, a CSV as a table); its Download saves the file itself.
                page.get_by_role('link',name='roster.csv',exact=False).click()
                viewer=page.locator('dialog.file-viewer')
                expect(viewer.locator('.file-viewer-table')).to_be_visible()
                with page.expect_download() as capture:
                    viewer.get_by_role('link',name='Download').click()
                viewer.get_by_role('button',name='Close the viewer').click()
                download=capture.value
                assert Path(download.path()).read_bytes()==csv_bytes()
                page.get_by_role('link',name='Open section roster',exact=False).click()
                expect(page.locator('.private-student-id')).to_have_count(2)
                page.locator('nav').get_by_role('link',name='Teaching Inbox',exact=True).click()
                expect(page.locator('.inbox-job-row')).to_have_count(0)
                # A roster sync is not a scan: History stays empty and it is under Submissions.
                expect(page.get_by_role('button',name='History 0',exact=True)).to_be_visible()
                page.get_by_role('button',name='Submissions 1',exact=True).click()
                expect(page.locator('.inbox-job-row')).to_have_count(1)
                expect(page.locator('.inbox-job-row .inbox-badge')).to_have_text('Done')
                expect(page.locator('[data-inbox-needs]')).to_have_count(0)
                page.locator('nav').get_by_role('link',name='Teaching Inbox',exact=True).click()
                expect(page.get_by_role('button',name='Current 0',exact=True)).to_have_attribute('aria-pressed','true')
                expect(page.locator('.inbox-job-row')).to_have_count(0)
                more(page, 'New submission…')
                page.get_by_label('Action',exact=True).select_option('quiz.ingest')
                page.get_by_label('Course destination',exact=True).select_option(label='MATH 140 · Fall 2026')
                page.get_by_text('Advanced',exact=True).click()
                page.get_by_label('Metadata (JSON or YAML)',exact=True).fill('quiz: 7\nversion: B\npublish: false')
                page.get_by_label('Attachments',exact=True).set_input_files({'name':'quiz7.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4\nSynthetic browser test document'})
                page.get_by_role('button',name='Queue submission',exact=True).click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text('Needs you')
                expect(page.locator('.inbox-job-notice')).to_contain_text('not been implemented')
                page.get_by_role('button',name='Review command',exact=True).click()
                page.get_by_label('Action',exact=True).select_option('document.archive')
                page.get_by_label('Course destination',exact=True).select_option(label='MATH 140 · Across all terms')
                page.get_by_role('button',name='Save reviewed command',exact=True).click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text('Done',timeout=15000)
                page.locator('[data-inbox-technical] > summary').click()
                expect(page.locator('.inbox-audit')).to_contain_text('quiz.ingest')
                second = page.url
                # One Courses entry in the sidebar opens Current courses.
                page.locator('.sidebar').get_by_role('link',name='Courses',exact=True).click()
                page.get_by_role('link',name='Open MATH 140 · Fall 2026',exact=True).click()
                expect(page.locator('[data-inbox-resources="course"]')).to_contain_text('quiz7.pdf')
                expect(page.locator('[data-inbox-resources="term"]')).not_to_be_visible()
                page.get_by_role('tab',name='Section 502',exact=True).click()
                expect(page.locator('[data-inbox-resources="section"]')).not_to_be_visible()
                page.goto(second)
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text('Done')
                # The page header names the submission and where it sits; the trail leads back to the list.
                expect(page.locator('#page-title')).to_have_text('Archive documents')
                expect(page.locator('#page-context')).to_contain_text('MATH 140 · Across all terms')
                expect(page.locator('#breadcrumb li')).to_have_text(['Home', 'Teaching Inbox', 'Archive documents'])
                expect(page.locator('#primary-action')).to_be_hidden()
                page.screenshot(path='test-results/inbox-job-desktop.png',full_page=True)
                fits(page, 'Job detail')
                page.get_by_role('link',name='Back to Teaching Inbox',exact=True).click()
                expect(page.locator('.inbox-job-row')).to_have_count(0)
                page.get_by_role('button',name='Submissions 2',exact=True).click()
                expect(page.locator('.inbox-job-row')).to_have_count(2)
                more(page, 'Codes & API access…')
                # The email gateway's state is said in this dialog, no longer on the list.
                expect(page.get_by_role('dialog')).to_contain_text('Email delivery is not configured')
                page.get_by_role('button',name='Create code',exact=True).click()
                page.get_by_label('Course destination',exact=True).select_option(label='MATH 140 · Across all terms')
                page.get_by_label('Code label',exact=True).fill('Course handouts')
                page.get_by_role('button',name='Create code',exact=True).click()
                expect(page.get_by_label('Generated submission code',exact=True)).to_be_visible()
                code=page.get_by_label('Generated submission code',exact=True).input_value()
                assert code.startswith('ti:') and 'MATH' not in code
                expect(page.locator('.inbox-qr svg')).to_be_visible()
                page.get_by_role('button',name='Done',exact=True).click()
                more(page, 'Use a submission code…')
                page.get_by_label('Submission code',exact=True).fill(code)
                page.get_by_role('button',name='Continue',exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('Course handouts')
                expect(page.get_by_label('Action',exact=True)).to_have_count(0)
                page.get_by_label('Attachments',exact=True).set_input_files({'name':'handout.txt','mimeType':'text/plain','buffer':b'Course-wide handout'})
                page.get_by_role('button',name='Queue submission',exact=True).click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text('Done',timeout=15000)
                page.get_by_role('link',name='Back to Teaching Inbox',exact=True).click()
                expect(page.locator('.inbox-job-row')).to_have_count(3)
                page.screenshot(path='test-results/inbox-desktop.png',full_page=True)
                # A link to the former Failed tab opens Current.
                page.goto(address+'/#inbox?status=failed')
                expect(page.get_by_role('button',name='Current 0',exact=True)).to_have_attribute('aria-pressed','true')
                expect(page.locator('.inbox-empty')).to_contain_text('Nothing needs you right now')
                page.get_by_role('button',name='Submissions 3',exact=True).click()
                expect(page).to_have_url(re.compile(r'#inbox\?status=submissions$'))
                page.reload()
                expect(page.get_by_role('button',name='Submissions 3',exact=True)).to_have_attribute('aria-pressed','true')
                expect(page.locator('.inbox-job-row')).to_have_count(3)
                check_search(page, inbox)
                fits(page, 'Inbox')
                page.goto(first)
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text('Done')
                # Deleting is a restorable tombstone, so the toast that confirms it offers Restore (the same request as the Deleted tab's).
                first_id = first.rsplit('/', 1)[1]
                page.get_by_role('button',name='Delete from Inbox',exact=True).click()
                warning = page.locator('#inbox-delete-form')
                warning.get_by_label('I understand, delete it from the Inbox.').check()
                warning.get_by_role('button',name='Delete from Inbox',exact=True).click()
                deleted_toast = page.locator('#toast .toast-item').filter(has_text='Deleted from the Inbox.')
                expect(deleted_toast).to_be_visible()
                assert inbox.get(first_id)['deleted'] is not None
                deleted_toast.get_by_role('button',name='Restore',exact=True).click()
                expect(page.locator('#toast')).to_contain_text('Restored to the Inbox.')
                assert inbox.get(first_id)['deleted'] is None
                # Upload scanned assignment is the page's main action: one scan per PDF, and the same file again opens that scan.
                page.set_viewport_size({'width':1440,'height':1000})
                page.goto(address+'/#inbox')
                expect(page.locator('#primary-action')).to_have_text('Upload scanned assignment')
                buffer = io.BytesIO()
                Image.new('L', (850, 1100), 255).save(buffer, format='PDF', resolution=100)
                scan = {'name':'Section 501.pdf','mimeType':'application/pdf','buffer':buffer.getvalue()}
                page.locator('#primary-action').click()
                dialog = page.get_by_role('dialog')
                expect(dialog).to_contain_text('Upload scanned assignment')
                dialog.get_by_label('Scanned PDFs',exact=True).set_input_files({'name':'notes.txt','mimeType':'text/plain','buffer':b'not a scan'})
                dialog.get_by_role('button',name='Upload',exact=True).click()
                expect(dialog.locator('.form-error')).to_contain_text('notes.txt is not a PDF')
                dialog.get_by_label('Scanned PDFs',exact=True).set_input_files(scan)
                dialog.get_by_role('button',name='Upload',exact=True).click()
                expect(page).to_have_url(re.compile(r'#inbox/job/'))
                expect(page.locator('#toast')).to_contain_text('Scan uploaded')
                uploaded = page.url
                page.goto(address+'/#inbox')
                page.locator('#primary-action').click()
                page.get_by_role('dialog').get_by_label('Scanned PDFs',exact=True).set_input_files(scan)
                page.get_by_role('dialog').get_by_role('button',name='Upload',exact=True).click()
                expect(page.locator('#toast')).to_contain_text('already in the Inbox')
                assert page.url == uploaded, (page.url, uploaded)
                # A failed job says why on its tile, comes first in Current, and is retried from there.
                term = next(target for target in inbox.catalog()['targets'] if target['level'] == 'term')
                broken = inbox.submit({'action':'quiz.ingest','target':{'level':'term','id':term['id']},'metadata':{}},
                                      [{'filename':'quiz9.pdf','content':b'%PDF-1.4 quiz nine'}], source='api')
                with connect(inbox.path) as db:
                    db.execute("UPDATE inbox_jobs SET status='failed',error='Simulated worker failure' WHERE id=?", (broken['id'],))
                # It is not a scan, so Current lists only scans and says one other submission needs you, linking to Submissions.
                page.goto(address+'/#inbox')
                expect(page.locator('.inbox-job-list')).not_to_contain_text('quiz9.pdf')
                expect(page.locator('#inbox-search-summary')).to_have_text(re.compile(r'^\d+ needs? you · \d+ working · 0 failed · 1 other submission needs you$'))
                page.locator('[data-inbox-submissions-link]').click()
                expect(page).to_have_url(re.compile(r'#inbox\?status=submissions$'))
                tile = page.locator('.inbox-job-list > *').first
                expect(tile).to_contain_text('quiz9.pdf')
                expect(tile.locator('.inbox-job-note')).to_have_text('Simulated worker failure')
                expect(tile.locator('.inbox-badge')).to_have_text('Failed')
                # Submissions says what it holds at a glance, from the server's own filing of each row.
                expect(page.locator('#inbox-search-summary')).to_have_text(re.compile(r'^0 need you · 0 working · 1 failed$'))
                tile.get_by_role('button',name='Retry job',exact=True).click()
                retried = page.locator('.inbox-job-list > *').filter(has_text='quiz9.pdf')
                expect(retried.locator('.inbox-badge')).to_have_text('Needs you',timeout=15000)
                expect(retried.locator('.inbox-job-note')).to_contain_text('not been implemented')
                expect(retried.get_by_role('button',name='Retry job',exact=True)).to_have_count(0)
                # Workspace settings links to #inbox?codes: it opens Codes & API access, and the address becomes the list's again.
                page.goto(address+'/#inbox?codes')
                expect(page.get_by_role('dialog')).to_contain_text('Codes & API access')
                expect(page).to_have_url(re.compile(r'#inbox$'))
                page.get_by_role('dialog').get_by_role('button',name='Done',exact=True).click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                # Every 'Upload scans' next step links to #inbox?upload=1: it opens Upload scanned assignment once, and the address
                # becomes the list's again, so a reload or Back does not open it a second time.
                page.goto(address+'/#inbox?upload=1')
                expect(page.get_by_role('dialog')).to_contain_text('Upload scanned assignment')
                expect(page).to_have_url(re.compile(r'#inbox$'))
                page.get_by_role('dialog').get_by_role('button',name='Close',exact=True).click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                page.reload()
                expect(page.locator('.inbox-job-list')).to_be_visible()
                page.wait_for_timeout(500)
                expect(page.get_by_role('dialog')).not_to_be_visible()
                fits(page, 'Current')
                page.screenshot(path='test-results/inbox-current.png',full_page=True)
                # A late paper scanned on its own is ticked with the section's scan and combined into one; the originals go to Deleted.
                buffer = io.BytesIO()
                Image.new('L', (850, 1100), 200).save(buffer, format='PDF', resolution=100)
                inbox.submit({'action':'scan.receive'}, [{'filename':'Section 501 late paper.pdf','content':buffer.getvalue()}], source='web',
                             sender='workspace', idempotency_key='late-paper', instructor_review_required=True, large_scans=True)
                page.goto(address+'/#inbox')
                nudge(page)  # the list is already on screen: read it now rather than at the idle list's next poll
                # The tick boxes wait behind one toggle (UX overhaul 2026-09-26).
                expect(page.locator('.inbox-scan-tile')).to_have_count(2)
                expect(page.locator('.inbox-select')).to_have_count(0)
                page.get_by_role('button', name='Select scans to combine', exact=True).click()
                expect(page.get_by_role('button', name='Done selecting', exact=True)).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('.inbox-scan-tile.selectable')).to_have_count(2)
                expect(page.locator('.inbox-scan-tile .inbox-badge').filter(has_text='Working')).to_have_count(0, timeout=60000)
                selection = page.locator('[data-inbox-selection]')
                expect(selection).to_be_hidden()
                page.get_by_label('Select Section 501.pdf to combine', exact=True).check()
                expect(selection).to_contain_text('1 scan selected')
                expect(page.get_by_role('button', name='Combine into one scan', exact=True)).to_be_disabled()
                page.get_by_label('Select Section 501 late paper.pdf to combine', exact=True).check()
                expect(selection).to_contain_text('2 scans selected')
                fits(page, 'Selection')
                page.screenshot(path='test-results/inbox-combine.png',full_page=True)
                page.get_by_role('button', name='Combine into one scan', exact=True).click()
                dialog = page.get_by_role('dialog')
                expect(dialog.locator('.inbox-combine-list li')).to_have_count(2)
                expect(dialog.locator('.inbox-combine-list li').first).to_contain_text('Section 501.pdf')
                dialog.get_by_role('button', name='Combine scans', exact=True).click()
                expect(dialog.locator('.form-error')).to_contain_text('Tick the box')
                dialog.get_by_label('Combine these scans and move the originals to Deleted.').check()
                dialog.get_by_role('button', name='Combine scans', exact=True).click()
                expect(page).to_have_url(re.compile(r'#inbox/job/'))
                expect(page.locator('#toast')).to_contain_text('Scans combined')
                combined = inbox.get(page.url.rsplit('/', 1)[1])
                assert sorted(item['filename'] for item in combined['artifacts'] if item['role'] == 'input') == ['Section 501 late paper.pdf', 'Section 501.pdf'], combined['artifacts']
                page.goto(address+'/#inbox?status=deleted')
                expect(page.locator('.inbox-job-list')).to_contain_text('Section 501 late paper.pdf')
                expect(page.locator('.inbox-select')).to_have_count(0)
                # PDFs dropped on the list open Upload with them chosen.
                page.goto(address+'/#inbox')
                expect(page.locator('.inbox-job-list')).to_be_visible()
                page.evaluate('''() => {
                  const transfer = new DataTransfer();
                  transfer.items.add(new File(['%PDF-1.4 dropped'], 'dropped.pdf', {type: 'application/pdf'}));
                  const list = document.querySelector('.inbox-job-list');
                  list.dispatchEvent(new DragEvent('dragover', {dataTransfer: transfer, bubbles: true, cancelable: true}));
                  list.dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true}));
                }''')
                dialog = page.get_by_role('dialog')
                expect(dialog).to_contain_text('Upload scanned assignment')
                assert dialog.get_by_label('Scanned PDFs', exact=True).evaluate('input => [...input.files].map(file => file.name)') == ['dropped.pdf']
                dialog.get_by_role('button', name='Close', exact=True).click()
                # History shows a run of the same submission once: three archives from one script are one row that opens to list them.
                archives = [inbox.submit({'action':'document.archive','target':{'level':'course','id':next(t for t in inbox.catalog()['targets'] if t['level']=='course')['id']},'metadata':{}},
                                         [{'filename':f'rubric-{number}.csv','content':f'Rubric {number}'.encode()}], source='api') for number in range(3)]
                # History is read once a minute: open it once the worker has archived them.
                for _ in range(60):
                    if all(inbox.get(job['id'])['status'] == 'completed' for job in archives): break
                    page.wait_for_timeout(250)
                page.goto(address+'/#inbox?status=submissions')
                group = page.locator('.inbox-job-group')
                expect(group).to_have_count(1, timeout=15000)
                expect(group.locator('> summary')).to_contain_text('Archive documents · 3 files')
                expect(group.locator('> summary')).to_contain_text('3 submissions')
                expect(group.locator('.inbox-job-row').first).to_be_hidden()
                group.locator('> summary').click()
                expect(group.locator('.inbox-job-row')).to_have_count(3)
                expect(group.locator('.inbox-job-row').first).to_be_visible()
                page.screenshot(path='test-results/inbox-history-grouped.png',full_page=True)
                # A background read that fails (as while the service restarts) keeps the list on screen and says so above it; the next
                # good read takes the notice away by itself, even when nothing in the list changed.
                listing = re.compile(r'/api/inbox/jobs\?')
                restarting = lambda route: route.fulfill(status=503, json={'error': 'Synthetic restart'})
                page.route(listing, restarting)
                nudge(page)
                stale = page.locator('#inbox-section > .inbox-stale')
                expect(stale).to_contain_text("Couldn't refresh. Showing the last loaded results.", timeout=10000)
                expect(stale).to_contain_text('Synthetic restart')
                expect(group).to_have_count(1)
                expect(page.locator('#inbox-section')).not_to_contain_text('Inbox unavailable')
                page.unroute(listing, restarting)
                nudge(page)
                expect(stale).to_have_count(0, timeout=10000)
                expect(group).to_have_count(1)
                # A page that cannot be read at all (another view chosen while the server is away) still says the Inbox is unavailable.
                page.route(listing, restarting)
                page.get_by_role('button', name=re.compile(r'^Current'), exact=False).click()
                expect(page.locator('#inbox-section')).to_contain_text('Inbox unavailable', timeout=10000)
                page.unroute(listing, restarting)
                page.get_by_role('button', name='Try again', exact=True).click()
                expect(page.get_by_role('button', name=re.compile(r'^Current'), exact=False)).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('#inbox-section')).not_to_contain_text('Inbox unavailable')
                # An arrival without a PDF is its Scan stage alone, says why, and points to the next scan in Current that needs you.
                bare = inbox.submit({'action':'scan.receive'}, [{'filename':'cover-note.txt','content':b'no scan here'}], source='email',
                                    sender='scanner', original=b'retained-email:cover-note', idempotency_key='no-pdf-arrival',
                                    authenticated=True, instructor_review_required=True)
                # A grading crop of one of its responses, and a run of three like events in its history (synthetic rows, as grading and
                # the worker would add them).
                with connect(inbox.path) as db:
                    db.execute("INSERT INTO inbox_artifacts VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), bare['message_id'], bare['id'],
                               'response-0-original.png', 'grading/synthetic/0-original.png', 'output', 10, '0' * 64, '2026-10-02T00:00:00+00:00'))
                    for number in range(3):
                        db.execute("INSERT INTO inbox_events (job_id,at,kind,detail,software_version) VALUES (?,?,?,?,?)",
                                   (bare['id'], f'2026-10-02T00:00:0{number}+00:00', 'alias_ocr_retry', f'Synthetic retry {number + 1}', 'teach-inbox/1'))
                # The scan's own page reads the scan's summary (no page rows); once the worker is done with it, its job is polled with
                # its tag and comes back as an empty 304.
                polled = []
                watch = lambda response: polled.append(response.status) if response.url.endswith(f"/api/inbox/jobs/{bare['id']}") and response.request.headers.get('if-none-match') else None
                page.on('response', watch)
                with page.expect_response(lambda response: response.url.endswith(f"/api/documents/messages/{bare['message_id']}?view=summary")):
                    page.goto(address+f"/#inbox/job/{bare['id']}")
                card = page.locator('[data-scan-card]')
                for _ in range(40):
                    if 304 in polled: break
                    page.wait_for_timeout(500)
                page.remove_listener('response', watch)
                assert 304 in polled, polled
                expect(card.locator('[data-pipeline-step]')).to_have_count(1)
                expect(card).to_contain_text('Nothing to inspect: this arrival holds no PDF.')
                expect(card.locator('[data-scan-next-action]')).to_have_count(0)  # it would only open this page again
                expect(page.get_by_role('button', name='Retry inspection')).to_have_count(0)
                expect(page.locator('[data-scan-updated]')).to_contain_text('Updated')
                # The heading's chip says the scan's own state in the canonical words, never the stored "Needs review"; its menu says
                # "Cancel scan…" (2026-10-18).
                expect(page.locator('.inbox-detail-heading .inbox-badge')).to_have_text(re.compile(r'^(Needs you|Working|Nothing to do)$'))
                page.locator('.inbox-detail-heading').get_by_role('button', name='Scan actions').click()
                expect(page.get_by_role('menuitem', name='Cancel scan…')).to_be_visible()
                expect(page.get_by_role('menuitem', name=re.compile('job'))).to_have_count(0)
                page.keyboard.press('Escape')
                # The folded record draws its audit history only once it is opened, and each run of like events only once that run is
                # opened. A scan's record shows no "not implemented" notice: that is how every retained scan waits for its review.
                record = page.locator('[data-inbox-record]')
                assert not record.evaluate('node => node.open')
                expect(page.locator('.inbox-audit li')).to_have_count(0)
                record.locator('summary').first.click()
                expect(page.locator('.inbox-audit li')).to_have_count(0)
                record.locator('[data-inbox-technical] > summary').click()
                expect(page.locator('.inbox-audit li').first).to_be_visible()
                expect(page.locator('.inbox-job-notice')).to_have_count(0)
                run = page.locator('.inbox-audit-run').filter(has_text='Recovery label OCR retried · 3 times')
                expect(run).to_have_count(1)
                expect(run.locator('[data-audit-body] li')).to_have_count(0)
                run.locator('summary', has_text='Show all 3').click()
                expect(run.locator('[data-audit-body] li')).to_have_count(3)
                expect(run.locator('[data-audit-body]')).to_contain_text('Synthetic retry 2')
                # Grading's crops of the scan's responses are listed on their own, not among its pages.
                expect(record.locator('[data-inbox-file-group="results"]')).to_have_count(0)
                crops = record.locator('[data-inbox-file-group="crops"]')
                expect(crops.locator('> summary')).to_contain_text('Grading crops')
                crops.locator('> summary').click()
                expect(crops).to_contain_text('response-0-original.png')
                expect(crops).to_contain_text('each response shows its own in Grading')
                # The original submission opens in the app's file viewer as the email it arrived in (VIEWER), not as a download.
                record.locator('summary', has_text='Original scan').click()
                downloads = []
                page.on('download', lambda item: downloads.append(item))
                record.get_by_role('link', name='Original submission').click()
                viewer = page.locator('dialog.file-viewer')
                expect(viewer.locator('#file-viewer-title')).to_have_text('Original submission')
                expect(viewer.locator('.file-viewer-head p')).to_contain_text('Email')
                expect(viewer.get_by_role('link', name='Download')).to_have_attribute('href', f"/api/inbox/messages/{bare['message_id']}/original")
                viewer.get_by_role('button', name='Close the viewer').click()
                expect(viewer).to_be_hidden()
                assert downloads == [], downloads
                page.get_by_role('button', name='Refresh page review', exact=True).click()
                expect(page.locator('[data-scan-refresh]')).to_have_count(0)
                following = page.locator('[data-inbox-next-current]')
                expect(following).to_be_visible(timeout=10000)
                page.screenshot(path='test-results/inbox-no-pdf.png',full_page=True)
                following.click()
                expect(page).to_have_url(re.compile(r'#inbox/job/(?!' + bare['id'] + ')'))
                # A stage the scan does not have says so in one line, with the way back.
                page.goto(address+f"/#inbox/job/{bare['id']}/students")
                expect(page.locator('[data-scan-stage-missing]')).to_have_text(re.compile(r'^This scan has no Students stage\. Open the scan'))
                # Except the Bubble sheets page: its reader is the only place a read is asked for, so a scan without a recognized
                # sheet still offers it there (review fix W2-INBOX-R1).
                page.goto(address+f"/#inbox/job/{combined['id']}/sheets")
                reader = page.locator('[data-bubble-form]')
                expect(reader).to_be_visible(timeout=10000)
                expect(page.locator('[data-scan-stage-missing]')).to_have_count(0)
                if not reader.evaluate('node => node.open'): reader.locator(':scope > summary').click()
                expect(reader.get_by_role('button', name='Read bubble sheets')).to_be_visible()
                # The scan's page looks again every 5 s while the worker is busy with it (here its inspection is shown as running), but
                # not while the tab is hidden: it waits for the tab to be shown, looks once then, and leaves no listener behind.
                probe = browser.new_page(viewport={'width':1440,'height':1000})
                probe.on('pageerror',lambda error:errors.append(str(error)))
                probe.add_init_script(HIDDEN)
                summary = re.compile(rf"/api/documents/messages/{bare['message_id']}\?view=summary$")
                def busy(route):
                    data = route.fetch(headers={key: value for key, value in route.request.headers.items() if key != 'if-none-match'}).json()
                    data['decode'] = {**(data['decode'] or {}), 'status': 'running'}
                    route.fulfill(json=data)
                probe.route(summary, busy)
                looks = []
                probe.on('request', lambda request: looks.append(request.url) if summary.search(request.url) else None)
                probe.goto(address+f"/#inbox/job/{bare['id']}")
                for _ in range(60):
                    if len(looks) >= 2: break
                    probe.wait_for_timeout(250)
                assert len(looks) >= 2, looks
                listeners = probe.evaluate('window.__visibilityListeners.size')
                probe.evaluate('window.__setHidden(true)')
                seen = len(looks)
                probe.wait_for_timeout(11000)
                assert len(looks) == seen, len(looks) - seen
                assert probe.evaluate('window.__visibilityListeners.size') == listeners + 1
                probe.evaluate('window.__setHidden(false)')
                probe.wait_for_timeout(2500)
                assert len(looks) == seen + 1, len(looks) - seen
                assert probe.evaluate('window.__visibilityListeners.size') == listeners
                probe.close()
                check_scan_groups(page, address)
                check_poll_cadence(browser, address, errors)
                check_missing_scan(browser, address, errors)
                # Last: the worker stops, and a new scan waits for it.
                stop.set()
                worker.join(timeout=10)
                check_worker_notice(page, address, inbox)
                assert not errors,errors
                browser.close()
                print('Inbox browser checks passed: web upload, queued roster processing, IDs, downloads, unimplemented actions, review, audit, QR/codes, fixed destinations, search/status filters, retained search and drafts, escaped filenames, deep links, the desktop layout at 1280 px, scanned-assignment upload, the Current list and combining scans, the poll cadence, a failed background read, a missing scan, grouped scans of one assignment, the upload deep link, the folded audit history, grading crops and the worker notice on a waiting scan.')
        finally:
            stop.set();worker.join(timeout=5);server.close()


if __name__=='__main__':
    main()
