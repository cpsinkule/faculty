import json
"""Real catalog intake/term boundaries, with synthetic retained page bytes."""
import uuid
import unittest
from unittest.mock import patch

from werkzeug.exceptions import Conflict, Forbidden, NotFound
from rosters import connect
from test_exam_grading import HEADERS
import test_frq_grading as fixture


class GradingIntakeTests(unittest.TestCase):
    managed = False
    setUp = fixture.FrqWorkflowTests.setUp
    document_version = fixture.FrqWorkflowTests.document_version
    ready_batch = fixture.FrqWorkflowTests.ready_batch
    reviewed_source = fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = fixture.FrqWorkflowTests.configure_rubric
    queue = fixture.FrqWorkflowTests.queue
    finish = fixture.FrqWorkflowTests.finish
    approve = fixture.FrqWorkflowTests.approve

    def test_step_activity_is_scoped_and_does_not_walk_other_assignments(self):
        job, _ = self.queue()
        with patch.object(self.frq, 'overview', side_effect=AssertionError('A step must not load the overview')):
            response = self.client.get(f"/api/grading/assignments/{self.plan['id']}?include=activity")
        self.assertEqual(response.status_code, 200)
        plan = response.get_json()
        self.assertEqual([j['id'] for j in plan['activity']['jobs']], [job['id']])
        self.assertEqual(plan['workflow'], self.frq.assignment(self.plan['id'])['workflow'])
        with connect(self.path) as db:
            self.assertEqual(self.frq.activity(db, 'another-plan'), {'jobs': [], 'preparations': []})
        self.assertNotIn('activity', self.client.get(f"/api/grading/assignments/{self.plan['id']}").get_json())

    def test_source_reuses_page_reads_but_revalidates_each_question_and_request(self):
        with self.frq.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, self.plan['id'])
            question = self.frq.question(db, plan['id'], self.qid)
            event, participants = self.frq.manual._context(db, plan['event_id'])
            participant, shared, queries = participants[0], {}, []
            db.set_trace_callback(queries.append)
            first = self.frq.source(db, plan, question, participant, event, shared)
            self.assertTrue(queries)
            queries.clear()
            self.assertEqual(self.frq.source(db, plan, question, participant, event, shared), first)
            self.assertEqual(queries, [])
            missing = {**question, 'content': {**question['content'], 'regions': [{'page': 999}]}}
            with self.assertRaisesRegex(Conflict, 'response page is missing'):
                self.frq.source(db, plan, missing, participant, event, shared)
        # A later read must see a changed source review gate, even after the
        # previous read verified exactly the same paper.
        with self.frq.rosters.lock(), connect(self.path) as db:
            db.execute('UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?', (self.job['message_id'],))
            with self.assertRaisesRegex(Conflict, 'Explicitly review'):
                self.frq.source(db, plan, question, participant, event, {})
        # ... and a scan deleted from the Inbox since: the paper's pages are read together, each still PRESENT-filtered.
        with self.frq.rosters.lock(), connect(self.path) as db:
            db.execute('UPDATE inbox_messages SET instructor_review_required=0 WHERE id=?', (self.job['message_id'],))
            self.assertEqual(self.frq.source(db, plan, question, participant, event, {}), first)
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)", (self.job['message_id'], 'workspace', '', '2026-09-29T00:00:00+00:00'))
            shared, queries = {}, []
            db.set_trace_callback(queries.append)
            with self.assertRaisesRegex(Conflict, 'source scan was removed'):
                self.frq.source(db, plan, question, participant, event, shared)
            self.assertEqual(len([sql for sql in queries if 'FROM scan_pages sp' in sql and 'inbox_deletions' in sql]), 1)
            self.assertEqual(set(shared['source_pages'].values()), {None})

    def test_a_pass_over_every_paper_reads_the_exams_assemblies_once_with_the_same_answers(self):
        """PERFORMANCE (2026-10-02): with the caller's `prefetch` opt-in (a listing of every student), `source` reads every assembly
        of the exam and its pages' Inbox state once. Each answer and refusal is what each paper's own reads give."""
        with connect(self.path) as db:
            other = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=other), changed="second student")
        with self.frq.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, self.plan['id'])
            question = self.frq.question(db, plan['id'], self.qid)
            event, participants = self.frq.manual._context(db, plan['event_id'])
            self.assertEqual(len([p for p in participants if p['collection']['assembly_revision_id']]), 2)
            alone = [self.frq.source(db, plan, question, p, event, {}) for p in participants]
            shared, queries = {'prefetch': {}}, []
            db.set_trace_callback(queries.append)
            self.assertEqual(self.frq.source(db, plan, question, participants[0], event, shared), alone[0])
            first = len(queries)
            self.assertEqual(self.frq.source(db, plan, question, participants[1], event, shared), alone[1])
            self.assertEqual(len(queries), first)  # the second paper is already read
            db.set_trace_callback(None)
            # A paper whose assembly is not among the exam's (here: the collection points elsewhere) is read alone and refused alike.
            stray = {**participants[0], 'collection': {**participants[0]['collection'], 'assembly_revision_id': 'not-a-revision'}}
            with self.assertRaisesRegex(Conflict, 'does not belong to this exam'):
                self.frq.source(db, plan, question, stray, event, shared)
            # A scan deleted from the Inbox: the next read keeps its pages as None and refuses that paper, as each paper's read does;
            # the other student's paper is unchanged.
            mine = next(i for i, p in enumerate(participants) if p['collection']['assembly_revision_id'] == self.revision['id'])
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)", (self.job['message_id'], 'workspace', '', '2026-09-29T00:00:00+00:00'))
            shared = {'prefetch': {}}
            with self.assertRaisesRegex(Conflict, 'source scan was removed'):
                self.frq.source(db, plan, question, participants[mine], event, shared)
            self.assertEqual(self.frq.source(db, plan, question, participants[1 - mine], event, shared), alone[1 - mine])
            self.assertEqual({type(value) for value in shared['source_pages'].values()}, {type(None), dict})
            with self.assertRaisesRegex(Conflict, 'source scan was removed'):
                self.frq.source(db, plan, question, participants[mine], event, {})

    def inspected(self, job=None):
        job = job or self.job
        if self.storage:
            self.storage.reconcile_all()
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (job["message_id"],))
        return job["message_id"]

    def arrival(self, authenticated=True):
        content = str(uuid.uuid4()).encode()
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": b"%PDF-synthetic" + content}],
            source="email", sender="scanner", original=b"fixture" + content, idempotency_key=str(uuid.uuid4()),
            authenticated=authenticated, instructor_review_required=True)
        job = self.inbox.get(job["id"])
        artifact = next(a for a in job["artifacts"] if a["role"] == "input")
        self.scans._retain_page(job, artifact, {"index": 1, "pdf": b"fixture-page" + content, "png": b"fixture-image" + content, "decoder": {}, "blank": False, "candidates": []})
        self.inspected(job)
        return job

    def command(self, message, **extra):
        return {"request_id": str(uuid.uuid4()), "fingerprint": self.frq.scan_context(message)["fingerprint"],
                "term_id": self.plan["term_id"], "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]], **extra}

    def test_recognized_scans_join_existing_plan_without_review_or_grading(self):
        message = self.inspected()
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?", (message,))
        self.assertTrue(self.frq.sync_scan(message))
        self.assertFalse(self.frq.sync_scan(message))
        state = self.frq.scan_context(message)
        self.assertEqual(state["plan"]["id"], self.plan["id"])
        self.assertTrue(state["review_required"])
        self.assertEqual(state["source"], "automatic")
        second, _ = self.reviewed_source(changed="later")
        self.assertTrue(self.frq.sync_scan(self.inspected(second)))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_assignments").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_jobs").fetchone()[0], 0)
        w = self.frq.assignment(self.plan["id"])["workflow"]
        self.assertEqual(len(w["scans"]), 2)
        self.assertEqual(w["awaiting_review"], 1)

    def test_unknown_assignment_create_is_atomic_and_idempotent(self):
        job = self.arrival(); message = job["message_id"]
        self.assertFalse(self.frq.sync_scan(message))
        command = self.command(message, assignment_id=None, new_title="Quiz 2")
        result = self.frq.associate_scan(message, command)
        self.assertEqual(result["plan"]["title"], "Quiz 2")
        self.assertTrue(result["review_required"])
        self.assertEqual(self.frq.associate_scan(message, command)["plan"], result["plan"])
        with self.assertRaises(Conflict):
            self.frq.associate_scan(message, {**command, "new_title": "Different"})
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT page_stamp_id FROM scan_pages WHERE message_id=?", (message,)).fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE title='Quiz 2'").fetchone()[0], 1)

    def test_later_recovered_labels_attach_paper_to_the_same_item(self):
        job = self.arrival(); message = job["message_id"]
        self.frq.associate_scan(message, self.command(message))
        batch = self.documents.create_batch(self.version_id, self.section["id"], 1, str(uuid.uuid4()), students=[self.enrollment])
        with connect(self.path) as db:
            db.execute("UPDATE page_stamps SET state='issued' WHERE printed_copy_id=?", (batch["copies"][0]["id"],))
            db.execute("UPDATE print_batches SET status='ready',output_path='print.pdf',output_sha256=?,manifest_path='manifest.json',manifest_sha256=? WHERE id=?",
                       ("c" * 64, "d" * 64, batch["id"]))
            stamp = db.execute("SELECT id FROM page_stamps WHERE printed_copy_id=? AND page_number=1", (batch["copies"][0]["id"],)).fetchone()[0]
            db.execute("UPDATE scan_pages SET page_stamp_id=?,status='manual' WHERE message_id=?", (stamp, message))
        self.assertTrue(self.frq.sync_scan(message))
        self.assertEqual(self.frq.scan_context(message)["plan"]["id"], self.plan["id"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT event_id FROM exam_event_batches WHERE batch_id=?", (batch["id"],)).fetchone()[0], self.event["id"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_scan_links WHERE message_id=?", (message,)).fetchone()[0], 2)

    def test_invalid_sections_roll_back_new_assignment(self):
        message = self.arrival()["message_id"]
        with self.assertRaises(ValueError):
            self.frq.associate_scan(message, self.command(message, assignment_id=None, new_title="Must roll back", section_ids=["bad"]))
        with connect(self.path) as db:
            self.assertFalse(db.execute("SELECT 1 FROM assignments WHERE title='Must roll back'").fetchone())

    def test_explicit_review_handoff_preserves_identity_blockers(self):
        message = self.arrival()["message_id"]
        state = self.frq.associate_scan(message, self.command(message))
        response = self.client.post(f"/api/grading/scans/{message}/review", json={"fingerprint": state["fingerprint"], "confirm_review": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn('Finish the steps for the whole scan', response.json['error'])
        blockers = self.frq.scan_context(message)['review_blockers']
        self.assertTrue(blockers)
        for blocker in blockers:  # Every open step for the whole scan is named, not only the first.
            self.assertIn(blocker, response.json['error'])
        self.assertTrue(self.frq.scan_context(message)['review_required'])
        self.assertEqual(self.frq.scan_context(message)['href'], f"#inbox/job/{state['job_id']}")
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT page_stamp_id FROM scan_pages WHERE message_id=?", (message,)).fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_jobs").fetchone()[0], 0)

    def test_only_cleared_associated_scans_enter_grading(self):
        message = self.inspected()
        with connect(self.path) as db:
            db.execute('UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?', (message,))
        self.frq.sync_scan(message)
        overview = self.frq.overview(include_health=False)
        self.assertEqual(overview['assignments'], [])
        # The assignment waiting for its scan's scan checks is named (read-only), with its course, term and the scan to open, and
        # (additive, I2 2026-10-18) its course-wide assignment and term, for the link to its Collect's waiting scans.
        plan = self.frq.assignment(self.plan['id'])
        self.assertEqual(overview['waiting'], [{'id': self.plan['id'], 'title': plan['title'], 'code': plan['code'], 'term': plan['term'],
                                                'term_other': plan.get('term_other') or '', 'year': plan['year'],
                                                'href': self.frq.scan_context(message)['href'], 'scans': 1,
                                                'assignment_id': plan['source_assignment_id'], 'term_id': plan['term_id']}])
        self.assertEqual(self.frq.overview(navigation=True, include_health=False)['waiting'], overview['waiting'])
        state = self.frq.scan_context(message)
        self.assertFalse(state['blockers'])
        result = self.frq.review_scan(message, {'fingerprint': state['fingerprint'], 'confirm_review': True})
        self.assertTrue(result['ready'])
        self.assertEqual(result['href'], f"#grading/{self.plan['id']}")
        self.assertEqual(len(self.frq.overview(include_health=False)['assignments']), 1)
        self.assertEqual(self.frq.overview(include_health=False)['waiting'], [])
        # Later unresolved arrivals stay in the Inbox without hiding prior work.
        arrival = self.arrival()
        self.frq.associate_scan(arrival['message_id'], self.command(arrival['message_id']))
        self.assertEqual(len(self.frq.overview(include_health=False)['assignments']), 1)
        self.assertFalse(self.frq.scan_context(arrival['message_id'])['ready'])

    def test_scan_with_nothing_to_check_passes_review_automatically(self):
        # Requirement, 2026-09-27: a scan whose only open step is the review itself passes it without a click.
        # 2026-09-28: a page flagged on a scan no longer holds the rest of it back; only steps for the whole scan and the worker's
        # own reading on it do.
        flagged = self.arrival()["message_id"]
        self.frq.associate_scan(flagged, self.command(flagged))
        self.checked_for_sheets(flagged)
        held = self.arrival()["message_id"]
        self.frq.associate_scan(held, self.command(held))
        with connect(self.path) as db:
            page = db.execute("SELECT id FROM scan_pages WHERE message_id=?", (held,)).fetchone()[0]
            db.execute("INSERT INTO scan_alias_ocr(scan_page_id,message_id,status,created_at,updated_at) VALUES(?,?,'pending','t','t')", (page, held))
        message = self.inspected()
        with connect(self.path) as db:
            db.execute('UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?', (message,))
        self.frq.sync_scan(message)
        self.assertTrue(self.frq.auto_review_one())
        state = self.frq.scan_context(message)
        self.assertTrue(state['ready'])
        with connect(self.path) as db:
            review = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                              WHERE j.message_id=? AND e.kind='reviewed'""", (message,)).fetchone()[0])
            overview = self.frq.exams.scans.overview(db, message)
        self.assertEqual(review['by'], 'automatic')
        self.assertEqual(next(s for s in overview['stages'] if s['key'] == 'review')['detail'], 'Passed automatically: nothing needed checking.')
        # A page nothing identifies is a flag on that page: the scan passes, and the page stays open in the Inbox for the instructor.
        before = self.frq.scan_context(flagged)
        self.assertTrue(before['blockers'])
        self.assertEqual(before['review_blockers'], [])
        self.assertTrue(self.frq.auto_review_one())
        after = self.frq.scan_context(flagged)
        self.assertTrue(after['ready'])
        self.assertEqual(after['blockers'], before['blockers'])
        with connect(self.path) as db:
            left = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                            WHERE j.message_id=? AND e.kind='review_exceptions'""", (flagged,)).fetchone()[0])
            overview = self.frq.exams.scans.overview(db, flagged)
        self.assertEqual((left['actor'], left['left']), ('automatic', before['blockers']))
        self.assertEqual(next(s for s in overview['stages'] if s['key'] == 'review')['detail'], 'Passed automatically: flagged papers wait in their own stages.')
        self.assertIn(overview['state'], ('todo', 'check'))  # Still listed under Current until the instructor settles it.
        # The worker still reading the scan (a label read in progress) holds the review back, and it is looked at again after a pause.
        self.assertTrue(any(s.get('progress') for s in self.overview_steps(held)))
        self.assertFalse(self.frq.auto_review_one())
        self.assertTrue(self.frq.scan_context(held)['review_required'])
        # Only a scan's review may be automatic; every other job still needs the instructor.
        with self.assertRaises(Forbidden):
            job = self.inbox.get(self.frq.scan_context(held)['job_id'])
            with connect(self.path) as db:
                db.execute("UPDATE inbox_jobs SET action='roster.sync' WHERE id=?", (job['id'],))
            self.inbox.transition(job['id'], 'review', job['version'] , {}, actor='automatic')

    def checked_for_sheets(self, message):
        """The worker looked for bubble sheets on the scan and found none, as it does right after inspection."""
        from bubble_sheets.service import DETECTOR
        with connect(self.path) as db:
            db.executemany("INSERT INTO bubble_detections VALUES(?,?,?,'other','{}','t')",
                           [(row[0], DETECTOR, message) for row in db.execute("SELECT id FROM scan_pages WHERE message_id=?", (message,))])

    def test_bubble_sheets_on_a_scan_its_labels_associated_are_read_for_that_assignment(self):
        # 2026-10-24: Exam 2 scanned at home in stacks, each student's bubble sheet beside their booklet. The booklets' labels
        # associate the scan, so its sheets are read for that assignment without asking whose they are, once.
        from bubble_sheets.service import DETECTOR
        finder = self.frq.exams.scans.assignment_finder
        message = self.arrival()["message_id"]
        self.frq.associate_scan(message, self.command(message))
        with connect(self.path) as db:
            db.executemany("INSERT INTO bubble_detections VALUES(?,?,?,'sheet','{}','t')",
                           [(row[0], DETECTOR, message) for row in db.execute("SELECT id FROM scan_pages WHERE message_id=?", (message,))])
        while finder.process_one():  # other scans of the fixture are looked at too
            pass
        with connect(self.path) as db:
            run = db.execute("SELECT assignment_id,actor FROM bubble_runs WHERE message_id=?", (message,)).fetchone()
            event = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                             WHERE j.message_id=? AND e.kind='linked_sheets_read'""", (message,)).fetchone()[0])
        self.assertEqual(tuple(run), (self.assignment["id"], "automatic"))
        self.assertEqual((event["read"], event["notes"]), (True, []))
        # A Scantron label of another assignment on the sheets leaves the choice to the instructor, and says why.
        content = str(uuid.uuid4()).encode()
        job = self.inbox.get(self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": b"%PDF-synthetic" + content}], source="email",
                                               sender="scanner", original=b"fixture" + content, idempotency_key=str(uuid.uuid4()), authenticated=True,
                                               instructor_review_required=True)["id"])
        self.scans._retain_page(job, next(a for a in job["artifacts"] if a["role"] == "input"),
                                {"index": 1, "pdf": b"fixture-page" + content, "png": b"fixture-image" + content, "decoder": {}, "blank": False,
                                 "candidates": [{"payload": "T1-ANOTHERASSIGNMENTSLABEL0"}]})
        other = self.inspected(job)
        self.frq.associate_scan(other, self.command(other))
        with patch("document_identity.service.DocumentService.sheet_label", return_value={"assignment_id": "another-assignment"}), connect(self.path) as db:
            db.executemany("INSERT INTO bubble_detections VALUES(?,?,?,'sheet','{}','t')",
                           [(row[0], DETECTOR, other) for row in db.execute("SELECT id FROM scan_pages WHERE message_id=?", (other,))])
            db.commit()
            self.assertTrue(finder.process_one())
            self.assertFalse(finder.process_one())  # once only
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM bubble_runs WHERE message_id=?", (other,)).fetchone())
            event = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                             WHERE j.message_id=? AND e.kind='linked_sheets_read'""", (other,)).fetchone()[0])
        self.assertEqual((event["read"], event["notes"]), (False, ["A Scantron label on these sheets names another assignment; choose whose sheets these are."]))

    def overview_steps(self, message):
        with connect(self.path) as db:
            return self.frq.exams.scans.overview(db, message)['steps']

    def test_an_accepted_partial_paper_clears_its_scan(self):
        # bugs-backend-1: a paper the instructor accepted with a page missing is settled. It used to keep its scan flagged for good
        # ("1 copy has pages that no scan has shown with a label"): filed as needing review, awaiting review in the grading workflow
        # and blocking certification (inbox_pending), with nothing on any page that could clear it.
        with connect(self.path) as db:
            other = [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? ORDER BY enrollment_id", (self.section["id"],))][1]
        batch = self.ready_batch(enrollment_id=other)
        job, revision = self.reviewed_source(pages=(1,), batch=batch, changed="partial")  # page 2 of 2 never arrived; partial accepted
        message = job["message_id"]
        self.frq.sync_scan(message)
        with self.frq.rosters.lock(), connect(self.path) as db:
            self.assertEqual(db.execute("SELECT partial FROM assembly_revisions WHERE id=?", (revision["id"],)).fetchone()[0], 1)
            self.assertEqual(self.frq.scan_blockers(db, message), [])
            row = dict(db.execute("SELECT id,status,action,message_id,0 gone FROM inbox_jobs WHERE message_id=?", (message,)).fetchone())
            self.assertEqual(self.inbox.filed(db, [row])[job["id"]][0], "completed")
        plan = next(p for p in self.frq.overview(include_health=False)["assignments"] if p["id"] == self.plan["id"])
        self.assertFalse(plan["workflow"]["awaiting_review"])
        checks = self.app.extensions["certification"].view(self.plan["id"]).get("checks") or []
        self.assertEqual([check for check in checks if check["id"] == "inbox_pending"], [])

    def test_stale_review_authentication_and_label_mismatch_refused(self):
        message = self.inspected()
        command = self.command(message)
        other = self.documents.create_assignment(self.assignment["definition_id"], "different", "Different")
        with self.assertRaises(Conflict):
            self.frq.associate_scan(message, {**command, "assignment_id": other["id"]})
        self.frq.sync_scan(message)
        with self.assertRaises(Conflict):
            self.frq.review_scan(message, {"fingerprint": command["fingerprint"], "confirm_review": True})
        unauthenticated = self.arrival(authenticated=False)["message_id"]
        with self.assertRaises(Forbidden):
            self.frq.associate_scan(unauthenticated, self.command(unauthenticated))

    def test_deleted_scans_do_not_count_and_restore_returns_same_item(self):
        job = self.arrival(); message = job["message_id"]
        self.frq.associate_scan(message, self.command(message))
        before = self.frq.assignment(self.plan["id"])["workflow"]
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete")
        after = self.frq.assignment(self.plan["id"])["workflow"]
        self.assertEqual(len(before["scans"]) - len(after["scans"]), 1)
        with self.assertRaises((Conflict, NotFound)):
            self.frq.associate_scan(message, self.command(message))
        self.inbox.restore(job["id"])
        self.assertEqual(len(self.frq.assignment(self.plan["id"])["workflow"]["scans"]), len(before["scans"]))

    def test_same_course_assignment_in_other_term_is_a_separate_item(self):
        result = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Spring", "year": 2027, "sections": [501], "is_current": True}, headers=HEADERS)
        self.assertEqual(result.status_code, 201)
        if self.storage:
            self.storage.reconcile_all()
        with connect(self.path) as db:
            section = db.execute("SELECT s.id,s.term_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE t.year=2027").fetchone()
        message = self.arrival()["message_id"]
        state = self.frq.associate_scan(message, self.command(message, term_id=section["term_id"], section_ids=[section["id"]]))
        self.assertNotEqual(state["plan"]["id"], self.plan["id"])
        self.assertEqual(state["plan"]["source_assignment_id"], self.plan["source_assignment_id"])
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["scans"], [])

    def test_new_only_skips_inflight_and_finished_responses(self):
        preview = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "selection": "new"})
        self.assertEqual(len(preview["eligible"]), 1)
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["new_responses"], 1)
        job, _ = self.queue(selection="new")
        waiting = self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "selection": "new"})
        self.assertEqual(waiting["eligible"], [])
        self.assertEqual(waiting["existing_count"], 1)
        self.finish(job)
        self.assertEqual(self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "selection": "new"})["eligible"], [])
        self.assertEqual(len(self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "selection": "all"})["eligible"]), 1)

    def test_tile_setup_projection_and_current_export(self):
        workflow = self.frq.overview()["assignments"][0]["workflow"]
        question = workflow["setup"]["questions"][0]
        self.assertEqual(question["question_id"], self.qid)
        self.assertTrue(question["rubric"]["approved_at"])
        self.assertNotIn("rules", question["rubric"])
        self.assertNotIn("regions", question["content"])
        self.assertFalse(workflow["export"]["current"])
        self.assertEqual(workflow["certification"], {"state": "none", "id": None, "created_at": None, "changed": [], "gradebook": None, "left_out": 0, "left_out_dr": 0})
        job, _ = self.queue()
        response = self.finish(job)
        self.approve(response)
        self.frq.export(self.plan["id"], "csv")
        self.assertTrue(self.frq.assignment(self.plan["id"])["workflow"]["export"]["current"])
        # A later decision invalidates the snapshot even when counts are unchanged.
        self.approve(self.frq.response(response["id"]), selected_items=[])
        self.assertFalse(self.frq.assignment(self.plan["id"])["workflow"]["export"]["current"])
        dataset, _, _ = self.frq.export(self.plan["id"], "dataset")
        dataset.close()
        self.assertFalse(self.frq.assignment(self.plan["id"])["workflow"]["export"]["current"])
        self.frq.export(self.plan["id"], "csv")
        self.assertTrue(self.frq.assignment(self.plan["id"])["workflow"]["export"]["current"])

    def test_progress_tracks_current_responses_reviews_regrades_and_retries(self):
        def progress():
            return self.frq.assignment(self.plan['id'])['workflow']['progress']

        empty = {'completed': 0, 'total': 1}
        complete = {'completed': 1, 'total': 1}
        self.assertEqual(progress(), {'grade': empty, 'review': empty})
        job, _ = self.queue()
        self.assertEqual(progress()['grade'], empty)
        response = self.finish(job)
        self.assertEqual(progress(), {'grade': complete, 'review': empty})
        # A new grading run must not borrow completion from the older result.
        job, _ = self.queue(selection='all')
        self.assertEqual(progress()['grade'], empty)
        self.fake.fail = True
        for _ in range(20):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job['id'])['status'], 'failed')
        self.assertEqual(progress()['grade'], empty)
        self.fake.fail = False
        self.frq.retry_job(job['id'])
        self.assertEqual(progress()['grade'], empty)
        response = self.finish(job)
        self.assertEqual(progress(), {'grade': complete, 'review': empty})
        self.approve(response)
        self.assertEqual(progress(), {'grade': complete, 'review': complete})
        # Read-only reload and the compact landing API agree; historical attempts
        # never add to the denominator or undo the latest successful retry.
        self.assertEqual(self.frq.overview()['assignments'][0]['workflow']['progress'], progress())
        # A changed paper invalidates processing and final review together.
        self.reviewed_source(changed='new scan')
        self.assertEqual(progress(), {'grade': empty, 'review': empty})

    def test_pending_arrivals_are_inbox_work_until_sources_clear_review(self):
        # The plan may have setup while its scan still needs Inbox approval.
        message = self.inspected()
        with connect(self.path) as db:
            db.execute('UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?', (message,))
        self.frq.sync_scan(message)
        workflow = self.frq.assignment(self.plan['id'])['workflow']
        self.assertGreater(workflow['blocked_responses'], 0)
        self.assertEqual(workflow['progress']['grade'], {'completed': 0, 'total': 0})
        self.assertNotEqual(workflow['next_tab'], 'papers')
        self.assertEqual(self.frq.overview(include_health=False)['assignments'], [])
        preview = self.frq.preview(self.plan['id'], {'question_ids': [self.qid], 'selection': 'new'})
        self.assertEqual(preview['eligible'], [])
        state = self.frq.scan_context(message)
        self.frq.review_scan(message, {'fingerprint': state['fingerprint'], 'confirm_review': True})
        workflow = self.frq.assignment(self.plan['id'])['workflow']
        self.assertEqual(workflow['new_responses'], 1)
        self.assertEqual(workflow['progress']['grade'], {'completed': 0, 'total': 1})
        self.assertEqual(len(self.frq.overview(include_health=False)['assignments']), 1)
        response = self.finish(self.queue()[0])
        self.approve(response)
        arrival = self.arrival()
        self.frq.associate_scan(arrival['message_id'], self.command(arrival['message_id']))
        workflow = self.frq.assignment(self.plan['id'])['workflow']
        self.assertEqual(workflow['awaiting_review'], 1)
        self.assertEqual(workflow['next_tab'], 'export')
        self.assertEqual(workflow['progress'], {
            'grade': {'completed': 1, 'total': 1}, 'review': {'completed': 1, 'total': 1}})
        self.assertTrue(self.inbox.get(arrival['id'])['instructor_review_required'])

    def test_tile_scan_failures_follow_current_visible_arrivals(self):
        job = self.arrival()
        self.frq.associate_scan(job["message_id"], self.command(job["message_id"]))
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='failed' WHERE message_id=?", (job["message_id"],))
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["scans"][0]["inspection_status"], "failed")
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete")
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["scans"], [])

    def test_tile_preparation_status_uses_current_question_and_latest_attempt(self):
        question = self.frq.assignment(self.plan["id"])["questions"][0]
        payload = {"question_id": self.qid, "question_version": question["version"]}
        old = self.frq.queue_preparation(self.plan["id"], "compile", payload, str(uuid.uuid4()))
        with connect(self.path) as db:
            db.execute("UPDATE frq_preparations SET status='failed' WHERE id=?", (old["id"],))
        def status():
            return self.frq.assignment(self.plan["id"])["workflow"]["setup"]["preparation"]
        self.assertEqual(status(), {"pending": 0, "failed": 1})
        self.frq.queue_preparation(self.plan["id"], "compile", payload, str(uuid.uuid4()))
        self.assertEqual(status(), {"pending": 1, "failed": 0})
        # An edit supersedes the old question version; its failures cannot stay red forever.
        with connect(self.path) as db:
            db.execute("UPDATE frq_assignment_questions SET version=version+1 WHERE assignment_id=? AND question_id=?", (self.plan["id"], self.qid))
        self.assertEqual(status(), {"pending": 0, "failed": 0})


class ManagedGradingIntakeTests(GradingIntakeTests):
    managed = True
