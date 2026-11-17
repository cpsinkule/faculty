"""ASSISTANT: workspace read projections for automation and blocked work; no scheduler of its own."""
import json
from rosters import connect


def inventory(service):
    found = service.workflows.listing()
    watch = service.watches.listing()
    runs = found['preparations']
    rows = []
    with connect(service.database) as db:
        terms = {r['id']: _term_label(r) for r in db.execute('SELECT id,code,term,term_other,year FROM course_terms')}
    from .workflows import LATEST
    for rule in found['workflows']:
        # The rule's own latest run, not only among the 30 newest of every kind (watches crowd those out).
        last = next((r for r in runs if r['kind'] == rule['kind'] and r['target'] == rule['target']), None)
        if last is None:
            with connect(service.database) as db:
                row = db.execute(LATEST + ' WHERE r.kind=? AND r.target=? ORDER BY r.rowid DESC LIMIT 1', (rule['kind'], rule['target'])).fetchone()
                last = {'state': row['state'], 'actions': service.workflows.actions(db, row['id'])} if row else None
        rows.append({'id': rule['id'], 'kind': 'workflow', 'title': rule['title'], 'enabled': bool(rule['enabled']),
                     'scope': terms.get(rule['term_id'], 'Workspace'), 'schedule': 'Daily at 5:00 AM Central' if rule['kind'] == 'reconciliation' else 'When assessment records change',
                     'next_check': rule['next_check'], 'last_result': last['state'] if last else 'Not checked yet',
                     'blocker': rule['last_error'], 'revision': rule['revision'],
                     # AUTOMATION VISIBILITY (2026-10-09 review): neutral status beside `blocker`, which keeps real problems only.
                     'waiting': rule.get('waiting') or '', 'held': rule.get('held') or '',
                     'last_checked_at': rule.get('last_checked_at'), 'last_outcome': rule.get('last_outcome') or '',
                     'pending': sum(a['pending'] for a in last['actions']) if last else 0,
                     'href': '#gradebook/' + rule['term_id'] if rule['kind'] == 'reconciliation' and rule['term_id'] else
                             '#exams/' + rule['target'] if rule['kind'] == 'readiness' and ':' not in rule['target'] else
                             '#assignments/' + rule['target'].replace(':', '/', 1) if rule['kind'] == 'readiness' else '#overview'})
    for rule in watch['watches']:
        rows.append({'id': rule['id'], 'kind': 'watch', 'title': rule['title'], 'enabled': bool(rule['enabled']),
                     'scope': {'scan': 'One Inbox submission', 'grading': 'One grading plan', 'exam': 'One assessment'}[rule['kind']], 'schedule': 'Every two minutes; notify on ' + rule['condition'],
                     'last_result': (rule.get('observation') or {}).get('message') or 'Not checked yet',
                     'last_check': rule['checked_at'], 'blocker': rule['last_error'], 'revision': rule['revision'],
                     'waiting': '', 'held': '', 'last_checked_at': rule['checked_at'] or None, 'last_outcome': '',
                     'href': (rule.get('observation') or {}).get('page') or '#overview'})
    with connect(service.database) as db:
        paused = db.execute('SELECT paused FROM student_support_suggestion_pauses ORDER BY id DESC LIMIT 1').fetchone()
        settings = db.execute('SELECT settings_json FROM frq_settings WHERE id=1').fetchone()
    grading = json.loads(settings[0]) if settings else {}
    canvas = service.app.extensions.get('canvas')
    from gradebook import canvas_nightly, iclicker_after_class
    gradebook = service.app.extensions['gradebook']
    observed = {'canvas': [], 'iclicker': []}
    with connect(service.database) as db:  # catalog reads only: no lifecycle lock (2026-10-09 review)
        for term in db.execute('SELECT id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY id'):
            last = canvas_nightly.latest(gradebook, db, term['id'])
            if last:
                observed['canvas'].append({'scope': terms[term['id']], 'status': last['status'], 'at': last['at'],
                    'blocker': last.get('problem') or ('Assignments need matching' if last.get('waiting') else ''),
                    'href': '#gradebook/' + term['id']})
        for section in db.execute('''SELECT s.id,s.term_id,s.number FROM course_sections s JOIN course_terms t ON t.id=s.term_id
                WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY s.term_id,s.number'''):
            last = iclicker_after_class.latest(db, section['id'])
            if last:
                observed['iclicker'].append({'scope': terms[section['term_id']] + ' · Section ' + str(section['number']),
                    'status': last['status'], 'at': last['at'], 'blocker': last.get('problem') or '',
                    'href': '#gradebook/' + section['term_id'] + '?section=' + section['id']})
    rows += [
        {'id': 'scan-pipeline', 'kind': 'built-in', 'title': 'Scan processing', 'enabled': True, 'scope': 'Current submissions',
         'schedule': 'As eligible papers arrive', 'href': '#inbox', 'last_result': 'Inspect the Inbox for current work', 'blocker': ''},
        {'id': 'auto-grade', 'kind': 'built-in', 'title': 'Automatic grading', 'enabled': grading.get('auto_grade', True),
         'scope': 'Eligible current assessments', 'schedule': 'After new work settles for two minutes',
         'href': '#grading?settings=1', 'last_result': 'Requires approved rubrics and answer areas', 'blocker': ''},
        {'id': 'auto-certify', 'kind': 'built-in', 'title': 'Late papers certify themselves', 'enabled': grading.get('auto_certify', True),
         'scope': 'Certified current assessments', 'schedule': 'After a late paper is graded and settles for five minutes',
         'href': '#grading?settings=1', 'last_result': 'Only students with no certified grade; sent to Gradescope as their section was', 'blocker': ''},
        {'id': 'makeup-version-gradescope', 'kind': 'built-in', 'title': 'Makeup versions on Gradescope', 'enabled': True,
         'scope': 'Assessments linked on Gradescope', 'schedule': 'Once a makeup version is ready to grade', 'href': '#overview',
         'last_result': 'Added once to every linked set that lacks it', 'blocker': ''},
        {'id': 'gradescope-after-certifying', 'kind': 'built-in', 'title': 'Gradescope after certifying', 'enabled': grading.get('auto_gradescope', True),
         'scope': 'Assessments you certify with the Gradescope box ticked, and late papers', 'schedule': 'Right after a certification',
         'href': '#grading?settings=1', 'last_result': 'Makes the assignment where a section has none, sends what is missing, publishes each section that is fully graded there',
         'blocker': ''},
        {'id': 'follow-ups', 'kind': 'built-in', 'title': 'Follow-up preparation', 'enabled': not bool(paused and paused[0]),
         'scope': 'Current student conversations', 'schedule': 'When conversations need a reply', 'href': '#follow-ups',
         'last_result': 'Replies retain explicit Send', 'blocker': ''},
        {'id': 'canvas-morning', 'kind': 'built-in', 'title': 'Morning Canvas sync', 'enabled': bool(canvas and canvas.settings.configured),
         'scope': 'Linked current terms', 'schedule': 'Daily from 5:00–8:00 AM Central', 'href': '#canvas',
         'last_result': 'See retained results below' if observed['canvas'] else 'No retained morning run',
         'observations': observed['canvas'], 'blocker': '' if canvas and canvas.settings.configured else 'Canvas is not configured'},
        {'id': 'iclicker-after-class', 'kind': 'built-in', 'title': 'iClicker after class', 'enabled': True,
         'scope': 'Sections with linked iClicker and an active term schedule', 'schedule': 'Ten minutes after the last class',
         'href': '#current-courses', 'last_result': 'See retained results below' if observed['iclicker'] else 'No retained after-class run',
         'observations': observed['iclicker'], 'blocker': ''},
    ]
    with connect(service.database) as db:
        rows += outcomes(service, db, rows, found['workflows'])
        done = done_by_faculty(db)
    return {'automations': rows, 'preparations': runs, **({'done': done} if done['lines'] else {}),
            'note': 'Built-in controls open their owning page. Pausing a preparation revokes its pending changes.'}


def blockers(service, plan_id=None, job_id=None):
    def read(path):
        status, value, _ = service.transport.get(path)
        if status != 200:
            raise ValueError('ASSISTANT: the owning work could not be read.')
        return value
    if plan_id:
        view = read('/api/grading/assignments/' + plan_id)
        return {'plan_id': plan_id, 'entry': view.get('entry'), 'workflow': view.get('workflow'),
                'questions': [{'question_id': q['question_id'], 'title': q['content'].get('title'),
                               'rubric': bool(q.get('rubric')), 'rubric_approved': bool((q.get('rubric') or {}).get('approved_at')),
                               'locations': q.get('location_check')} for q in view.get('questions', [])],
                'href': '#grading/' + plan_id,
                'note': 'Prepare missing prerequisites. Retry only confirmed failures. Certifying is the instructor’s, except that '
                        'Faculty certifies a late paper of a student with no certified grade by itself once it is graded and '
                        'cleared (see certification’s automatic).'}
    if job_id:
        view = read('/api/inbox/jobs/' + job_id)
        return {'job_id': job_id, 'status': view.get('status'), 'error': view.get('error'), 'events': view.get('events'),
                'href': '#inbox/job/' + job_id, 'note': 'An interrupted or uncertain outcome requires explicit review before any retry.'}
    view = read('/api/workspace/overview')
    return {'attention': view.get('attention'), 'operations': view.get('operations'),
            'note': 'These are the owning services’ current blockers; no work was retried.'}


# AUTOMATION VISIBILITY (2026-10-09 review; the instructor: "Fix all of the issues and implement your suggestions"). What Faculty
# did by itself, read from the receipts and events the owning modules already keep: catalog reads only, bounded, no outward call,
# no new storage, and counts and plan titles rather than student names.
DONE_DAYS = 7
TRIAGE_DAYS = 30
DONE_LINES = 40
HARMLESS_SKIPS = ('Already on Gradescope.', 'Nothing to send.')
ABOUT = ('scan-pipeline', 'auto-grade', 'follow-ups')  # built-in rows whose `last_result` describes the rule rather than an outcome
SYNC_WORDS = {'synced': 'synced with Canvas', 'imported': 'imported WebAssign and Canvas quiz scores', 'unchanged': 'nothing had changed',
              'none': 'nothing to do', 'needs_choice': 'waits for your choice of columns', 'signed_out': 'Canvas was signed out',
              'failed': 'failed'}
PLAN = 'SELECT f.id,a.title FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id JOIN assignments a ON a.id=e.assignment_id'
TIMELINE = '''SELECT m.id,m.channel,r.revision,r.sent_at,r.author_role,r.context_json,m.external_id,m.thread_key,r.captured_at,
    l.enrollment_id,MAX(CASE WHEN l.role='author' THEN 1 ELSE 0 END) student_author
    FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
    JOIN comm_links l ON l.message_id=m.id JOIN course_terms t ON t.id=l.term_id
    WHERE t.deleted_at IS NULL AND m.channel IN ('gmail','canvas_message','canvas_comment','gradescope_regrade') AND l.enrollment_id=?
    GROUP BY m.id,l.enrollment_id ORDER BY r.sent_at,m.id'''


def _cutoff(days, now=None):
    from datetime import datetime, timedelta, timezone
    return ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()


def _instant(value):
    from communications.record import utc
    return utc(value)


def _has(db, *names):
    marks = ','.join('?' * len(names))
    return len({r[0] for r in db.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({marks})", names)}) == len(names)


def _plural(count, word, many=None):
    return f"{count} {word if count == 1 else many or word + 's'}"


def _titles(db, ids):
    ids = sorted({i for i in ids if i})
    if not ids or not _has(db, 'frq_assignments', 'exam_events', 'assignments'):
        return {}
    return {r['id']: r['title'] for r in db.execute(PLAN + f" WHERE f.id IN ({','.join('?' * len(ids))})", ids)}


def _current_plans(db):
    if not _has(db, 'frq_assignments', 'exam_events', 'assignments', 'course_terms'):
        return {}
    return {r['id']: r['title'] for r in db.execute(PLAN + ' JOIN course_terms t ON t.id=e.term_id WHERE t.is_current=1 AND t.deleted_at IS NULL')}


def _late_rows(summary_json):
    return len((json.loads(summary_json or '{}').get('automatic') or {}).get('rows') or [])


def _term_label(row):
    name = row['term_other'] if row['term'] == 'Other' else row['term']
    return f"{row['code']} · {name} {row['year']}"


def done_by_faculty(db, now=None):
    """{lines: [{at, text, href | tab}], more, days}: one line per thing Faculty did by itself in the last DONE_DAYS, newest first,
    each linking to its owning page (the Assistant's own changes open its Changes tab)."""
    from datetime import datetime
    from scheduling.clock import CAMPUS_ZONE
    since, lines = _cutoff(DONE_DAYS, now), []
    if _has(db, 'frq_certifications'):
        # GRADING "LATE PAPERS CERTIFY THEMSELVES": Faculty's certifications carry actor 'automatic'.
        rows = db.execute("""SELECT assignment_id,summary_json,created_at FROM frq_certifications WHERE actor='automatic' AND created_at>=?
            ORDER BY created_at DESC LIMIT 50""", (since,)).fetchall()
        titles = _titles(db, [r['assignment_id'] for r in rows])
        lines += [{'at': r['created_at'], 'text': f"Certified {_plural(_late_rows(r['summary_json']), 'late paper')} · {titles.get(r['assignment_id'], 'An assessment')}",
                   'href': f"#grading/{r['assignment_id']}?tab=export"} for r in rows]
    if _has(db, 'frq_gradescope_automatic_sends'):
        # GRADESCOPE "AUTOMATIC LATE-PAPER SEND": recorded rows name their ordinary send.
        rows = db.execute("""SELECT assignment_id,COUNT(DISTINCT section_id||':'||enrollment_id) papers,MAX(updated_at) at FROM frq_gradescope_automatic_sends
            WHERE status='recorded' AND updated_at>=? GROUP BY certification_id,assignment_id ORDER BY at DESC LIMIT 50""", (since,)).fetchall()
        titles = _titles(db, [r['assignment_id'] for r in rows])
        lines += [{'at': r['at'], 'text': f"Sent {_plural(r['papers'], 'late paper')} to Gradescope · {titles.get(r['assignment_id'], 'An assessment')}",
                   'href': f"#grading/{r['assignment_id']}?tab=export&open=gradescope"} for r in rows]
    if _has(db, 'frq_gradescope_creations'):
        # GRADESCOPE "A VERSION THE SET LACKS": Faculty's own creation names the letter it added.
        rows = db.execute("""SELECT assignment_id,json_extract(plan_json,'$.automatic.letter') letter,status,created_at FROM frq_gradescope_creations
            WHERE actor='automatic' AND json_extract(plan_json,'$.automatic.letter') IS NOT NULL AND created_at>=? ORDER BY created_at DESC LIMIT 50""", (since,)).fetchall()
        titles = _titles(db, [r['assignment_id'] for r in rows])
        verb = {'complete': 'Added', 'cancelled': 'Stopped adding'}
        lines += [{'at': r['created_at'], 'text': f"{verb.get(r['status'], 'Adding')} makeup Version {r['letter'] or '?'} on Gradescope · {titles.get(r['assignment_id'], 'An assessment')}",
                   'href': f"#grading/{r['assignment_id']}?tab=export&open=gradescope"} for r in rows]
    if _has(db, 'frq_gradescope_automatic_runs'):
        # GRADESCOPE "AFTER CERTIFYING": one line per finished run that did something or holds a section.
        rows = db.execute("""SELECT assignment_id,kind,outcome_json,updated_at FROM frq_gradescope_automatic_runs WHERE status='done' AND updated_at>=?
            ORDER BY updated_at DESC LIMIT 50""", (since,)).fetchall()
        titles = _titles(db, [r['assignment_id'] for r in rows])
        for r in rows:
            said = after_words(r['kind'], json.loads(r['outcome_json'] or '{}'))
            if said:
                lines.append({'at': r['updated_at'], 'text': f"{said} · {titles.get(r['assignment_id'], 'An assessment')}",
                              'href': f"#grading/{r['assignment_id']}?tab=export"})
    if _has(db, 'gradebook_canvas_nightly_runs', 'course_terms'):
        # CANVAS NIGHTLY SYNC: one row per attempt of a term's morning.
        rows = db.execute("""SELECT n.term_id,n.status,n.created_at,t.code,t.term,t.term_other,t.year FROM gradebook_canvas_nightly_runs n
            JOIN course_terms t ON t.id=n.term_id WHERE n.created_at>=? ORDER BY n.created_at DESC LIMIT 50""", (since,)).fetchall()
        lines += [{'at': r['created_at'], 'text': f"Morning Canvas sync · {_term_label(r)}: {SYNC_WORDS.get(r['status'], r['status'])}",
                   'href': '#gradebook/' + r['term_id']} for r in rows]
    if _has(db, 'assistant_actions', 'assistant_action_events'):
        # ASSISTANT: the automatic tier's receipts, counted per campus day (their summaries can name students).
        days = {}
        for (created,) in db.execute("""SELECT a.created_at FROM assistant_actions a
                JOIN assistant_action_events e ON e.id=(SELECT MAX(id) FROM assistant_action_events WHERE action_id=a.id)
                WHERE a.tier='auto' AND a.created_at>=? AND e.kind='succeeded' ORDER BY a.created_at DESC LIMIT 500""", (since,)):
            try:
                day = datetime.fromisoformat(_instant(created)).astimezone(CAMPUS_ZONE).date().isoformat()
            except (TypeError, ValueError):
                day = str(created)[:10]
            entry = days.setdefault(day, [0, created])
            entry[0] += 1
        lines += [{'at': at, 'text': f"The Assistant made {_plural(count, 'change')} by itself", 'tab': 'changes'} for count, at in days.values()]
    lines.sort(key=lambda line: _instant(line['at']), reverse=True)
    return {'lines': lines[:DONE_LINES], 'more': max(0, len(lines) - DONE_LINES), 'days': DONE_DAYS}


def after_words(kind, outcome):
    """What a finished run of GRADESCOPE "AFTER CERTIFYING" did, in a line (section numbers only), or '' when it found nothing to do."""
    publish = outcome.get('publish') or {}
    sections, held = publish.get('sections') or [], publish.get('held') or []
    sent = (outcome.get('written') or {}).get('send_id') or (outcome.get('sheets') or {}).get('send_id')
    parts = []
    if kind == 'late':
        if sections:
            parts.append(f"Published a late paper’s grade on Gradescope (section {', '.join(str(s) for s in sections)})")
    else:
        if (outcome.get('create') or {}).get('creation_id'):
            parts.append('Made the assignment on Gradescope')
        if sent:
            parts.append('Sent to Gradescope')
        if sections:
            parts.append(f"Published {_plural(len(sections), 'section')} on Gradescope")
    if held:
        parts.append(f"{_plural(len(held), 'section')} held back on Gradescope")
    if outcome.get('stopped'):
        parts.append('stopped before finishing')
    return ' · '.join(parts)


def gradescope_after(db, gradescope):
    """The "Gradescope after certifying" row: on only while its switch is on and Gradescope is set up (a local file check, never a
    sign-in), its newest run that did something, whether one is under way, and the newest section Faculty holds for the instructor's
    own Publish grades (until a later publication of that plan finishes)."""
    status = gradescope.settings.status() if gradescope is not None and getattr(gradescope, 'settings', None) is not None else {}
    found = {'last_outcome': 'Nothing sent or published by itself yet'}
    if not status.get('configured'):
        found.update(enabled=False, blocker=status.get('problem') or 'Gradescope is not set up.')
    if not _has(db, 'frq_gradescope_automatic_runs'):
        return found
    plans = _current_plans(db)
    for r in db.execute("SELECT assignment_id,kind,outcome_json,updated_at FROM frq_gradescope_automatic_runs WHERE status='done' ORDER BY updated_at DESC LIMIT 20"):
        said = after_words(r['kind'], json.loads(r['outcome_json'] or '{}'))
        if said:
            title = _titles(db, [r['assignment_id']]).get(r['assignment_id'], 'An assessment')
            found.update(last_outcome=f"{said} · {title}", last_checked_at=r['updated_at'], href=f"#grading/{r['assignment_id']}?tab=export")
            break
    working = db.execute("SELECT COUNT(*) FROM frq_gradescope_automatic_runs WHERE status!='done'").fetchone()[0]
    if working:
        found['waiting'] = f"{_plural(working, 'assessment')} going to Gradescope now"
    for r in db.execute("SELECT assignment_id,outcome_json,updated_at FROM frq_gradescope_automatic_runs WHERE status='done' ORDER BY updated_at DESC LIMIT 50"):
        held = (json.loads(r['outcome_json'] or '{}').get('publish') or {}).get('held') or []
        if not held or r['assignment_id'] not in plans or not status.get('configured'):
            continue
        if db.execute("SELECT 1 FROM frq_gradescope_publications WHERE assignment_id=? AND status='complete' AND created_at>? LIMIT 1",
                      (r['assignment_id'], r['updated_at'])).fetchone():
            continue  # the instructor published it since
        more = f" ({len(held) - 1} more)" if len(held) > 1 else ''
        found.update(blocker=f"Faculty did not publish section {held[0]['section']} of {plans[r['assignment_id']]} on Gradescope: {held[0]['reason']}{more}",
                     href=f"#grading/{r['assignment_id']}?tab=export")
        break
    return found


def late_papers(db):
    """The "Late papers certify themselves" row's real outcome: the newest automatic certification, what still waits, and the late
    papers Faculty could not send to Gradescope by itself (GRADESCOPE "AUTOMATIC LATE-PAPER SEND" says why once)."""
    found, plans = {'last_outcome': 'No late paper certified by itself yet'}, _current_plans(db)
    if not _has(db, 'frq_certifications'):
        return found
    latest = db.execute("SELECT assignment_id,summary_json,created_at FROM frq_certifications WHERE actor='automatic' ORDER BY created_at DESC LIMIT 1").fetchone()
    if latest:
        title = _titles(db, [latest['assignment_id']]).get(latest['assignment_id'], 'An assessment')
        found.update(last_outcome=f"Certified {_plural(_late_rows(latest['summary_json']), 'late paper')} · {title}",
                     last_checked_at=latest['created_at'], href=f"#grading/{latest['assignment_id']}?tab=export")
    waiting = 0
    for aid in plans:
        certified = db.execute('SELECT created_at FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1', (aid,)).fetchone()
        if not certified:
            continue
        reason = db.execute("""SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='automatic_certification_not_made' AND created_at>=?
            ORDER BY id DESC LIMIT 1""", (aid, certified[0])).fetchone()
        waiting += len(json.loads(reason[0]).get('students') or []) if reason else 0
    parts = [f"{_plural(waiting, 'late paper')} {'waits' if waiting == 1 else 'wait'} to certify"] if waiting else []
    if _has(db, 'frq_gradescope_automatic_sends') and plans:
        marks = ','.join('?' * len(plans))
        sending = db.execute(f"""SELECT COUNT(*) FROM frq_gradescope_automatic_sends WHERE status IN ('pending','sending')
            AND assignment_id IN ({marks})""", list(plans)).fetchone()[0]
        if sending:
            parts.append(f"{_plural(sending, 'late paper')} {'waits' if sending == 1 else 'wait'} to go to Gradescope")
        skipped = [r for r in db.execute(f"""SELECT assignment_id,error FROM frq_gradescope_automatic_sends WHERE status='skipped' AND updated_at>=?
            AND assignment_id IN ({marks}) ORDER BY updated_at DESC LIMIT 50""", [_cutoff(DONE_DAYS), *plans]) if r['error'] not in HARMLESS_SKIPS]
        if skipped:
            found.update(blocker=f"{_plural(len(skipped), 'late paper')} not sent to Gradescope by itself: {skipped[0]['error'] or 'no reason was recorded'}",
                         href=f"#grading/{skipped[0]['assignment_id']}?tab=export&open=gradescope")
    if parts:
        found['waiting'] = ' · '.join(parts)
    return found


def makeup_versions(db, gradescope):
    """The "Makeup versions on Gradescope" row: on only while Gradescope is set up (a local file check, never a sign-in), its newest
    automatic addition, and a version Faculty could not add by itself that no later creation added."""
    status = gradescope.settings.status() if gradescope is not None and getattr(gradescope, 'settings', None) is not None else {}
    configured = bool(status.get('configured'))
    found = {'enabled': configured, 'href': '#grading', 'last_outcome': 'No makeup version added by itself yet',
             'blocker': '' if configured else status.get('problem') or 'Gradescope is not set up.'}
    if not _has(db, 'frq_gradescope_creations', 'frq_events'):
        return found
    made = db.execute("""SELECT assignment_id,json_extract(plan_json,'$.automatic.letter') letter,status,created_at FROM frq_gradescope_creations
        WHERE actor='automatic' AND json_extract(plan_json,'$.automatic.letter') IS NOT NULL ORDER BY created_at DESC LIMIT 1""").fetchone()
    if made:
        title = _titles(db, [made['assignment_id']]).get(made['assignment_id'], 'An assessment')
        verb = {'complete': 'Added', 'cancelled': 'Stopped adding'}.get(made['status'], 'Adding')
        found.update(last_outcome=f"{verb} makeup Version {made['letter'] or '?'} · {title}", last_checked_at=made['created_at'],
                     href=f"#grading/{made['assignment_id']}?tab=export&open=gradescope")
    missed = []
    for aid, title in _current_plans(db).items():
        for event in db.execute("""SELECT detail_json,created_at FROM frq_events WHERE assignment_id=? AND kind='gradescope_version_not_added'
                ORDER BY id DESC LIMIT 5""", (aid,)):
            if not db.execute("SELECT 1 FROM frq_gradescope_creations WHERE assignment_id=? AND created_at>? AND status!='cancelled' LIMIT 1",
                              (aid, event['created_at'])).fetchone():
                detail = json.loads(event['detail_json'])
                missed.append((event['created_at'], aid, title, detail.get('letter') or '?', (detail.get('reasons') or ['no reason was recorded'])[0]))
    if missed and configured:
        missed.sort(reverse=True)
        _, aid, title, letter, reason = missed[0]
        more = f" ({len(missed) - 1} more)" if len(missed) > 1 else ''
        found.update(blocker=f"Faculty could not add makeup Version {letter} of {title} by itself: {reason}{more}",
                     href=f"#grading/{aid}?tab=export&open=gradescope")
    return found


def _timeline(db, enrollment):
    """{conversation key: [{role, sent_at}]} of one student, grouped as triage groups them (decisions_sandbox/labels.py `timeline`)."""
    from student_support.service import conversation_key, joins, regrade_role
    rows = db.execute(TIMELINE, (enrollment,)).fetchall()
    keyed = [(row, conversation_key(row, json.loads(row['context_json']))) for row in rows]
    joined = joins(db, {(row['enrollment_id'], key) for row, key in keyed})
    grouped = {}
    for row, key in keyed:
        role = regrade_role(row)
        if role == 'instructor':
            grouped.setdefault(joined.get((enrollment, key), key), []).append(row['sent_at'])
    return grouped


def triage_counts(db, now=None):
    """Observed, never an accuracy rate (AUTOMATION VISIBILITY): over TRIAGE_DAYS, the conversations whose triage read as FYI (and had no
    follow-up yet), how many of those the instructor later answered or tracked anyway, and the follow-ups THANKS CLOSES resolved
    (with the instructor's Undos). A lower bound: it sees only what was answered or tracked here."""
    from student_support.triage import reads_as_fyi
    since, first = _cutoff(TRIAGE_DAYS, now), {}
    counts = {'fyi': 0, 'answered': 0, 'thanks': 0, 'undone': 0}
    if _has(db, 'jev_calls'):
        for row in db.execute("""SELECT owner_id,enrollment_id,model,result_json,created_at FROM jev_calls WHERE purpose='student-triage'
                AND status='complete' AND created_at>=? ORDER BY created_at LIMIT 5000""", (since,)):
            if row['owner_id'] in first or not row['enrollment_id'] or not row['owner_id'].startswith(row['enrollment_id'] + ':'):
                continue
            try:
                fyi = reads_as_fyi({**json.loads(row['result_json']), 'model': row['model']})
            except (KeyError, TypeError, ValueError):
                continue
            if fyi:
                first[row['owner_id']] = (row['enrollment_id'], row['created_at'])
    timelines = {}
    for owner, (enrollment, moment) in list(first.items())[:300]:
        source = owner[len(enrollment) + 1:]
        item = db.execute("SELECT created_at FROM student_support_items WHERE kind='followup' AND enrollment_id=? AND source_key=?",
                          (enrollment, source)).fetchone() if _has(db, 'student_support_items') else None
        if item and _instant(item[0]) <= _instant(moment):
            continue  # already tracked: shown as its follow-up, never as FYI
        counts['fyi'] += 1
        if enrollment not in timelines and _has(db, 'comm_messages', 'comm_revisions', 'comm_links'):
            timelines[enrollment] = _timeline(db, enrollment)
        wrote = any(_instant(sent) > _instant(moment) for sent in timelines.get(enrollment, {}).get(source, []))
        counts['answered'] += bool(item or wrote)
    if _has(db, 'assistant_actions', 'assistant_action_events', 'student_support_suggestion_undos'):
        closed = db.execute("""SELECT COUNT(*),COALESCE(SUM(EXISTS(SELECT 1 FROM student_support_suggestion_undos u WHERE u.action_id=a.id)),0)
            FROM assistant_actions a JOIN assistant_action_events e ON e.id=(SELECT MAX(id) FROM assistant_action_events WHERE action_id=a.id)
            WHERE a.tier='auto' AND a.endpoint='student_support.revise' AND a.created_at>=? AND e.kind='succeeded'
            AND json_extract(a.body_json,'$.request_id') LIKE 'thanks-closes:%'""", (since,)).fetchone()
        counts['thanks'], counts['undone'] = closed[0], closed[1]
    return counts


def triage(db, now=None, status=None):
    """The "Student triage" row: the provider and model that triage now (JEV TRIAGE PROVIDER) and the 30-day observed counts.
    `status` is JevService.triage_status() (JEV HOST FAILURES): a pause is the row's blocker, failed reads a waiting note."""
    from jev import client
    from jev.service import TRIAGE_PURPOSE, provider_for
    provider = provider_for(TRIAGE_PURPOSE)
    counts = triage_counts(db, now)
    line = (f"{_plural(counts['fyi'], 'conversation')} shown as FYI · {counts['answered']} you later answered or tracked · "
            f"{_plural(counts['thanks'], 'follow-up')} closed on a thanks" + (f" · {counts['undone']} you undid" if counts['undone'] else ''))
    return {'id': 'student-triage', 'kind': 'built-in', 'title': 'Student triage', 'enabled': bool(provider.available()),
            'scope': f"{'Jev' if provider is client else 'OpenAI Decisions'} · {provider.MODEL}", 'schedule': 'When a student message is captured',
            'href': '#follow-ups?filter=fyi', 'last_result': line, 'last_outcome': line, 'outcome_label': 'Last 30 days',
            'note': 'Observed, not an accuracy rate.', 'counts': counts, 'blocker': (status or {}).get('paused') or '',
            'waiting': (f"{_plural(status['failed_count'], 'conversation')} could not be read · Retry all failed on Follow-up"
                        if (status or {}).get('failed_count') else '')}


def outcomes(service, db, rows, rules):
    """Fills in the built-in rows' real outcomes and each rule row's Prepare again target, and returns the rows to add."""
    targets = {rule['id']: {'kind': rule['kind'], 'target': rule['target']} for rule in rules}
    for row in rows:
        if row['id'] in ABOUT:
            row['about'] = True  # a fixed description, shown as "How it works", never as a result
        elif row['kind'] == 'workflow' and row['id'] in targets:
            row.setdefault('prepare', targets[row['id']])
        elif row['id'] == 'auto-certify':
            row.update(late_papers(db))
        elif row['id'] == 'makeup-version-gradescope':
            row.update(makeup_versions(db, service.app.extensions.get('gradescope')))
        elif row['id'] == 'gradescope-after-certifying':
            row.update(gradescope_after(db, service.app.extensions.get('gradescope')))
    jev = service.app.extensions.get('jev')
    try:
        status = jev.triage_status() if jev is not None and hasattr(jev, 'triage_status') else None
    except Exception:  # a status read never takes the Automation tab down
        status = None
    return [triage(db, status=status)]


def changes_waiting(db, now=None):
    """Home's "Assistant changes waiting" (FRONTEND "Home"): the Approve cards the Changes tab lists as waiting (latest event proposed
    or previewed, younger than the assistant's EXPIRY), less those of follow-up preparations, which Home's Student follow-up card
    already counts. A catalog count only; expiring old cards stays the Assistant's own read."""
    from datetime import datetime, timezone
    from .service import EXPIRY
    if not _has(db, 'assistant_actions', 'assistant_action_events'):
        return 0
    followups = ('AND NOT EXISTS(SELECT 1 FROM student_support_suggestion_actions s WHERE s.action_id=a.id)'
                 if _has(db, 'student_support_suggestion_actions') else '')
    cutoff = ((now or datetime.now(timezone.utc)) - EXPIRY).isoformat()
    return db.execute(f"""SELECT COUNT(*) FROM assistant_actions a WHERE a.tier='approve' AND a.created_at>=?
        AND (SELECT kind FROM assistant_action_events e WHERE e.action_id=a.id ORDER BY e.id DESC LIMIT 1) IN ('proposed','previewed')
        {followups}""", (cutoff,)).fetchone()[0]
