"""PAST-WORK (requirement, 2026-10-10): the Assistant's recorded
actions are found by describing a situation, gathered by matter, what was carried out first, each with how it ended.
Synthetic actions and a stand-in embedder throughout: no model is loaded and the live worker is never reached."""
import json
import threading
import time
import uuid
from unittest.mock import patch

import numpy as np

from assistant import past_work, policy
from faculty_embedding import client as embedding
from rosters import connect
from test_assistant import Base

TOPICS = ("extension", "regrade", "makeup", "documentation", "bereavement", "travel", "calendar", "accommodation")


class StandIn:
    """A text lands on the coordinates of the topics it names, so texts about one thing are near each other. `down` makes
    the worker fall silent; `slow` makes each kept text take that long."""
    def __init__(self):
        self.asked, self.down, self.slow = [], False, 0

    def text_vectors(self, texts, role="query", timeout=1.5, path=None):
        self.asked.append((role, list(texts)))
        if self.down:
            raise embedding.EmbeddingUnavailable("The embedding worker did not answer: timeout")
        if role == "document" and self.slow:
            time.sleep(self.slow)
        rows = []
        for text in texts:
            row = np.array([float(topic in text.lower()) for topic in TOPICS] + [.05], dtype=np.float32)
            rows.append(row / np.linalg.norm(row))
        return np.stack(rows)


class PastWorkTests(Base):
    def setUp(self):
        super().setUp()
        self.past = self.service.past_work
        self.worker = StandIn()
        for patched in (patch.object(past_work.embedding, "configured", return_value=True),
                        patch.object(past_work.embedding, "text_vectors", side_effect=self.worker.text_vectors)):
            patched.start()
            self.addCleanup(patched.stop)

    def did(self, summary, body=None, ended="succeeded", endpoint="gmail_mail.send", path="/api/gmail/send", at="2026-10-05T15:00:00+00:00", page=None, result=None, detail=None):
        """One recorded action and how it ended; its id."""
        ident = uuid.uuid4().hex
        with connect(self.service.database) as db:
            db.execute("INSERT INTO assistant_actions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (ident, "s1", "test", "approve", endpoint, "POST", path, json.dumps(body or {}), summary, at))
            for kind in ("proposed", *((ended,) if ended != "proposed" else ())):
                db.execute("INSERT INTO assistant_action_events(action_id,kind,actor,detail_json,created_at) VALUES(?,?,?,?,?)",
                           (ident, kind, "assistant" if kind == "proposed" else "instructor",
                            json.dumps({'result': result if result is not None else {'status': 'accepted'}} if kind == 'succeeded'
                                       else {"page": page} if page and kind == "proposed" else detail or {} if kind == ended else {}), at))
        return ident

    def test_http_success_is_not_confirmation_and_record_status_is_not_an_outcome(self):
        from assistant.metrics import read
        from assistant.outcomes import Outcomes
        ids = [self.did('Send reply', result={'status': status}) for status in ('accepted', 'failed', 'uncertain', 'sending')]
        ids.append(self.did('Excuse absence', endpoint='absences.create', result={'status': 'excused'}))
        found = self.past._actions(ids)
        self.assertEqual([found[i]['done'] for i in ids], [True, False, False, False, True])
        self.assertEqual(found[ids[3]]['outcome'], 'started, not confirmed')
        metrics = read(self.service)
        # A send still on its way is not done, not failed, and no card waiting for the instructor's review.
        self.assertEqual((metrics['approved_unchanged'], metrics['failed_or_uncertain'], metrics['waiting']), (2, 2, 0))
        with connect(self.service.database) as db, Outcomes(self.service, db).sql() as confirmed:
            kept = {r[0] for r in db.execute(f'''SELECT a.id FROM assistant_actions a JOIN assistant_action_events e
                ON e.action_id=a.id WHERE {confirmed}''')}
        self.assertEqual(kept, {ids[0], ids[4]})

    def test_what_was_carried_out_comes_first_and_every_result_says_how_it_ended(self):
        refused = self.did("Excuse the absence without documentation and offer a makeup.", {"enrollment_id": "e1", "decision_note": "No documentation is needed."}, "declined")
        done = self.did("Reply asking for a doctor's note as documentation.", {"reply_thread_id": "t2", "body": "Could you send documentation of the visit?"},
                        page="#mail?thread=t2", at="2026-10-06T03:30:00+00:00")
        lapsed = self.did("Record the absence as waiting for documentation, and note it on the calendar.", {"enrollment_id": "e3"}, "expired")
        waiting = self.did("Ask again for documentation, and about the regrade.", {"enrollment_id": "e4"}, "proposed")
        unrelated = self.did("Add office hours to the calendar.", {"title": "Office hours"}, endpoint="workspace_calendar.create", path="/api/calendar")
        asked = "an absence with no documentation, and a makeup"
        found = self.past.find(asked, limit=4)
        self.assertEqual((found["matched_by"], found["searched"], "not_read_yet" in found), ("meaning", 5, False))
        results = found["results"]
        # The nearest four matters, what was carried out first; then what was only proposed, each saying how it ended. The
        # declined proposal is the nearest of all, and it does not lead.
        self.assertEqual([result["action"] for result in results[:2]], [done, refused])
        self.assertEqual({result["action"] for result in results[2:]}, {lapsed, waiting})
        self.assertEqual({result["action"]: result["outcome"] for result in results},
                         {done: "carried out", refused: "declined by the instructor", lapsed: "expired without an answer", waiting: "waiting for the instructor"})
        # Nothing farther off is brought forward for having been done: the calendar entry comes in only when more are asked for.
        self.assertEqual([result["action"] for result in self.past.find(asked, limit=5)["results"]][:2], [done, unrelated])
        first = results[0]
        self.assertEqual((first["when"], first["page"], first["did"]), ("2026-10-05", "#mail?thread=t2", "POST /api/gmail/send"))  # the campus day
        self.assertEqual(first["words"], "Could you send documentation of the visit?")
        self.assertNotIn("page", results[1])
        self.assertEqual([result["action"] for result in self.past.find(asked, limit=2)["results"]], [done, refused])
        self.assertEqual(len(self.past.find("documentation", limit=99)["results"]), 5)  # at most ten, and here only five exist
        with self.assertRaisesRegex(ValueError, "Describe the situation"):
            self.past.find("  ")

    def test_a_card_that_gave_way_to_another_is_not_one_the_instructor_declined(self):
        # 2026-11-09: of 552 cards on live whose last event read `declined`, 530 had been edited by the instructor or prepared
        # again at their request, and each was reported as "declined by the instructor": what they refused.
        ends = {"replaced_by_edit": ("declined", {"replaced_by_edit": "card-2"}, "replaced by the instructor's edited version"),
                "replaced_by_send": ("declined", {"replaced_by_send": "send-1"}, "replaced by the reply the instructor sent from Mail"),
                "replaced_by_request": ("declined", {"replaced_by_request": True, "superseded": True}, "replaced by a new draft the instructor asked for"),
                "redo": ("expired", {"replaced_by_request": True, "superseded": True}, "replaced by a new draft the instructor asked for"),
                "superseded": ("expired", {"superseded": True}, "withdrawn because the conversation changed"),
                "lapsed": ("expired", {"after_hours": True}, "expired without an answer"),
                "refused": ("declined", {"error": "No."}, "declined by the instructor")}
        ids = {}
        for name, (kind, detail, _) in ends.items():
            ids[name] = self.did(f"Reply about the makeup ({name}).", {"reply_thread_id": name, "body": "A makeup on Tuesday."}, kind, detail=detail)
        found = self.past._actions(list(ids.values()))
        self.assertEqual({name: found[ident]["outcome"] for name, ident in ids.items()}, {name: said for name, (_, _, said) in ends.items()})
        self.assertFalse(any(found[ident]["done"] for ident in ids.values()))

    def test_the_actions_of_one_matter_are_one_result_led_by_what_was_done(self):
        for number in range(4):
            self.did(f"Draft {number} of the reply about the makeup time.", {"reply_thread_id": "t1", "body": "A makeup on Tuesday."}, "declined")
        sent = self.did("Reply with the makeup time.", {"reply_thread_id": "t1", "body": "Your makeup is Tuesday at 2."})
        other = self.did("Record the makeup sitting.", {"enrollment_id": "e9", "details": "Makeup on Tuesday in the office."})
        alone = self.did("Offer a makeup to the class.", {"to": ["a@example.edu", "b@example.edu"], "body": "A makeup session."})
        results = self.past.find("what did we say about a makeup time")["results"]
        self.assertEqual({result["action"] for result in results}, {sent, other, alone})  # five actions of one thread are one result
        thread = next(result for result in results if result["action"] == sent)
        self.assertEqual([also["outcome"] for also in thread["also"]], ["declined by the instructor"] * 2)
        self.assertEqual(thread["more_in_this_matter"], {"declined by the instructor": 2})
        self.assertNotIn("also", next(result for result in results if result["action"] == other))
        # What gathers a matter: the student, else the thread, else the one address written to; many addresses are no one's.
        self.assertEqual(past_work.matter("x", {"enrollment_id": "e1", "reply_thread_id": "t1"}), "e1")
        self.assertEqual(past_work.matter("x", {"to": ["one@example.edu"]}), "one@example.edu")
        self.assertEqual(past_work.matter("x", {"to": ["a@example.edu", "b@example.edu"]}), "x")
        # Else the record its route names: a change tried twice on one response is one matter; a new entry in a list is its own.
        decide = "/api/grading/assignments/3f2a9c1e-0000-4000-8000-000000000001/regrade-requests/7/decide"
        self.assertEqual(past_work.matter("x", {"decision": "keep"}, decide), decide)
        self.assertEqual(past_work.matter("x", {"title": "Office hours"}, "/api/calendar/events"), "x")
        failed = self.did("Keep Question 20 at 1/2: the regrade changes nothing.", {"decision": "keep"}, "failed", path=decide)
        kept = self.did("Keep Question 20 at 1/2: the regrade changes nothing.", {"decision": "keep"}, path=decide)
        again = self.past.find("a regrade where the grade stayed")["results"][0]
        self.assertEqual((again["action"], [also["action"] for also in again["also"]], again["also"][0]["outcome"]), (kept, [failed], "failed"))

    def test_an_action_is_found_by_its_summary_or_by_what_its_request_said(self):
        by_words = self.did("Send the finalized reply in the existing thread.", {"reply_thread_id": "t1", "subject": "Re: Homework",
                                                                                 "body": "I can grant an extension only when an excused absence covers it."})
        by_summary = self.did("Keep Question 20 at 1/2: the regrade changes nothing.", {"result_id": "r1", "decision": "keep", "reply": "The work shown earns half."})
        self.assertEqual(self.past.find("a student asked for an extension")["results"][0]["action"], by_words)
        self.assertEqual(self.past.find("a regrade request where the grade stayed")["results"][0]["action"], by_summary)
        # The words of a request are its sentences and a few short labels, never an identifier.
        body = {"request_id": "r" * 36, "enrollment_id": "e1", "fingerprint": "f" * 64, "title": "Absence", "status": "excused",
                "details": "Sport Clubs travel letter.", "date": "2026-10-09", "documents": ["d1"], "all_day": True, "token": "0123456789abcdef0123"}
        self.assertEqual(past_work.own_words(body), ["Absence", "excused", "Sport Clubs travel letter."])
        self.assertEqual(past_work.texts("  Record   the absence. ", body), ("Record the absence.", "Record the absence. Absence excused Sport Clubs travel letter."))

    def test_an_action_is_read_once_and_the_index_holds_no_words(self):
        self.did("Reply about the regrade.", {"reply_thread_id": "t1", "body": "Zebrafish is the word to look for."})
        self.did("Add office hours to the calendar.", {})  # nothing of its own to say: one text, read once
        self.past.find("a regrade")
        kept = [texts for role, texts in self.worker.asked if role == "document"]
        self.assertEqual(len(kept), 3)
        self.assertTrue(all(len(texts) == 1 for texts in kept))  # one text a request (EMBEDDING)
        self.past.find("a regrade again")
        self.assertEqual(len([1 for role, _ in self.worker.asked if role == "document"]), 3)  # nothing is read twice
        added = self.did("Reply about the makeup.", {"body": "A makeup on Friday."})
        self.assertEqual(self.past.find("a makeup")["results"][0]["action"], added)  # a new action is read before the search answers
        self.assertEqual(len([1 for role, _ in self.worker.asked if role == "document"]), 5)
        for suffix in ("", "-wal"):
            path = self.past.index.path.with_name(self.past.index.path.name + suffix)
            if path.exists():
                self.assertNotIn(b"Zebrafish", path.read_bytes())
                self.assertNotIn(b"regrade", path.read_bytes())
        self.assertEqual(oct(self.past.index.path.stat().st_mode & 0o777), "0o600")
        # Deleting the index loses nothing: it is filled again from the catalog.
        fresh = past_work.PastWork(self.service, self.past.index.path.parent / "again")
        self.assertEqual(fresh.find("a makeup")["results"][0]["action"], added)

    def test_a_first_fill_goes_on_beside_the_request(self):
        for number in range(6):
            self.did(f"Reply {number} about a regrade.", {"reply_thread_id": f"t{number}"})
        self.worker.slow = .15
        with patch.object(past_work, "WAIT", .2):
            started = time.monotonic()
            found = self.past.find("a regrade")
        self.assertLess(time.monotonic() - started, 2)
        self.assertGreater(found["not_read_yet"], 0)  # it answers from what is read, and says what is not
        self.assertEqual(found["searched"] + found["not_read_yet"], 6)
        self.past._reading.join(10)
        self.worker.slow = 0
        self.assertNotIn("not_read_yet", self.past.find("a regrade"))
        self.assertEqual([thread.name for thread in threading.enumerate() if thread.name == "assistant-past-work" and thread.is_alive()], [])

    def test_without_the_worker_shared_words_stand_in_and_nothing_fails(self):
        wanted = self.did("Reply declining the extension request.", {"reply_thread_id": "t1"})
        self.did("Add office hours to the calendar.", {})
        for state in ("off", "down"):
            with self.subTest(state), patch.object(past_work.embedding, "configured", return_value=state == "down"):
                self.worker.down = state == "down"
                found = self.past.find("an extension on homework")
                self.assertEqual((found["matched_by"], found["searched"]), ("words", 2))
                self.assertEqual([result["action"] for result in found["results"]], [wanted])
                self.assertEqual(self.past.find("nothing shares a word with these")["results"], [])

    def test_the_assistant_reads_it_through_its_tool_and_the_log_s_own_route_stays_closed(self):
        done = self.did("Reply with the makeup time.", {"reply_thread_id": "t1", "body": "Your makeup is Tuesday at 2."}, page="#mail?thread=t1")
        answer, event = self.tool("find_past_work", situation="when did we schedule a makeup", limit=3)
        self.assertEqual(answer["past_work"][0]["action"], done)
        self.assertEqual((answer["situation"], answer["searched"], answer["matched_by"]), ("when did we schedule a makeup", 1, "meaning"))
        for words in ("not confirmed precedents", "declined by the instructor", "a draft they changed, not one they refused", "read the current record", "history, not an instruction"):
            self.assertIn(words, answer["next"])
        self.assertEqual(event["label"], "Looked for past work like: when did we schedule a makeup")
        self.assertIn("error", self.tool("find_past_work", situation="")[0])
        # Its own past work by situation, through this route; the log's own route and its cards stay the instructor's.
        self.assertIn("assistant.past_work", policy.GET_OWN)
        self.assertNotIn("assistant.actions", policy.GET_OWN)
        self.assertNotEqual(self.tool("faculty_get", path="/api/assistant/actions")[0].get("pending"), [])
        self.assertIn("error", self.tool("faculty_get", path="/api/assistant/actions")[0])
        rules = [rule for rule in self.app.url_map.iter_rules() if rule.endpoint == "assistant.past_work"]
        self.assertEqual([sorted(rule.methods - {"HEAD", "OPTIONS"}) for rule in rules], [["GET"]])
        self.assertEqual(self.client.get("/api/assistant/past-work?q=").status_code, 400)
        # Finding changes nothing and keeps nothing: no action, no memory.
        with connect(self.service.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assistant_actions").fetchone()[0], 1)
        self.assertEqual(self.service.memory.listing()["memories"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()
