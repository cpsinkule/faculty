"""A question bank for each course (requirement, 2026-10-01).

Asked, the instructor chose (same day): questions come from approved quizzes Faculty drafted and approved assignments, from past
assessments already in Faculty, from questions they type or paste, and from a PDF a model reads for them to review; the outline starts
from the lecture notes (chapters, sections, topics) and stays editable, with LEARNING OBJECTIVES below the topics, and each question also
carries a DIFFICULTY and its QUESTION TYPES; and the model TAGS AUTOMATICALLY (its tags apply at once, the instructor changes them later).

Owner: the COURSE DEFINITION (`course_definitions.id`). Every question, outline entry, upload and model run belongs to one course code
and serves all its terms and sections; nothing is copied into a term or a section, and an assessment's question is retained by
reference to its grading question (`frq_question_id`), its content taken as a revision of the bank's own so the bank can be edited
without touching what was graded. Catalog rows are append-only (migration `question_bank/001_bank`); uploaded PDFs live in the
definition's verified storage (`question-bank/uploads/<upload id>/`).

AUTOMATIC WORK, one bounded turn of the Inbox worker's rotation (`process_one`, the catalog plus one file write, at most every
`SYNC_SECONDS`): read the course's current lecture notes into the outline when a new edition stands; retain every question whose rubric
the instructor approved in Key & Rubric Finalization, and every question of a drafted quiz whose approval was carried out (from the
LaTeX they approved); follow a retained question's later approved revision while the instructor has not edited it in the bank; and queue
tagging for questions with no tags or with model tags for an older revision. It also links a drafted quiz's versions and queues the
model's 'versions' run for other assessments, and, once a version's bubble sheets were graded, queues a reading of that version's blank:
the blank PDF is kept in the definition's verified storage (`question-bank/uploads/<id>/source.pdf`, `_sync_bubbles`) under the
lifecycle lock and the owner lock. Each question revision is tagged automatically ONCE: a failed run is retried only when the
instructor asks. The model's work (`runs.py`) is carried out by the grading cloud dispatcher, as quiz drafts are.

Lock order: the roster lifecycle lock, the definition's owner lock (storage) when files are written, then ONE `BEGIN IMMEDIATE`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import digest as _sha, encoded as _json
from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from request_ids import valid_request_id as _request
from rosters import connect
from seating import term_label

from . import content as contents
from .pictures import QuestionBankPictures
from .runs import QuestionBankRuns

LOG = logging.getLogger(__name__)
# The document sandbox reads at most this much (`template_conversion.MAX_INPUT`): a larger PDF is refused here, not after it is kept.
from document_identity.template_conversion import MAX_INPUT as MAX_PDF  # noqa: E402
IDENTIFIER = re.compile(r"[A-Za-z0-9-]{1,64}")
SYNC_SECONDS = 15      # the worker's automatic pass runs at most this often
BUBBLE_RECHECK = 600   # a bubble-sheet version not graded yet is looked at again at most this often
TAG_BATCH = 15         # questions in one tagging request
MAX_ACCEPT = 2000      # problems added from one reading at once (a textbook's 99 pages printed 626)
MAX_TAGS = 12          # outline entries one question is placed under
OUTLINE_RULE = 'bank-outline-notes-v1'
# An upload is one file in one course (UNIQUE(definition, checksum)): its ID derives from them, so a retry finishes its own folder.
UPLOADS = uuid.UUID("2b7e9c41-5d0a-4f3e-9a61-0c8d3e5f7a12")




def _replayed(db, table, owner_column, owner, request_id):
    """An immutable request belongs to the same question or outline entry on retry."""
    row = db.execute(f"SELECT {owner_column} FROM {table} WHERE request_id=?", (request_id,)).fetchone()
    if row and row[0] != owner:
        raise Conflict("This request ID already belongs to another change.")
    return bool(row)


def _file_name(text, fallback="questions.pdf"):
    plain = unicodedata.normalize("NFKC", str(text or ""))
    plain = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", plain)
    plain = " ".join(plain.split()).strip(" .")[:150]
    return plain or fallback


def _slug(text):
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:80] or "questions"


def _number_key(number):
    """'2.10' after '2.9'; numbers before entries without one."""
    parts = re.findall(r"\d+|[^\d.]+", str(number or ""))
    return (0, [(0, int(p)) if p.isdigit() else (1, p) for p in parts]) if parts else (1, [])


def _placed(items):
    """(key, item) for a reading's problems: the key is (page, printed number, place among the problems with that number on that page),
    so the same problem is recognised in another reading of the same PDF."""
    seen = {}
    for item in items:
        number = str((item.get("content") or {}).get("number") or "")
        nth = seen[(item.get("page"), number)] = seen.get((item.get("page"), number), 0) + 1
        yield (item.get("page"), number, nth), item


def _bank_index(questions):
    """The bank's questions (latest content, `_sources` applied) as `_marked` looks them up: by upload, by text, by the assessment version
    and number they are printed as, and each one's printed place."""
    index = {"by_upload": {}, "by_text": {}, "by_number": {}, "printed_of": {}}
    for q in questions:
        label = q["source_label"] + (" (retired)" if q["retired"] else "")
        if q["upload_id"]:
            index["by_upload"].setdefault(q["upload_id"], []).append(q)
        text = contents.text_key(q["content"])
        if text:
            index["by_text"].setdefault(text, []).append((q, label))
        printed = q.get("printed") or {}
        if printed.get("assignment_id"):
            place = (printed["assignment_id"], printed.get("version") or "", str(printed.get("number") or "").strip())
            index["printed_of"][q["id"]] = place
            index["by_number"].setdefault(place, (q, label))
    return index


def _version_by_text(items, upload_id, index):
    """{(assignment, version)} a PDF is when nothing added from it says so: at least two of its problems are word for word that version's
    under the same printed numbers, and none of its other problems that are only text is new to the bank or printed elsewhere (one in
    the bank without a version, written there for example, says nothing); else an empty set."""
    places, hits = set(), 0
    for item in items:
        text = None if item.get("figures") else contents.text_key(item["content"])
        if not text:
            continue
        number = str(item["content"].get("number") or "").strip()
        matches = [q for q, _ in index["by_text"].get(text, []) if q["upload_id"] != upload_id]
        printed = [index["printed_of"][q["id"]] for q in matches if q["id"] in index["printed_of"]]
        if matches and not printed:
            continue
        same = {place[:2] for place in printed if place[2] == number}
        if not same:
            return set()
        places |= same
        hits += 1
    return places if hits >= 2 and len(places) == 1 else set()


class QuestionBankService(QuestionBankPictures, QuestionBankRuns):
    def __init__(self, rosters, documents, notes, grading):
        self.rosters, self.path, self.documents, self.notes, self.grading = rosters, rosters.path, documents, notes, grading
        self._last_sync = 0.0
        self._bubble_checked = {}
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "question_bank", migration.stem, migration.read_text(encoding="utf-8"))

    # -- Reading ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _definition(db, definition_id):
        if not isinstance(definition_id, str) or not IDENTIFIER.fullmatch(definition_id):
            raise NotFound("Course not found.")
        row = db.execute("SELECT id, code FROM course_definitions WHERE id=?", (definition_id,)).fetchone()
        if not row:
            raise NotFound("Course not found.")
        return row

    @staticmethod
    def _outline(db, definition_id):
        """The outline's entries as they stand, in tree order (each entry after its parent, siblings by place, number and title)."""
        rows = [dict(row) for row in db.execute("""SELECT n.id, n.parent_id, n.level, n.origin, n.origin_key, n.created_at, r.revision, r.number, r.title,
                r.position, r.retired, r.origin AS revised_by, r.created_at AS updated_at
            FROM question_bank_nodes n JOIN question_bank_node_revisions r ON r.node_id=n.id
            WHERE n.definition_id=? AND r.revision=(SELECT MAX(revision) FROM question_bank_node_revisions WHERE node_id=n.id)""", (definition_id,))]
        children = {}
        for row in rows:
            children.setdefault(row["parent_id"], []).append(row)
        ordered = []

        def walk(parent, depth):
            for row in sorted(children.get(parent, []), key=lambda r: (r["position"], _number_key(r["number"]), r["title"].casefold(), r["id"])):
                row["depth"], row["retired"] = depth, bool(row["retired"])
                ordered.append(row)
                walk(row["id"], depth + 1)
        walk(None, 0)
        return ordered

    def _latest(self, db, definition_id):
        """Each question of the course with its latest revision, classification and retirement, and what its tagging is doing."""
        questions = [dict(row) for row in db.execute("""SELECT q.*, r.revision, r.content_json, r.origin AS revised_by, r.source_ref, r.created_at AS revised_at
            FROM question_bank_questions q JOIN question_bank_revisions r ON r.question_id=q.id
            WHERE q.definition_id=? AND r.revision=(SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id)
            ORDER BY q.created_at, q.rowid""", (definition_id,))]
        classified = {row["question_id"]: dict(row) for row in db.execute("""SELECT c.* FROM question_bank_classifications c
            JOIN question_bank_questions q ON q.id=c.question_id WHERE q.definition_id=?
            AND c.revision=(SELECT MAX(revision) FROM question_bank_classifications WHERE question_id=c.question_id)""", (definition_id,))}
        retired = {row["question_id"]: row["action"] == "retired" for row in db.execute("""SELECT e.question_id, e.action FROM question_bank_events e
            JOIN question_bank_questions q ON q.id=e.question_id WHERE q.definition_id=?
            AND e.rowid=(SELECT MAX(rowid) FROM question_bank_events WHERE question_id=e.question_id)""", (definition_id,))}
        runs = {}
        for row in db.execute("""SELECT m.question_id, m.content_revision, r.id, r.status, r.error, r.created_at, r.actor FROM question_bank_run_questions m
                JOIN question_bank_runs r ON r.id=m.run_id WHERE r.definition_id=? AND r.kind='tag' AND r.purpose='' ORDER BY r.created_at, r.rowid""", (definition_id,)):
            runs[row["question_id"]] = dict(row)   # the latest tagging run of each question
        printed = {row["question_id"]: dict(row) for row in db.execute("""SELECT l.* FROM question_bank_assessment_links l
            JOIN question_bank_questions q ON q.id=l.question_id WHERE q.definition_id=?
            AND l.revision=(SELECT MAX(revision) FROM question_bank_assessment_links WHERE question_id=l.question_id)""", (definition_id,))}
        groups = {row["question_id"]: dict(row) for row in db.execute("""SELECT l.* FROM question_bank_version_links l
            JOIN question_bank_questions q ON q.id=l.question_id WHERE q.definition_id=?
            AND l.revision=(SELECT MAX(revision) FROM question_bank_version_links WHERE question_id=l.question_id)""", (definition_id,))}
        pictures = self._pictures(db, [question["id"] for question in questions])
        for question in questions:
            question["pictures"] = pictures.get(question["id"], [])
            question["content"] = json.loads(question.pop("content_json"))
            question["source"] = json.loads(question.pop("source_json"))
            question["classification"] = classified.get(question["id"])
            question["retired"] = retired.get(question["id"], False)
            question["run"] = runs.get(question["id"])
            question["printed_link"] = printed.get(question["id"])
            question["group"] = groups.get(question["id"])
        return questions

    def _standing_outline(self, db, definition_id):
        """QUESTION-BANK: retiring an entry also hides every descendant from new tags."""
        standing, hidden = [], set()
        for node in self._outline(db, definition_id):
            if node["retired"] or node["parent_id"] in hidden:
                hidden.add(node["id"])
            else:
                standing.append(node)
        return standing

    @staticmethod
    def _tagging(question):
        """'pending'/'running' while a tagging run has it, 'failed' while its latest run failed and nothing tagged it since, else ''."""
        run, classification = question["run"], question["classification"]
        if not run:
            return ""
        if run["status"] in ("pending", "running"):
            return run["status"]
        if run["status"] == "failed" and (not classification or classification["created_at"] < run["created_at"]):
            return "failed"
        return ""

    def _sources(self, db, questions):
        """Where each question came from, read now from its owners: the assessment's title and version, the terms that graded it."""
        frq = sorted({q["frq_question_id"] for q in questions if q["frq_question_id"]})
        uploads = sorted({q["upload_id"] for q in questions if q["upload_id"]})
        linked = sorted({q["printed_link"]["assignment_id"] for q in questions if (q.get("printed_link") or {}).get("assignment_id")})
        found, terms, files, titles, used = {}, {}, {}, {}, {}
        # The terms a rebuilt quiz was used in count for the approval's question it reprints (`_quiz_aliases`).
        reprinted = {}
        if frq:
            for alias, original in self._quiz_aliases(db, None).items():
                if original in frq:
                    reprinted.setdefault(alias, original)
        if frq:
            marks = ",".join("?" * len(frq))
            for row in db.execute(f"""SELECT q.id, q.exam_version, a.id AS assignment_id, a.title FROM frq_questions q
                    JOIN assignment_versions v ON v.id=q.assignment_version_id JOIN assignments a ON a.id=v.assignment_id WHERE q.id IN ({marks})""", frq):
                found[row["id"]] = dict(row)
            for row in db.execute(f"""SELECT DISTINCT p.question_id, t.term, t.term_other, t.year FROM frq_assignment_questions p
                    JOIN frq_assignments f ON f.id=p.assignment_id JOIN exam_events e ON e.id=f.event_id JOIN course_terms t ON t.id=e.term_id
                    WHERE p.question_id IN ({",".join("?" * (len(frq) + len(reprinted)))}) ORDER BY t.year, t.term""", frq + sorted(reprinted)):
                owner = reprinted.get(row["question_id"], row["question_id"])
                if term_label(row) not in terms.get(owner, []):
                    terms.setdefault(owner, []).append(term_label(row))
        if uploads:
            marks = ",".join("?" * len(uploads))
            files = {row["id"]: row["source_name"] for row in db.execute(f"SELECT id, source_name FROM question_bank_uploads WHERE id IN ({marks})", uploads)}
        if linked:
            marks = ",".join("?" * len(linked))
            titles = {row["id"]: row["title"] for row in db.execute(f"SELECT id, title FROM assignments WHERE id IN ({marks})", linked)}
            for row in db.execute(f"""SELECT DISTINCT e.assignment_id, t.term, t.term_other, t.year FROM exam_events e JOIN course_terms t ON t.id=e.term_id
                    WHERE e.assignment_id IN ({marks}) ORDER BY t.year, t.term""", linked):
                used.setdefault(row["assignment_id"], []).append(term_label(row))
        for question in questions:
            content, source = question["content"], question["source"]
            if question["frq_question_id"]:
                row = found.get(question["frq_question_id"]) or {}
                version = row.get("exam_version") or ""
                question["source_label"] = " · ".join(filter(None, [row.get("title", "An assessment"), f"Problem {content['number']}" if content["number"] else "",
                                                                  f"Version {version}" if version else ""]))
                question["assignment_id"], question["version"], question["terms"] = row.get("assignment_id"), version, terms.get(question["frq_question_id"], [])
                question["printed"] = row and {"assignment_id": row["assignment_id"], "title": row["title"], "version": version, "number": content["number"], "from": "grading"}
                continue
            link = question.get("printed_link") or {}
            if link.get("assignment_id"):
                # Printed on an assessment the instructor named: labelled like a retained question, its file kept as the source.
                title = titles.get(link["assignment_id"], "An assessment")
                question["printed"] = {"assignment_id": link["assignment_id"], "title": title, "version": link["exam_version"], "number": link["number"],
                                       "from": "link", "revision": link["revision"]}
                question["source_label"] = " · ".join(filter(None, [title, f"Problem {link['number']}" if link["number"] else "",
                                                                  f"Version {link['exam_version']}" if link["exam_version"] else ""]))
                question["assignment_id"], question["version"], question["terms"] = link["assignment_id"], link["exam_version"], used.get(link["assignment_id"], [])
                question["source_file"] = files.get(question["upload_id"], "") if question["upload_id"] else ""
                continue
            question["printed"] = {"from": "link", "revision": link["revision"]} if link else None
            if question["upload_id"]:
                question["source_label"] = " · ".join(filter(None, [files.get(question["upload_id"], "A PDF"), contents.section_label(source.get("section")),
                                                                  f"page {source.get('page')}" if source.get("page") else "",
                                                                  f"Problem {content['number']}" if content["number"] else ""]))
                question["version"], question["terms"] = "", []
            else:
                question["source_label"], question["version"], question["terms"] = "Written in the bank", "", []
            question["assignment_id"] = None

    def _summary(self, question, details=False):
        content = question["content"]
        classification = question["classification"]
        shown = content if details else {k: v for k, v in content.items() if k not in ("solution", "figure")}
        return {
            "id": question["id"], "origin": question["origin"], "family": question["family"], "source_label": question["source_label"],
            "assignment_id": question.get("assignment_id"), "version": question["version"], "terms": question["terms"],
            "upload_id": question["upload_id"], "revision": question["revision"], "printed": question.get("printed"),
            "source_file": question.get("source_file", ""),
            "group": question.get("group") and question["group"]["group_id"] and {
                "id": question["group"]["group_id"], "origin": question["group"]["origin"], "reason": question["group"]["reason"], "revision": question["group"]["revision"]},
            "group_revision": question["group"]["revision"] if question.get("group") else 0,
            "edited": question["revised_by"] == "instructor" and question["revision"] > 1,  # changed in the bank after it joined
            "content": {**shown, "has_solution": bool(content["solution"]), "has_figure": bool(content["figure"])},
            "pictures": [p for p in question.get("pictures", []) if not p["removed"]] if not details else question.get("pictures", []),
            "classification": classification and {
                "revision": classification["revision"], "nodes": json.loads(classification["nodes_json"]), "difficulty": classification["difficulty"],
                "types": json.loads(classification["types_json"]), "origin": classification["origin"], "reason": classification["reason"],
                "content_revision": classification["content_revision"], "at": classification["created_at"]},
            "retired": question["retired"], "tagging": self._tagging(question),
            "tagging_error": question["run"]["error"] if self._tagging(question) == "failed" else "",
            "created_at": question["created_at"], "updated_at": max(question["revised_at"], classification["created_at"] if classification else ""),
        }

    def _model(self):
        """The model that tags and reads PDFs: the Rubric drafting and review model, as for drafted quizzes."""
        try:
            endpoint, settings = self.grading._drafting_model()
        except ValueError as problem:
            return None, None, str(problem)
        return endpoint, settings, ""

    def view(self, definition_id, summary=False):
        """The course's bank: the outline, every question (retired ones marked), the PDFs read for it, its model runs and counts."""
        with connect(self.path) as db:
            definition = self._definition(db, definition_id)
            questions = self._latest(db, definition_id)
            if summary:
                standing = [q for q in questions if not q["retired"]]
                return {"definition_id": definition["id"], "code": definition["code"], "questions": len(standing),
                        "tagged": sum(1 for q in standing if q["classification"])}
            outline = self._outline(db, definition_id)
            self._sources(db, questions)
            outline_import = db.execute("""SELECT i.notes_id, i.created_at, n.title FROM question_bank_outline_imports i JOIN course_notes n ON n.id=i.notes_id
                WHERE i.definition_id=? ORDER BY i.created_at DESC, i.rowid DESC LIMIT 1""", (definition_id,)).fetchone()
            uploads = [dict(row) for row in db.execute("SELECT * FROM question_bank_uploads WHERE definition_id=? ORDER BY created_at DESC, rowid DESC", (definition_id,))]
            runs = [dict(row) for row in db.execute("""SELECT id, kind, upload_id, retry_of, actor, status, cancelled, error, created_at, updated_at,
                    (SELECT COUNT(*) FROM question_bank_run_questions m WHERE m.run_id=r.id) AS questions,
                    CASE WHEN kind='extract' AND status='complete' THEN json_array_length(json_extract(result_json, '$.questions')) END AS found,
                    CASE WHEN kind='extract' AND status='complete' THEN json_extract(result_json, '$.pages') END AS pages
                FROM question_bank_runs r WHERE definition_id=? ORDER BY created_at DESC, rowid DESC""", (definition_id,))]
            waiting = len(self._waiting_for_text(db, definition_id))
            to_add = self._still_to_add(db, uploads, runs, questions)
            unread = sum(len(v["missing"]) for v in self._bubble_versions(db, definition_id) if v["attempted"] == "complete")
            assignments = [{"id": row["id"], "title": row["title"]} for row in db.execute(
                "SELECT id, title FROM assignments WHERE definition_id=? ORDER BY created_at, rowid", (definition_id,))]
        counts = {node["id"]: 0 for node in outline}
        parents = {node["id"]: node["parent_id"] for node in outline}
        for question in questions:
            if question["retired"] or not question["classification"]:
                continue
            seen = set()
            for node in json.loads(question["classification"]["nodes_json"]):
                while node and node not in seen:
                    seen.add(node)
                    node = parents.get(node)
            for node in seen:
                if node in counts:
                    counts[node] += 1
        added = {}
        for question in questions:
            if question["upload_id"]:
                added[question["upload_id"]] = added.get(question["upload_id"], 0) + 1
        latest_runs = {}
        for run in runs:
            if run["upload_id"] and run["upload_id"] not in latest_runs:
                latest_runs[run["upload_id"]] = run
        endpoint, _, problem = self._model()
        listed = [self._summary(q) for q in questions]
        return {
            "definition_id": definition["id"], "code": definition["code"],
            "outline": [{"id": n["id"], "parent_id": n["parent_id"], "level": n["level"], "number": n["number"], "title": n["title"],
                         "depth": n["depth"], "retired": n["retired"], "origin": n["origin"], "revised_by": n["revised_by"], "revision": n["revision"],
                         "questions": counts.get(n["id"], 0)} for n in outline],
            "outline_source": outline_import and {"notes_id": outline_import["notes_id"], "title": outline_import["title"], "imported_at": outline_import["created_at"]},
            "questions": listed,
            "uploads": [{"id": u["id"], "source_name": u["source_name"], "size": u["source_size"], "created_at": u["created_at"],
                         "url": f"/api/question-bank/uploads/{u['id']}/{_slug(Path(u['source_name']).stem)}.pdf", "added": added.get(u["id"], 0),
                         "waiting": to_add.get(u["id"], 0),
                         "pages": next((r["pages"] for r in runs if r["upload_id"] == u["id"] and r["pages"]), 0),
                         "run": latest_runs.get(u["id"]) and {k: latest_runs[u["id"]][k] for k in ("id", "status", "error", "found", "created_at", "updated_at")}}
                        for u in uploads],
            "runs": [run for run in runs if run["status"] in ("pending", "running")] + [run for run in runs if run["status"] not in ("pending", "running")][:10],
            "counts": {"questions": sum(1 for q in listed if not q["retired"]), "retired": sum(1 for q in listed if q["retired"]),
                       "tagged": sum(1 for q in listed if not q["retired"] and q["classification"]),
                       "tagging": sum(1 for q in listed if not q["retired"] and q["tagging"] in ("pending", "running")),
                       "failed": sum(1 for q in listed if not q["retired"] and q["tagging"] == "failed"), "waiting_for_text": waiting,
                       "bubbles_unread": unread},
            "model": {"ready": bool(endpoint), "name": endpoint["name"] if endpoint else "", "problem": problem},
            "vocabulary": {"levels": list(contents.LEVELS), "difficulties": list(contents.DIFFICULTIES), "kinds": contents.KIND_LABELS,
                           "types": [{"key": key, "label": label, "description": text} for key, (label, text) in contents.TYPES.items()]},
            "max_bytes": MAX_PDF, "assignments": assignments,
        }

    def _still_to_add(self, db, uploads, runs, questions):
        """{upload: how many problems of its latest reading are not in the bank in any way} (`_marked`): what Add all would add. A reading
        whose every problem was added from it needs no look."""
        latest, own, index = {}, {}, None
        for run in runs:
            if run["upload_id"] and run["status"] == "complete":
                latest.setdefault(run["upload_id"], run)
        for q in questions:
            if q["origin"] == "pdf":
                run_id = q["origin_key"].split(":")[1] if q["origin_key"].count(":") >= 2 else ""
                own[run_id] = own.get(run_id, 0) + 1
        counts = {}
        for upload in uploads:
            run = latest.get(upload["id"])
            if not run or not run["found"] or own.get(run["id"], 0) >= run["found"]:
                continue
            index = index or _bank_index(questions)
            kept = db.execute("SELECT id, result_json FROM question_bank_runs WHERE id=?", (run["id"],)).fetchone()
            counts[upload["id"]] = sum(1 for item in self._marked(db, upload["id"], dict(kept), index) if not item["question_id"])
        return counts

    def question(self, question_id):
        """One question with everything it holds and its history: every revision of its content and of its classification, and when it
        was retired or restored."""
        if not isinstance(question_id, str) or not IDENTIFIER.fullmatch(question_id):
            raise NotFound("Question not found.")
        with connect(self.path) as db:
            row = db.execute("SELECT definition_id FROM question_bank_questions WHERE id=?", (question_id,)).fetchone()
            if not row:
                raise NotFound("Question not found.")
            all_questions = self._latest(db, row["definition_id"])
            questions = [q for q in all_questions if q["id"] == question_id]
            self._sources(db, questions)
            revisions = [{"revision": r["revision"], "origin": r["origin"], "source_ref": r["source_ref"], "actor": r["actor"], "created_at": r["created_at"],
                          "content": json.loads(r["content_json"])}
                         for r in db.execute("SELECT * FROM question_bank_revisions WHERE question_id=? ORDER BY revision DESC", (question_id,))]
            classifications = [{"revision": c["revision"], "origin": c["origin"], "nodes": json.loads(c["nodes_json"]), "difficulty": c["difficulty"],
                                "types": json.loads(c["types_json"]), "reason": c["reason"], "content_revision": c["content_revision"], "actor": c["actor"],
                                "created_at": c["created_at"]}
                               for c in db.execute("SELECT * FROM question_bank_classifications WHERE question_id=? ORDER BY revision DESC", (question_id,))]
            events = [dict(e) for e in db.execute("SELECT action, actor, created_at FROM question_bank_events WHERE question_id=? ORDER BY created_at DESC, rowid DESC", (question_id,))]
            picture_events = [{"action": "added", "picture_id": p["id"], "caption": p["caption"], "actor": p["actor"], "created_at": p["created_at"]}
                              for p in db.execute("SELECT id, caption, actor, created_at FROM question_bank_pictures WHERE question_id=?", (question_id,))]
            picture_events += [{"action": e["action"], "picture_id": e["picture_id"], "caption": e["caption"], "actor": e["actor"], "created_at": e["created_at"]}
                               for e in db.execute("""SELECT e.action, e.picture_id, p.caption, e.actor, e.created_at FROM question_bank_picture_events e
                                   JOIN question_bank_pictures p ON p.id=e.picture_id WHERE p.question_id=?""", (question_id,))]
            pages = 0
            if questions[0]["upload_id"]:
                found = db.execute("""SELECT json_extract(result_json, '$.pages') FROM question_bank_runs WHERE upload_id=? AND status='complete'
                    ORDER BY created_at DESC, rowid DESC LIMIT 1""", (questions[0]["upload_id"],)).fetchone()
                pages = (found and found[0]) or 0
            group = (questions[0].get("group") or {}).get("group_id")
            members = [q for q in all_questions if group and q["id"] != question_id and (q.get("group") or {}).get("group_id") == group]
            self._sources(db, members)
            links = [{"revision": l["revision"], "grouped": bool(l["group_id"]), "origin": l["origin"], "reason": l["reason"], "actor": l["actor"], "created_at": l["created_at"]}
                     for l in db.execute("SELECT * FROM question_bank_version_links WHERE question_id=? ORDER BY revision DESC", (question_id,))]
        return {**self._summary(questions[0], details=True), "definition_id": row["definition_id"], "revisions": revisions,
                "classifications": classifications, "events": events, "version_links": links, "source_page": questions[0]["source"].get("page") or 0,
                "source_pages": pages, "picture_events": sorted(picture_events, key=lambda e: e["created_at"], reverse=True),
                "other_versions": sorted(({"id": m["id"], "version": m["version"], "source_label": m["source_label"], "retired": m["retired"]} for m in members),
                                         key=lambda v: (v["version"], v["source_label"]))}

    def statistics(self, definition_id):
        """How students did on each question, from the grades Faculty holds (the instructor's choice, 2026-10-01: shown beside the model's
        difficulty): a free-response question's scores in the LATEST CERTIFICATION of each term's plan, and a bubble-sheet question's item
        analysis (`bubble_analysis`, Inbox-deleted scans excluded). The share of points earned per student, averaged over every term that
        graded it. Read on request, never stored; a plan not certified yet adds its free-response grades once it is."""
        with connect(self.path) as db:
            self._definition(db, definition_id)
            questions = self._latest(db, definition_id)
            self._sources(db, questions)
            frq = {q["frq_question_id"]: q["id"] for q in questions if q["frq_question_id"]}
            # A rebuilt quiz's grades are on its new grading questions: they count for the approval's copy (`_quiz_aliases`).
            frq.update({alias: frq[original] for alias, original in self._quiz_aliases(db, definition_id).items() if original in frq})
            printed = {}
            for q in questions:
                where = q.get("printed") or {}
                if where.get("from") == "link" and where.get("assignment_id") and str(where.get("number") or "").isdigit():
                    printed[(where["assignment_id"], where.get("version") or "", int(where["number"]))] = q["id"]
            assignments = sorted({q["assignment_id"] for q in questions if q.get("assignment_id")})
            plans = [dict(row) for row in db.execute(f"""SELECT f.id AS plan_id, e.assignment_id, a.title, t.term, t.term_other, t.year,
                    (SELECT id FROM frq_certifications c WHERE c.assignment_id=f.id ORDER BY c.rowid DESC LIMIT 1) AS certification_id,
                    EXISTS (SELECT 1 FROM frq_bubble_keys k WHERE k.assignment_id=f.id) AS bubbles
                FROM frq_assignments f JOIN exam_events e ON e.id=f.event_id JOIN assignments a ON a.id=e.assignment_id JOIN course_terms t ON t.id=e.term_id
                WHERE e.assignment_id IN ({','.join('?' * len(assignments))}) ORDER BY t.year, t.term, a.title""", assignments)] if assignments else []
            totals = {}

            def add(question_id, share, students, term, source):
                entry = totals.setdefault(question_id, {"share": 0.0, "n": 0, "terms": [], "sources": []})
                entry["share"] += share
                entry["n"] += students
                for key, value in (("terms", term), ("sources", source)):
                    if value not in entry[key]:
                        entry[key].append(value)

            for plan in plans:
                plan["term_label"] = term_label(plan)
                if not plan["certification_id"] or not frq:
                    continue
                for row in db.execute("SELECT parts_json FROM frq_certification_students WHERE certification_id=? AND status='scored'", (plan["certification_id"],)):
                    for frq_id, score in ((json.loads(row["parts_json"]).get("frq") or {}).get("questions") or {}).items():
                        try:
                            earned, possible = float(score["score"]), float(score["max"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if frq_id in frq and possible > 0:
                            add(frq[frq_id], earned / possible, 1, plan["term_label"], "certified grades")
        for plan in plans:
            if not plan["bubbles"] or not any(key[0] == plan["assignment_id"] for key in printed):
                continue
            try:
                analysis = self.grading.bubble_analysis(plan["plan_id"])
            except (ValueError, NotFound, Conflict) as problem:
                LOG.warning("Bubble item analysis of plan %s unavailable for the question bank: %s", plan["plan_id"], problem)
                continue
            for version in analysis.get("versions") or []:
                for item in version.get("questions") or []:
                    question_id = printed.get((plan["assignment_id"], version["letter"], item["number"])) or printed.get((plan["assignment_id"], "", item["number"]))
                    # An item whose key accepts every bubble (a question thrown out) measures nothing about the question.
                    if item.get("scoring") == "either" and set("ABCDE") <= set(str(item.get("answer") or "")):
                        continue
                    if question_id and item.get("sheets") and item.get("difficulty") is not None:
                        add(question_id, float(item["difficulty"]) * item["sheets"], item["sheets"], plan["term_label"], "bubble sheets")
        return {"definition_id": definition_id,
                "questions": {qid: {"earned_pct": round(100 * entry["share"] / entry["n"]), "students": entry["n"], "terms": entry["terms"],
                                    "sources": entry["sources"]} for qid, entry in totals.items() if entry["n"]},
                "plans": [{"title": plan["title"], "term": plan["term_label"], "certified": bool(plan["certification_id"]), "bubble_sheets": bool(plan["bubbles"])}
                          for plan in plans]}

    def read_upload(self, upload_id):
        """(bytes, sha256, download name) of an uploaded PDF, checked against its checksum."""
        from document_identity.storage import read_verified
        if not isinstance(upload_id, str) or not IDENTIFIER.fullmatch(upload_id):
            raise NotFound("Upload not found.")
        with connect(self.path) as db:
            upload = db.execute("SELECT * FROM question_bank_uploads WHERE id=?", (upload_id,)).fetchone()
        if not upload:
            raise NotFound("Upload not found.")
        with self.rosters.lock(), self.documents.files.files("definition", upload["definition_id"]) as root:
            data = read_verified(root, upload["path"], upload["source_sha256"])
        name = _file_name(upload["source_name"])
        return data, upload["source_sha256"], name if name.lower().endswith(".pdf") else name + ".pdf"

    # -- The outline --------------------------------------------------------------------------------------------------------
    @staticmethod
    def _node(db, node_id):
        if not isinstance(node_id, str) or not IDENTIFIER.fullmatch(node_id):
            raise NotFound("Outline entry not found.")
        row = db.execute("""SELECT n.*, r.revision, r.number, r.title, r.position, r.retired FROM question_bank_nodes n
            JOIN question_bank_node_revisions r ON r.node_id=n.id WHERE n.id=?
            AND r.revision=(SELECT MAX(revision) FROM question_bank_node_revisions WHERE node_id=n.id)""", (node_id,)).fetchone()
        if not row:
            raise NotFound("Outline entry not found.")
        return dict(row)

    @staticmethod
    def _new_node(db, definition_id, parent_id, level, origin, origin_key, number, title, position, actor, request_id=None):
        node_id, now = str(uuid.uuid4()), timestamp()
        db.execute("INSERT INTO question_bank_nodes(id,definition_id,parent_id,level,origin,origin_key,created_at) VALUES (?,?,?,?,?,?,?)",
                   (node_id, definition_id, parent_id, level, origin, origin_key, now))
        db.execute("""INSERT INTO question_bank_node_revisions(node_id,revision,number,title,position,retired,origin,request_id,actor,created_at)
                      VALUES (?,1,?,?,?,0,?,?,?,?)""", (node_id, number, title, position, origin, request_id, actor, now))
        return node_id

    @staticmethod
    def _revise_node(db, node, origin, actor, request_id=None, **changes):
        values = {"number": node["number"], "title": node["title"], "position": node["position"], "retired": int(bool(node["retired"])), **changes}
        db.execute("""INSERT INTO question_bank_node_revisions(node_id,revision,number,title,position,retired,origin,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""", (node["id"], node["revision"] + 1, values["number"], values["title"], values["position"],
                                                       int(bool(values["retired"])), origin, request_id, actor, timestamp()))

    @staticmethod
    def _next_position(db, definition_id, parent_id):
        return db.execute("""SELECT COALESCE(MAX(r.position), 0) + 1 FROM question_bank_nodes n JOIN question_bank_node_revisions r ON r.node_id=n.id
            WHERE n.definition_id=? AND n.parent_id IS ?""", (definition_id, parent_id)).fetchone()[0]

    @staticmethod
    def _title(value, label="Title"):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 300:
            raise ValueError(f"{label} must be 1–300 characters.")
        return " ".join(value.split())

    @staticmethod
    def _number(value):
        value = "" if value is None else value
        if not isinstance(value, str) or len(value.strip()) > 20:
            raise ValueError("A number is at most 20 characters, such as 2 or 2.3.")
        return value.strip()

    def add_node(self, definition_id, data, actor="workspace"):
        """A chapter, section, topic or learning objective the instructor adds, at the end of its parent's entries."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        level = data.get("level")
        if level not in contents.LEVELS:
            raise ValueError("Choose a chapter, section, topic or learning objective.")
        title, number = self._title(data.get("title")), self._number(data.get("number"))
        parent_id = data.get("parent_id") or None
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._definition(db, definition_id)
            done = db.execute("""SELECT r.node_id,n.definition_id FROM question_bank_node_revisions r
                JOIN question_bank_nodes n ON n.id=r.node_id WHERE r.request_id=?""", (request_id,)).fetchone()
            if done:
                if done["definition_id"] != definition_id:
                    raise Conflict("This request ID already belongs to another course's outline.")
                return {**self.view(definition_id), "node_id": done["node_id"], "repeated": True}
            if parent_id:
                parent = self._node(db, parent_id)
                if parent["definition_id"] != definition_id or parent_id not in {node["id"] for node in self._standing_outline(db, definition_id)}:
                    raise ValueError("Choose an entry of this course's outline that is not retired.")
                allowed = {"section": ("chapter",), "topic": ("section",), "objective": ("section", "topic")}.get(level, ())
                if parent["level"] not in allowed:
                    raise ValueError(f"A {level} cannot sit under a {parent['level']}.")
            elif level in ("topic", "objective"):
                raise ValueError(f"Choose the {'section' if level == 'topic' else 'section or topic'} it belongs to.")
            node_id = self._new_node(db, definition_id, parent_id, level, "instructor", None, number, title,
                                     self._next_position(db, definition_id, parent_id), actor, request_id)
            db.commit()
        return {**self.view(definition_id), "node_id": node_id, "repeated": False}

    def revise_node(self, node_id, data, actor="workspace"):
        """Rename or renumber an entry, or retire it (its questions keep their tags; it is no longer offered) and restore it."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            node = self._node(db, node_id)
            if _replayed(db, "question_bank_node_revisions", "node_id", node_id, request_id):
                return {**self.view(node["definition_id"]), "repeated": True}
            if data.get("based_on") != node["revision"]:
                raise Conflict("This entry was changed since you opened it. Look at it again, then change it.")
            changes = {}
            if "title" in data:
                changes["title"] = self._title(data["title"])
            if "number" in data:
                changes["number"] = self._number(data["number"])
            if "retired" in data:
                if not isinstance(data["retired"], bool):
                    raise ValueError("Say whether the entry is retired.")
                changes["retired"] = int(data["retired"])
            if not changes or all(node[k] == v for k, v in changes.items()):
                return {**self.view(node["definition_id"]), "repeated": False}
            self._revise_node(db, node, "instructor", actor, request_id, **changes)
            db.commit()
        return {**self.view(node["definition_id"]), "repeated": False}

    def _import_outline(self, db, definition_id, dry=False):
        """The course's current lecture notes read into the outline, once per edition: each chapter, its sections and their topics.
        An entry the notes made follows a later edition's title; one the instructor renamed or retired keeps their revision, and nothing
        is removed when an edition leaves an entry out. Returns how many entries were added or renamed."""
        current = self.notes.view(definition_id)["current"]
        if not current or db.execute("SELECT 1 FROM question_bank_outline_imports WHERE definition_id=? AND notes_id=?",
                                     (definition_id, current["id"])).fetchone():
            return 0
        if dry:
            return 1
        existing = {row["origin_key"]: dict(row) for row in db.execute("""SELECT n.id, n.origin_key, r.revision, r.number, r.title, r.position, r.retired,
                r.origin AS revised_by FROM question_bank_nodes n JOIN question_bank_node_revisions r ON r.node_id=n.id
            WHERE n.definition_id=? AND n.origin_key IS NOT NULL
            AND r.revision=(SELECT MAX(revision) FROM question_bank_node_revisions WHERE node_id=n.id)""", (definition_id,))}
        added = renamed = 0

        def place(key, parent_id, level, number, title, position):
            nonlocal added, renamed
            title = " ".join(str(title).split())[:300] or number or level.title()
            found = existing.get(key)
            if not found:
                existing[key] = {"id": self._new_node(db, definition_id, parent_id, level, "notes", key, number, title, position, "automatic"),
                                 "revision": 1, "number": number, "title": title, "position": position, "retired": 0, "revised_by": "notes"}
                added += 1
            elif found["revised_by"] == "notes" and (found["title"], found["number"], found["position"]) != (title, number, position):
                self._revise_node(db, found, "notes", "automatic", None, title=title, number=number, position=position)
                found.update(revision=found["revision"] + 1, title=title, number=number, position=position)
                renamed += 1
            return existing[key]["id"]

        for part in current["parts"]:
            if part["kind"] != "section" or not part["number"]:
                continue
            chapter_id = None
            if part["chapter"]:
                chapter_id = place(f"chapter:{part['chapter']}", None, "chapter", part["chapter"], part["chapter_title"] or f"Chapter {part['chapter']}",
                                   int(part["chapter"]) if part["chapter"].isdigit() else 0)
            section_id = place(f"section:{part['number']}", chapter_id, "section", part["number"], part["title"], part["position"])
            seen = set()
            for index, topic in enumerate(part["topics"], 1):
                name = contents.norm(topic["title"])
                if not name or name in seen:
                    continue
                seen.add(name)
                place(f"topic:{part['number']}:{name}"[:400], section_id, "topic", "", topic["title"], index)
        db.execute("INSERT INTO question_bank_outline_imports(definition_id,notes_id,summary_json,created_at) VALUES (?,?,?,?)",
                   (definition_id, current["id"], _json({"rule": OUTLINE_RULE, "added": added, "renamed": renamed, "title": current["title"]}), timestamp()))
        return added + renamed + 1

    # -- Questions ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _question_row(db, question_id):
        if not isinstance(question_id, str) or not IDENTIFIER.fullmatch(question_id):
            raise NotFound("Question not found.")
        row = db.execute("""SELECT q.*, (SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id) AS revision,
                (SELECT MAX(revision) FROM question_bank_classifications WHERE question_id=q.id) AS classified FROM question_bank_questions q WHERE q.id=?""",
                         (question_id,)).fetchone()
        if not row:
            raise NotFound("Question not found.")
        return dict(row)

    @staticmethod
    def _new_question(db, definition_id, origin, origin_key, content, actor, *, frq_question_id=None, upload_id=None, family="", source=None,
                      revised_by="source", source_ref="", request_id=None):
        question_id, now = str(uuid.uuid4()), timestamp()
        db.execute("""INSERT INTO question_bank_questions(id,definition_id,origin,origin_key,frq_question_id,upload_id,family,source_json,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (question_id, definition_id, origin, origin_key.replace("{id}", question_id), frq_question_id, upload_id,
                                                         family, _json(source or {}), request_id, actor, now))
        QuestionBankService._add_revision(db, question_id, 1, content, revised_by, source_ref, request_id, actor, now=now)
        return question_id

    def _classification(self, db, definition_id, data):
        """An instructor's classification, checked: entries of this course's outline that are not retired, one difficulty, known types."""
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list) or len(nodes) > MAX_TAGS or not all(isinstance(n, str) for n in nodes):
            raise ValueError(f"Place a question under at most {MAX_TAGS} outline entries.")
        nodes = list(dict.fromkeys(nodes))
        if nodes:
            standing = {node["id"] for node in self._standing_outline(db, definition_id)}
            if set(nodes) - standing:
                raise ValueError("Choose entries of this course's outline that are not retired.")
        difficulty = data.get("difficulty") or ""
        if difficulty and difficulty not in contents.DIFFICULTIES:
            raise ValueError("Difficulty is easy, medium or hard.")
        types = data.get("types", [])
        if not isinstance(types, list) or any(not isinstance(t, str) or t not in contents.TYPES for t in types):
            raise ValueError("Choose question types from the list.")
        return nodes, difficulty, [t for t in contents.TYPES if t in types]

    @staticmethod
    def _add_revision(db, question_id, revision, content, origin, source_ref, request_id, actor, now=None, digest=None):
        db.execute("""INSERT INTO question_bank_revisions(question_id,revision,content_json,sha256,origin,source_ref,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?)""", (question_id, revision, _json(content), digest or _sha(content), origin, source_ref, request_id, actor,
                                                     now or timestamp()))

    @staticmethod
    def _classify(db, question_id, revision, content_revision, nodes, difficulty, types, origin, actor, run_id=None, reason="", request_id=None):
        db.execute("""INSERT INTO question_bank_classifications(question_id,revision,content_revision,nodes_json,difficulty,types_json,origin,run_id,reason,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (question_id, revision, content_revision, _json(nodes), difficulty, _json(types), origin, run_id,
                                                          reason[:2000], request_id, actor, timestamp()))

    def add_question(self, definition_id, data, actor="workspace"):
        """A question the instructor writes or pastes. Tags given with it are theirs; without them the model tags it automatically."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        content = contents.clean(data.get("content"))
        tags = data.get("classification")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._definition(db, definition_id)
            done = db.execute("""SELECT q.id,q.definition_id,r.sha256 FROM question_bank_questions q
                JOIN question_bank_revisions r ON r.question_id=q.id AND r.revision=1 WHERE q.request_id=?""", (request_id,)).fetchone()
            if done:
                if done["definition_id"] != definition_id or done["sha256"] != _sha(content):
                    raise Conflict("This request ID already belongs to another question.")
                return self.question(done["id"])
            classification = self._classification(db, definition_id, tags) if isinstance(tags, dict) else None
            question_id = self._new_question(db, definition_id, "instructor", "instructor:{id}", content, actor, revised_by="instructor", request_id=request_id)
            if classification and any(classification):
                self._classify(db, question_id, 1, 1, *classification, "instructor", actor, request_id=request_id)
            db.commit()
        self._wake()
        return self.question(question_id)

    def revise_question(self, question_id, data, actor="workspace"):
        """The instructor's edit of a question's content: a new revision; the earlier ones stay. A question retained from an assessment
        keeps the instructor's wording from then on, even when the assessment's question is revised."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        content = contents.clean(data.get("content"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._question_row(db, question_id)
            if _replayed(db, "question_bank_revisions", "question_id", question_id, request_id):
                return self.question(question_id)
            if data.get("based_on") != row["revision"]:
                raise Conflict("This question was changed since you opened it. Look at it again, then edit it.")
            latest = db.execute("SELECT sha256 FROM question_bank_revisions WHERE question_id=? AND revision=?", (question_id, row["revision"])).fetchone()
            digest = _sha(content)
            if latest["sha256"] != digest:
                self._add_revision(db, question_id, row["revision"] + 1, content, "instructor", "", request_id, actor, digest=digest)
                db.commit()
        self._wake()
        return self.question(question_id)

    def classify(self, question_id, data, actor="workspace"):
        """The instructor's tags for a question: where it sits in the outline, its difficulty and types. They stand until the instructor
        changes them or asks the model to tag the question again."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._question_row(db, question_id)
            if _replayed(db, "question_bank_classifications", "question_id", question_id, request_id):
                return self.question(question_id)
            if (data.get("based_on") or 0) != (row["classified"] or 0):
                raise Conflict("This question's tags changed since you opened it. Look at them again, then change them.")
            nodes, difficulty, types = self._classification(db, row["definition_id"], data)
            self._classify(db, question_id, (row["classified"] or 0) + 1, row["revision"], nodes, difficulty, types, "instructor", actor, request_id=request_id)
            db.commit()
        return self.question(question_id)

    def _retirement(self, question_id, data, action, actor):
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._question_row(db, question_id)
            prior = db.execute("SELECT question_id, action FROM question_bank_events WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if (prior["question_id"], prior["action"]) != (question_id, action):
                    raise Conflict("This request ID already belongs to another change.")
                return self.question(question_id)
            latest = db.execute("SELECT action FROM question_bank_events WHERE question_id=? ORDER BY rowid DESC LIMIT 1", (question_id,)).fetchone()
            retired = bool(latest) and latest["action"] == "retired"
            if action == "retired" and retired:
                raise Conflict("This question is retired already.")
            if action == "restored" and not retired:
                raise Conflict("This question is in the bank already.")
            db.execute("INSERT INTO question_bank_events(id,question_id,action,request_id,actor,created_at) VALUES (?,?,?,?,?,?)",
                       (str(uuid.uuid4()), question_id, action, request_id, actor, timestamp()))
            db.commit()
        return self.question(question_id)

    def retire(self, question_id, data, actor="workspace"):
        """Hide a question from the bank's lists; it keeps its history and Restore brings it back."""
        return self._retirement(question_id, data, "retired", actor)

    def restore(self, question_id, data, actor="workspace"):
        return self._retirement(question_id, data, "restored", actor)

    # -- Where a question was printed, and its versions ----------------------------------------------------------------------
    def set_printed(self, question_id, data, actor="workspace"):
        """Say which assignment, version and printed number a question read from a PDF (or written here) is, or that it is none; its
        grades are then found (`statistics`) and its versions linked with that assignment's. A question retained from grading needs none."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        assignment_id = data.get("assignment_id") or None
        version, number = data.get("exam_version") or "", str(data.get("number") or "").strip()
        if not isinstance(version, str) or (version and (len(version) != 1 or not "A" <= version <= "Z")):
            raise ValueError("A version is one letter, A to Z.")
        if len(number) > 20:
            raise ValueError("The printed number is at most 20 characters.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._question_row(db, question_id)
            if _replayed(db, "question_bank_assessment_links", "question_id", question_id, request_id):
                return self.question(question_id)
            if row["frq_question_id"]:
                raise ValueError("This question was retained from grading: its grading question says where it was printed.")
            if assignment_id and not db.execute("SELECT 1 FROM assignments WHERE id=? AND definition_id=?", (assignment_id, row["definition_id"])).fetchone():
                raise ValueError("Choose an assignment of this course.")
            latest = db.execute("SELECT MAX(revision) FROM question_bank_assessment_links WHERE question_id=?", (question_id,)).fetchone()[0] or 0
            if (data.get("based_on") or 0) != latest:
                raise Conflict("Where this question was printed changed since you opened it. Look at it again.")
            self._print_link(db, question_id, latest + 1, assignment_id, version if assignment_id else "", number if assignment_id else "", actor, request_id)
            db.commit()
        self._wake()
        return self.question(question_id)

    @staticmethod
    def _print_link(db, question_id, revision, assignment_id, version, number, actor, request_id=None):
        db.execute("""INSERT INTO question_bank_assessment_links(question_id,revision,assignment_id,exam_version,number,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?)""", (question_id, revision, assignment_id, version, number, request_id, actor, timestamp()))

    @staticmethod
    def _version_link(db, question_id, group_id, origin, actor, run_id=None, reason="", request_id=None):
        revision = (db.execute("SELECT MAX(revision) FROM question_bank_version_links WHERE question_id=?", (question_id,)).fetchone()[0] or 0) + 1
        db.execute("""INSERT INTO question_bank_version_links(question_id,revision,group_id,origin,run_id,reason,request_id,actor,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?)""", (question_id, revision, group_id, origin, run_id, reason[:1000], request_id, actor, timestamp()))

    def link_versions(self, question_id, data, actor="workspace"):
        """The instructor's version links: `with` names questions that are versions of the same problem as this one (they join its group,
        leaving any other), or `unlink` takes this question out of its group. Neither the model nor a quiz's source changes them later."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        others = data.get("with") or []
        unlink = data.get("unlink") is True
        if not isinstance(others, list) or len(others) > 26 or not all(isinstance(q, str) for q in others) or bool(others) == unlink:
            raise ValueError("Choose the versions to link, or unlink this question.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._question_row(db, question_id)
            owners = {r[0] for r in db.execute("SELECT question_id FROM question_bank_version_links WHERE request_id=?", (request_id,))}
            if owners:
                if not owners <= {question_id, *others}:
                    raise Conflict("This request ID already belongs to another change.")
                return self.question(question_id)
            latest = db.execute("SELECT revision, group_id FROM question_bank_version_links WHERE question_id=? ORDER BY revision DESC LIMIT 1", (question_id,)).fetchone()
            if (data.get("based_on") or 0) != (latest["revision"] if latest else 0):
                raise Conflict("This question's versions changed since you opened it. Look at them again.")
            if unlink:
                if not latest or not latest["group_id"]:
                    raise Conflict("This question is not linked to other versions.")
                self._version_link(db, question_id, None, "instructor", actor, request_id=request_id)
            else:
                others = [q for q in dict.fromkeys(others) if q != question_id]
                found = {r["id"] for r in db.execute(f"SELECT id FROM question_bank_questions WHERE definition_id=? AND id IN ({','.join('?' * len(others))})",
                                                        (row["definition_id"], *others))}
                if not others or found != set(others):
                    raise ValueError("Choose questions of this course's bank.")
                group = latest["group_id"] if latest and latest["group_id"] else str(uuid.uuid4())
                if not latest or latest["group_id"] != group:
                    self._version_link(db, question_id, group, "instructor", actor, request_id=request_id)
                for other in others:
                    current = db.execute("SELECT group_id FROM question_bank_version_links WHERE question_id=? ORDER BY revision DESC LIMIT 1", (other,)).fetchone()
                    if not current or current["group_id"] != group:
                        self._version_link(db, other, group, "instructor", actor, request_id=request_id)
            db.commit()
        return self.question(question_id)

    def _source_groups(self, db, definition_id, dry=False):
        """A drafted quiz's versions of one problem, linked from the quiz itself: its problem number is the same problem on every version."""
        rows = db.execute("""SELECT q.id, q.family FROM question_bank_questions q WHERE q.definition_id=? AND q.origin='quiz_draft' AND q.family!=''
            AND NOT EXISTS (SELECT 1 FROM question_bank_version_links l WHERE l.question_id=q.id) ORDER BY q.created_at, q.rowid""", (definition_id,)).fetchall()
        families = {}
        for row in rows:
            families.setdefault(row["family"], []).append(row["id"])
        written = 0
        for family, members in families.items():
            existing = db.execute("""SELECT l.group_id FROM question_bank_version_links l JOIN question_bank_questions q ON q.id=l.question_id
                WHERE q.definition_id=? AND q.family=? AND l.origin='source' AND l.group_id IS NOT NULL LIMIT 1""", (definition_id, family)).fetchone()
            if not existing and len(members) < 2:
                continue
            written += len(members)
            if not dry:
                group = existing["group_id"] if existing else str(uuid.uuid4())
                for member in members:
                    self._version_link(db, member, group, "source", "automatic", reason="The drafted quiz prints this problem on each of its versions.")
        return written

    def _unversioned(self, db, definition_id):
        """{assignment: [(question, revision)]}: the assessments whose versions the model should link now. An assessment qualifies when its
        questions (retained from grading, or linked by the instructor) span two or more versions and one of them was never linked or looked
        at by a versions run; the run gets every question of that assessment, so the model compares them all."""
        rows = db.execute("""SELECT q.id, (SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id) AS revision,
                COALESCE(a.id, l.assignment_id) AS assignment_id, COALESCE(NULLIF(f.exam_version, ''), l.exam_version, '') AS version,
                EXISTS (SELECT 1 FROM question_bank_version_links v WHERE v.question_id=q.id) AS linked,
                EXISTS (SELECT 1 FROM question_bank_run_questions m JOIN question_bank_runs r ON r.id=m.run_id WHERE m.question_id=q.id AND r.purpose='versions') AS looked
            FROM question_bank_questions q
            LEFT JOIN frq_questions f ON f.id=q.frq_question_id LEFT JOIN assignment_versions av ON av.id=f.assignment_version_id
            LEFT JOIN assignments a ON a.id=av.assignment_id
            LEFT JOIN question_bank_assessment_links l ON l.question_id=q.id AND l.revision=(SELECT MAX(revision) FROM question_bank_assessment_links WHERE question_id=q.id)
            WHERE q.definition_id=? AND q.origin!='quiz_draft'
            AND COALESCE((SELECT action FROM question_bank_events e WHERE e.question_id=q.id ORDER BY e.rowid DESC LIMIT 1), 'restored')!='retired'
            ORDER BY q.created_at, q.rowid""", (definition_id,)).fetchall()
        by = {}
        for row in rows:
            if row["assignment_id"] and row["version"]:
                by.setdefault(row["assignment_id"], []).append(row)
        return {assignment: [(r["id"], r["revision"]) for r in members] for assignment, members in by.items()
                if len({r["version"] for r in members}) >= 2 and any(not r["linked"] and not r["looked"] for r in members)}

    # -- Graded on bubble sheets ------------------------------------------------------------------------------------------------
    @staticmethod
    def _covered(db, definition_id):
        """{(assignment, version, printed number)} already in the bank: retained from grading, or linked by the instructor."""
        covered = set()
        for row in db.execute("""SELECT a.id AS assignment_id, f.exam_version, r.content_json FROM question_bank_questions q
                JOIN frq_questions f ON f.id=q.frq_question_id JOIN assignment_versions v ON v.id=f.assignment_version_id
                JOIN assignments a ON a.id=v.assignment_id JOIN question_bank_revisions r ON r.question_id=q.id
                AND r.revision=(SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id) WHERE q.definition_id=?""", (definition_id,)):
            covered.add((row["assignment_id"], row["exam_version"], str(json.loads(row["content_json"])["number"])))
        for row in db.execute("""SELECT l.assignment_id, l.exam_version, l.number FROM question_bank_assessment_links l JOIN question_bank_questions q ON q.id=l.question_id
                WHERE q.definition_id=? AND l.assignment_id IS NOT NULL
                AND l.revision=(SELECT MAX(revision) FROM question_bank_assessment_links WHERE question_id=l.question_id)""", (definition_id,)):
            covered.add((row["assignment_id"], row["exam_version"], str(row["number"])))
        return covered

    def _bubble_versions(self, db, definition_id):
        """Each bubble-sheet version of the course's plans (the latest answer key) with its keyed questions the bank lacks:
        [{plan_id, assignment_id, title, version, single, key: {number: entry}, missing: [numbers], attempted: run status or None}]."""
        covered = self._covered(db, definition_id)
        found = []
        for row in db.execute("""SELECT f.id AS plan_id, e.assignment_id, a.title, k.key_json FROM frq_bubble_keys k JOIN frq_assignments f ON f.id=k.assignment_id
                JOIN exam_events e ON e.id=f.event_id JOIN assignments a ON a.id=e.assignment_id
                WHERE a.definition_id=? AND k.revision=(SELECT MAX(revision) FROM frq_bubble_keys WHERE assignment_id=k.assignment_id)""", (definition_id,)):
            key = json.loads(row["key_json"])
            versions = key.get("versions") or []
            for version in versions:
                letter = version.get("letter") or ""
                single = len(versions) == 1
                entries = {str(q["number"]): q for q in version.get("questions") or [] if q.get("answer")}
                missing = [n for n in entries if (row["assignment_id"], letter, n) not in covered
                           and not (single and (row["assignment_id"], "", n) in covered)]
                attempted = db.execute("""SELECT status FROM question_bank_runs WHERE definition_id=? AND kind='extract'
                    AND json_extract(input_json, '$.bubbles.plan_id')=? AND json_extract(input_json, '$.bubbles.version')=?
                    ORDER BY created_at DESC, rowid DESC LIMIT 1""", (definition_id, row["plan_id"], letter)).fetchone()
                found.append({"plan_id": row["plan_id"], "assignment_id": row["assignment_id"], "title": row["title"], "version": letter, "single": single,
                              "key": entries, "defaults": key.get("defaults") or {}, "missing": sorted(missing, key=_number_key),
                              "attempted": attempted["status"] if attempted else None})
        return found

    def _blank_pdf(self, plan_id, assignment_id, version, single):
        """The blank of one version, from the plan's confirmed grading template for it (else the assignment's only template named for that
        version, or its only template when the key has one version): its original PDF, or the normalised one, within the reader's limit."""
        import io
        import zipfile
        templates = self.grading.templates
        template_id = None
        try:
            slots = self.grading.assignment_templates(plan_id)["slots"]
            template_id = next((s["template_id"] for s in slots if s.get("confirmed") and s.get("exam_version") == version and s.get("template_id")), None)
            if not template_id and single:
                template_id = next((s["template_id"] for s in slots if s.get("confirmed") and not s.get("exam_version") and s.get("template_id")), None)
        except (ValueError, NotFound, Conflict, KeyError):
            pass
        if not template_id:
            with connect(self.path) as db:
                rows = db.execute("SELECT id, label FROM assignment_templates WHERE assignment_id=? ORDER BY created_at", (assignment_id,)).fetchall()
            named = [r["id"] for r in rows if version and str(r["label"]).startswith(f"Version {version} ")]
            template_id = named[0] if len(named) == 1 else rows[0]["id"] if single and len(rows) == 1 else None
        if not template_id:
            return None, None
        try:
            with zipfile.ZipFile(io.BytesIO(templates.export(template_id, sources_only=True))) as archive:
                pdfs = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
                data = archive.read(pdfs[0]) if len(pdfs) == 1 else None
            if not data or len(data) > MAX_PDF:
                data = templates.read(template_id, "pdf")[0]
        except (ValueError, NotFound, KeyError, OSError, zipfile.BadZipFile):
            return template_id, None
        return template_id, data if data and data.startswith(b"%PDF-") and len(data) <= MAX_PDF else None

    def _sync_bubbles(self, definition_id, endpoint, settings):
        """Bank the bubble-sheet questions of every version whose sheets were graded: read that version's blank once (a PDF reading with
        `bubbles` in its input), then add each keyed question the bank lacks, keyed (`_bank_bubbles`). A version never graded is looked at
        again at most every `BUBBLE_RECHECK` seconds; one read before is never read again by itself."""
        from document_identity.storage import publish
        now = time.monotonic()
        self._bubble_checked = {mark: checked for mark, checked in self._bubble_checked.items() if now - checked < BUBBLE_RECHECK}
        with connect(self.path) as db:
            waiting = [v for v in self._bubble_versions(db, definition_id) if v["missing"] and v["attempted"] is None]
        queued, analyses = 0, {}
        for version in waiting:
            mark = (version["plan_id"], version["version"])
            if time.monotonic() - self._bubble_checked.get(mark, -BUBBLE_RECHECK) < BUBBLE_RECHECK:
                continue
            self._bubble_checked[mark] = time.monotonic()
            if version["plan_id"] not in analyses:
                try:
                    analyses[version["plan_id"]] = self.grading.bubble_analysis(version["plan_id"])
                except (ValueError, NotFound, Conflict) as problem:
                    LOG.warning("Bubble sheets of plan %s could not be read for the question bank: %s", version["plan_id"], problem)
                    analyses[version["plan_id"]] = {}
            graded = sum(v.get("sheets") or 0 for v in analyses[version["plan_id"]].get("versions") or []
                         if v.get("letter") == version["version"] or version["single"])
            if not graded:
                continue
            template_id, data = self._blank_pdf(version["plan_id"], version["assignment_id"], version["version"], version["single"])
            if not data:
                LOG.warning("No readable blank for %s version %s: its bubble-sheet questions wait for a PDF", version["title"], version["version"])
                continue
            checksum = hashlib.sha256(data).hexdigest()
            name = f"{version['title']} Version {version['version']} (blank from its grading template).pdf" if version["version"] else f"{version['title']} (blank from its grading template).pdf"
            with self.rosters.lock(), self.documents.files.files("definition", definition_id, write=True) as root:
                with connect(self.path) as db:
                    upload = db.execute("SELECT id FROM question_bank_uploads WHERE definition_id=? AND source_sha256=?", (definition_id, checksum)).fetchone()
                upload_id = upload["id"] if upload else str(uuid.uuid5(UPLOADS, f"{definition_id}:{checksum}"))
                path = f"question-bank/uploads/{upload_id}/source.pdf"
                if not upload:
                    publish(root / path, data)
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    again = [v for v in self._bubble_versions(db, definition_id) if (v["plan_id"], v["version"]) == mark]
                    if not again or not again[0]["missing"] or again[0]["attempted"] is not None:
                        continue
                    if not upload:
                        db.execute("""INSERT INTO question_bank_uploads(id,definition_id,source_name,source_sha256,source_size,path,request_id,actor,created_at)
                                      VALUES (?,?,?,?,?,?,?,?,?)""", (upload_id, definition_id, name[:200], checksum, len(data), path,
                                                                     f"bank-bubbles:{version['plan_id']}:{version['version']}:{checksum[:12]}", "automatic", timestamp()))
                    bubbles = {"plan_id": version["plan_id"], "assignment_id": version["assignment_id"], "version": version["version"], "template_id": template_id,
                               "key": {n: {k: e.get(k) for k in ("answer", "scoring")} for n, e in version["key"].items()}}
                    self._new_run(db, definition_id, "extract", f"bank-bubbles:{uuid.uuid4()}", {"upload_id": upload_id, "bubbles": bubbles},
                                  endpoint, settings, "automatic", upload_id=upload_id)
                    db.commit()
                    queued += 1
        if queued:
            self._wake()
        return queued

    def _bank_bubbles(self, db, task, result):
        """In a bubble-sheet reading's completing transaction: add each keyed question of that version the bank still lacks, linked to its
        printed number, its correct choice from the key (a thrown-out question, every answer accepted, gets a note instead)."""
        bubbles = json.loads(task["input_json"]).get("bubbles")
        if not bubbles:
            return 0
        covered, added = self._covered(db, task["definition_id"]), 0
        for item in result.get("questions") or []:
            content = dict(item["content"])
            number = str(content.get("number") or "")
            entry = bubbles["key"].get(number)
            if not entry or (bubbles["assignment_id"], bubbles["version"], number) in covered:
                continue
            answer = str(entry.get("answer") or "").upper()
            if content["kind"] == "multiple_choice" and len(answer) == 1 and 0 <= ord(answer) - 65 < len(content["choices"]):
                content["correct_choice"] = answer.lower()
            elif entry.get("scoring") == "either" and len(answer) > 1:
                note = f"Every answer ({', '.join(answer)}) was accepted for this question: its bubble-sheet key gives no single correct choice."
                content["notes"] = "\n".join(n for n in (content.get("notes"), note) if n)
            key = f"pdf:{task['id']}:{item['index']}"
            if db.execute("SELECT 1 FROM question_bank_questions WHERE definition_id=? AND origin_key=?", (task["definition_id"], key)).fetchone():
                continue
            question_id = self._new_question(db, task["definition_id"], "pdf", key, contents.clean(content), "automatic", upload_id=task["upload_id"],
                                             family=f"pdf:{task['id']}:{number}", source={"page": item["page"], "run_id": task["id"], "index": item["index"],
                                                                                          "bubble_key": True}, source_ref=task["id"])
            self._print_link(db, question_id, 1, bubbles["assignment_id"], bubbles["version"], number[:20], "automatic")
            covered.add((bubbles["assignment_id"], bubbles["version"], number))
            added += 1
        return added

    # -- Retained from assessments and drafted quizzes -------------------------------------------------------------------------
    @staticmethod
    def _approved(db, definition_id):
        """{grading question: the revision the bank takes}: every question graded or ready to grade, that is, one of whose rubrics the
        instructor ever approved in Key & Rubric Finalization (grading requires it; requirement, 2026-10-01). The revision is the latest one pinned with an approved rubric, or, while a graded question's
        rubric is being revised, its latest pinned revision."""
        found, approved = {}, {}
        for row in db.execute("""SELECT q.id AS question_id, q.exam_version, a.id AS assignment_id, r.id AS revision_id, r.revision, r.content_json,
                    EXISTS (SELECT 1 FROM frq_rubric_approvals ap WHERE ap.rubric_id=p.rubric_id) AS pinned_approved
                FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id JOIN frq_question_revisions r ON r.id=p.question_revision_id
                JOIN assignment_versions v ON v.id=q.assignment_version_id JOIN assignments a ON a.id=v.assignment_id
                WHERE a.definition_id=? AND EXISTS (SELECT 1 FROM frq_rubric_revisions rr JOIN frq_rubric_approvals ap ON ap.rubric_id=rr.id
                                                     WHERE rr.question_id=q.id)""", (definition_id,)):
            target = approved if row["pinned_approved"] else found
            if row["question_id"] not in target or row["revision"] > target[row["question_id"]]["revision"]:
                target[row["question_id"]] = dict(row)
        return {**found, **approved}

    @staticmethod
    def _quiz_questions(db, definition_id, skip=()):
        """{grading question: (quiz revision ID, problem, version letter, assignment)} for every drafted quiz whose approval was carried out.
        `skip` names grading questions the bank already holds; their quizzes are not read (they are large)."""
        approvals = [dict(row) for row in db.execute("""SELECT a.revision_id, a.result_json FROM quiz_draft_approvals a JOIN quiz_drafts d ON d.id=a.draft_id
            WHERE d.definition_id=? AND a.status='complete' ORDER BY a.created_at""", (definition_id,))]
        mapped = {}
        for approval in approvals:
            result = json.loads(approval["result_json"] or "{}")
            for key, question_id in (result.get("questions") or {}).items():
                number, _, letter = key.partition(":")
                mapped[question_id] = (approval["revision_id"], number, letter, result.get("assignment_id"))
        mapped = {q: v for q, v in mapped.items() if q not in skip}
        quizzes = {}
        for revision_id in {v[0] for v in mapped.values()}:
            row = db.execute("SELECT result_json FROM quiz_draft_revisions WHERE id=?", (revision_id,)).fetchone()
            quizzes[revision_id] = {str(p["number"]): p for p in json.loads(row["result_json"] or "{}").get("quiz", {}).get("problems", [])} if row else {}
        return {q: (revision_id, quizzes.get(revision_id, {}).get(number), letter, assignment)
                for q, (revision_id, number, letter, assignment) in mapped.items()}

    def _waiting_for_text(self, db, definition_id):
        """Approved questions the bank cannot hold yet: their grading revision has no statement (a rubric uploaded without the problem)."""
        retained = {row[0] for row in db.execute("SELECT frq_question_id FROM question_bank_questions WHERE definition_id=? AND frq_question_id IS NOT NULL", (definition_id,))}
        retained |= set(self._quiz_aliases(db, definition_id))
        return [q for q, row in self._approved(db, definition_id).items()
                if q not in retained and not str(json.loads(row["content_json"]).get("problem") or "").strip()]

    def _retain(self, db, definition_id, dry=False):
        """Retain new approved questions, and keep unedited ones equal to their approved source as Faculty converts it now (a later
        approved revision, or a corrected conversion of the same one). Returns how many rows were (`dry`: would be) written."""
        retained = {row["frq_question_id"]: dict(row) for row in db.execute("""SELECT q.id, q.frq_question_id, q.origin, r.revision, r.origin AS revised_by, r.source_ref, r.sha256
            FROM question_bank_questions q JOIN question_bank_revisions r ON r.question_id=q.id
            WHERE q.definition_id=? AND q.frq_question_id IS NOT NULL
            AND r.revision=(SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id)""", (definition_id,))}
        quiz = self._quiz_questions(db, definition_id, skip=retained)
        aliases = self._quiz_aliases(db, definition_id)  # a rebuilt quiz's questions are its approval's, never banked again
        written = 0
        for question_id, (revision_id, problem, letter, assignment) in quiz.items():
            if question_id in retained or not problem or letter not in (problem.get("content") or {}):
                continue
            if dry:
                written += 1
                continue
            try:
                content = contents.from_quiz(problem, letter)
            except ValueError as problem_:
                LOG.warning("Quiz question %s could not be kept in the bank: %s", question_id, problem_)
                continue
            source = {"quiz_revision_id": revision_id, "problem": problem["number"], "version": letter, "tag": str(problem.get("tag") or "")[:200]}
            self._new_question(db, definition_id, "quiz_draft", f"frq:{question_id}", content, "automatic", frq_question_id=question_id,
                               family=f"assignment:{assignment}:{problem['number']}", source=source, source_ref=revision_id)
            retained[question_id] = {"origin": "quiz_draft"}
            written += 1
        for question_id, row in self._approved(db, definition_id).items():
            if question_id in aliases:
                continue
            mine = retained.get(question_id)
            if mine and (mine["origin"] != "assessment" or mine["revised_by"] != "source" or not row["pinned_approved"]):
                continue  # a drafted quiz keeps its approved LaTeX; the instructor's edit stands; a revision waits for its rubric's approval
            frq = json.loads(row["content_json"])
            if not str(frq.get("problem") or "").strip():
                continue
            try:
                content = contents.from_frq(frq)
            except ValueError as problem_:
                LOG.warning("Grading question %s could not be kept in the bank: %s", question_id, problem_)
                continue
            if dry:
                written += int(not mine or mine["sha256"] != _sha(content))
                continue
            if not mine:
                number = content["number"] or str(frq.get("title") or "")
                self._new_question(db, definition_id, "assessment", f"frq:{question_id}", content, "automatic", frq_question_id=question_id,
                                   family=f"assignment:{row['assignment_id']}:{number}" if number else "", source={"version": row["exam_version"]},
                                   source_ref=row["revision_id"])
                written += 1
            elif mine["sha256"] != (digest := _sha(content)):
                self._add_revision(db, mine["id"], mine["revision"] + 1, content, "source", row["revision_id"], None, "automatic", digest=digest)
                written += 1
        return written

    @staticmethod
    def _quiz_aliases(db, definition_id):
        """{rebuilt grading question: the approval's grading question} for every carried-out rebuild of a drafted quiz (QUIZZES
        "REBUILDING AN APPROVED QUIZ"). A rebuild prints the SAME approved problems in a new layout on new grading questions, so each is
        the bank's one copy of its approval's question (problem and version), never a second one (requirement, 2026-10-02: Quiz 4's
        rebuild had been banked again as an assessment, its multiple-choice options lost). Its grades and terms count for that copy.
        `definition_id` None: every course's (the questions being described may come from any)."""
        aliases = {}
        for approved, rebuilt in db.execute("""SELECT a.result_json, r.result_json FROM quiz_draft_rebuilds r JOIN quiz_draft_approvals a ON a.id=r.approval_id
                JOIN quiz_drafts d ON d.id=a.draft_id WHERE (? IS NULL OR d.definition_id=?) AND a.status='complete' AND r.status='complete' ORDER BY r.created_at""",
                (definition_id, definition_id)):
            original = (json.loads(approved or "{}").get("questions") or {})
            for key, question_id in (json.loads(rebuilt or "{}").get("questions") or {}).items():
                if original.get(key) and question_id != original[key]:
                    aliases[question_id] = original[key]
        return aliases

    # -- The automatic pass -------------------------------------------------------------------------------------------------
    def _untagged(self, db, definition_id):
        """Questions to tag automatically, in source order: not retired; no tags, or the model's tags of an older revision that the model
        read differently (a revision that changed only what it never reads, such as the correct choice, keeps them); and no tagging run
        has had their current revision yet (so a failed run waits for the instructor's retry)."""
        rows = db.execute("""SELECT q.id, r.revision, r.content_json, c.content_revision AS tagged_revision FROM question_bank_questions q
            JOIN question_bank_revisions r ON r.question_id=q.id AND r.revision=(SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id)
            LEFT JOIN question_bank_classifications c ON c.question_id=q.id AND c.revision=(SELECT MAX(revision) FROM question_bank_classifications WHERE question_id=q.id)
            WHERE q.definition_id=?
            AND (c.question_id IS NULL OR (c.origin='model' AND c.content_revision < r.revision))
            AND COALESCE((SELECT action FROM question_bank_events e WHERE e.question_id=q.id ORDER BY e.rowid DESC LIMIT 1), 'restored')!='retired'
            AND NOT EXISTS (SELECT 1 FROM question_bank_run_questions m JOIN question_bank_runs t ON t.id=m.run_id
                            WHERE m.question_id=q.id AND m.content_revision=r.revision AND t.purpose='')
            ORDER BY q.family, q.created_at, q.rowid""", (definition_id,)).fetchall()
        waiting = []
        for row in rows:
            if row["tagged_revision"] is not None:
                tagged = db.execute("SELECT content_json FROM question_bank_revisions WHERE question_id=? AND revision=?", (row["id"], row["tagged_revision"])).fetchone()
                if contents.tagger_reads(json.loads(tagged["content_json"])) == contents.tagger_reads(json.loads(row["content_json"])):
                    continue
            waiting.append((row["id"], row["revision"]))
        return waiting

    def sync(self, definition_id=None):
        """The automatic pass for one course or all: read new lecture notes into the outline, retain newly approved questions, and queue
        tagging. Writes the catalog and, for bubble-sheet readings, the version's blank PDF into the definition's verified storage
        (`_sync_bubbles`, under the lifecycle and owner locks); it also links versions. Nothing is sent to an outside service here (the
        dispatcher sends a queued run). Returns how many things changed."""
        endpoint, settings, _ = self._model()
        with connect(self.path) as db:
            courses = [definition_id] if definition_id else [row["id"] for row in db.execute("SELECT id FROM course_definitions ORDER BY id")]
        changed = queued = 0
        for course in courses:
            # A read-only look first: the pass runs every few seconds and takes the lifecycle lock only when it has something to write.
            with connect(self.path) as db:
                if not (self._import_outline(db, course, dry=True) or self._retain(db, course, dry=True) or self._source_groups(db, course, dry=True)
                        or (endpoint and (self._untagged(db, course) or self._unversioned(db, course)))):
                    continue
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                self._definition(db, course)
                changed += self._import_outline(db, course) + self._retain(db, course)
                changed += self._source_groups(db, course)
                if endpoint:
                    for assignment, members in self._unversioned(db, course).items():
                        self._new_run(db, course, "tag", f"bank-versions:{uuid.uuid4()}", {"automatic": True, "assignment_id": assignment}, endpoint,
                                      settings, "automatic", questions=members, purpose="versions")
                        queued += 1
                    waiting = self._untagged(db, course)
                    for start in range(0, len(waiting), TAG_BATCH):
                        batch = waiting[start:start + TAG_BATCH]
                        self._new_run(db, course, "tag", f"bank-tag:{uuid.uuid4()}", {"automatic": True}, endpoint, settings, "automatic", questions=batch)
                        queued += 1
                db.commit()
        for course in courses:
            if endpoint:
                queued += self._sync_bubbles(course, endpoint, settings)
        if queued:
            self._wake()
        return changed + queued

    def process_one(self):
        """One bounded turn of the Inbox worker's rotation: the automatic pass, at most every `SYNC_SECONDS`."""
        now = time.monotonic()
        if now - self._last_sync < SYNC_SECONDS:
            return False
        self._last_sync = now
        try:
            return bool(self.sync())
        except Exception:
            LOG.exception("The question bank's automatic pass stopped; it runs again on a later turn")
            return False

    def _wake(self):
        inbox = getattr(self.grading, "inbox", None)
        if inbox is not None:
            inbox.wake.set()

    # -- Model runs the instructor asks for ---------------------------------------------------------------------------------
    @staticmethod
    def _new_run(db, definition_id, kind, request_id, inputs, endpoint, settings, actor, *, upload_id=None, retry_of=None, questions=(), purpose=""):
        run_id, now = str(uuid.uuid4()), timestamp()
        db.execute("""INSERT INTO question_bank_runs(id,definition_id,kind,purpose,upload_id,retry_of,request_id,input_json,profile_json,actor,status,created_at,updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)""", (run_id, definition_id, kind, purpose, upload_id, retry_of, request_id, _json(inputs),
                                                                    _json({"endpoint": endpoint, "settings": settings}), actor, now, now))
        for question_id, revision in questions:
            db.execute("INSERT INTO question_bank_run_questions(run_id,question_id,content_revision) VALUES (?,?,?)", (run_id, question_id, revision))
        return run_id

    def _ready_model(self):
        endpoint, settings, problem = self._model()
        if not endpoint:
            raise ValueError(problem or "Choose a Rubric drafting and review model in Compute settings first.")
        return endpoint, settings

    def tag(self, definition_id, data, actor="workspace"):
        """Ask the model to tag these questions now, replacing their tags (the instructor's included: they asked)."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        wanted = data.get("question_ids")
        if not isinstance(wanted, list) or not 1 <= len(wanted) <= 500 or not all(isinstance(q, str) for q in wanted):
            raise ValueError("Choose the questions to tag.")
        endpoint, settings = self._ready_model()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._definition(db, definition_id)
            if _replayed(db, "question_bank_runs", "definition_id", definition_id, f"{request_id}:0"):
                return self.view(definition_id)
            rows = {row["id"]: row["revision"] for row in db.execute(f"""SELECT q.id, (SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id) AS revision
                FROM question_bank_questions q WHERE q.definition_id=? AND q.id IN ({','.join('?' * len(wanted))})""", (definition_id, *wanted))}
            if len(rows) != len(set(wanted)):
                raise ValueError("Choose questions of this course's bank.")
            busy = {row[0] for row in db.execute(f"""SELECT m.question_id FROM question_bank_run_questions m JOIN question_bank_runs r ON r.id=m.run_id
                WHERE r.status IN ('pending','running') AND r.purpose='' AND m.question_id IN ({','.join('?' * len(rows))})""", list(rows))}
            if busy:
                raise Conflict("The model is tagging some of these questions already. Wait for it to finish.")
            batch = [(q, rows[q]) for q in dict.fromkeys(wanted)]
            for index, start in enumerate(range(0, len(batch), TAG_BATCH)):
                self._new_run(db, definition_id, "tag", f"{request_id}:{index}", {"automatic": False}, endpoint, settings, actor,
                              questions=batch[start:start + TAG_BATCH])
            db.commit()
        self._wake()
        return self.view(definition_id)

    def retry_run(self, run_id, data, actor="workspace"):
        """Run a failed run again, with today's model: the instructor's explicit retry (nothing failed is ever retried by itself)."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        endpoint, settings = self._ready_model()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, run_id)
            if _replayed(db, "question_bank_runs", "retry_of", run_id, request_id):
                return self.view(run["definition_id"])
            if run["status"] != "failed":
                raise Conflict("Only a run that failed or was stopped can be run again.")
            if db.execute("SELECT 1 FROM question_bank_runs WHERE retry_of=? AND status!='failed'", (run_id,)).fetchone():
                raise Conflict("This run was run again already.")
            questions = [(row["question_id"], row["revision"]) for row in db.execute("""SELECT m.question_id,
                    (SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=m.question_id) AS revision
                FROM question_bank_run_questions m WHERE m.run_id=?""", (run_id,))]
            self._new_run(db, run["definition_id"], run["kind"], request_id, json.loads(run["input_json"]), endpoint, settings, actor,
                          upload_id=run["upload_id"], retry_of=run_id, questions=questions, purpose=run["purpose"])
            db.commit()
        self._wake()
        return self.view(run["definition_id"])

    def cancel_run(self, run_id, data, actor="workspace"):
        """Stop a run that waits or is working: its request in flight is closed, nothing it found is applied."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, run_id)
            db.execute("""UPDATE question_bank_runs SET status='failed', cancelled=1, error='Stopped by the instructor.', updated_at=?
                          WHERE id=? AND status IN ('pending','running')""", (timestamp(), run_id))
            db.commit()
        return self.view(run["definition_id"])

    @staticmethod
    def _run(db, run_id):
        if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
            raise NotFound("Run not found.")
        row = db.execute("SELECT * FROM question_bank_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise NotFound("Run not found.")
        return dict(row)

    # -- PDFs read for questions ----------------------------------------------------------------------------------------------
    def upload(self, definition_id, data, filename, request_id, actor="workspace"):
        """Keep a PDF of questions (an old exam, a problem set) and ask the model to read its questions out of it. Nothing enters the bank
        until the instructor adds what it found. The same file uploaded again returns its first upload."""
        from document_identity.storage import publish
        request_id = _request(request_id)
        name = _file_name(PurePosixPath(str(filename or "").replace("\\", "/")).name)[:200]
        if not isinstance(data, bytes) or not data:
            raise ValueError("Choose the PDF.")
        if len(data) > MAX_PDF:
            raise ValueError(f"That file is larger than {MAX_PDF // 1024**2} MB, the most the PDF reader opens. Save a smaller copy (a lower scan resolution, or print to PDF), then upload that.")
        if not data.startswith(b"%PDF-"):
            if name.lower().endswith((".docx", ".doc")):
                raise ValueError("Upload the questions as a PDF: in Word, choose File → Save As (or Export) → PDF, then upload that file.")
            raise ValueError("Upload the questions as a PDF.")
        endpoint, settings = self._ready_model()
        checksum = hashlib.sha256(data).hexdigest()
        def existing(db):
            prior = db.execute("SELECT id,definition_id,source_sha256 FROM question_bank_uploads WHERE request_id=?", (request_id,)).fetchone()
            if prior and (prior["definition_id"] != definition_id or prior["source_sha256"] != checksum):
                raise Conflict("This request ID already belongs to another upload.")
            return prior or db.execute("SELECT id FROM question_bank_uploads WHERE definition_id=? AND source_sha256=?", (definition_id, checksum)).fetchone()
        with connect(self.path) as db:
            self._definition(db, definition_id)
            prior = existing(db)
        if prior:
            return {**self.view(definition_id), "upload_id": prior["id"], "repeated": True}
        upload_id = str(uuid.uuid5(UPLOADS, f"{definition_id}:{checksum}"))
        path = f"question-bank/uploads/{upload_id}/source.pdf"
        with self.rosters.lock(), self.documents.files.files("definition", definition_id, write=True) as root:
            publish(root / path, data)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                prior = existing(db)
                if prior:
                    return {**self.view(definition_id), "upload_id": prior["id"], "repeated": True}
                db.execute("""INSERT INTO question_bank_uploads(id,definition_id,source_name,source_sha256,source_size,path,request_id,actor,created_at)
                              VALUES (?,?,?,?,?,?,?,?,?)""", (upload_id, definition_id, name, checksum, len(data), path, request_id, actor, timestamp()))
                self._new_run(db, definition_id, "extract", f"{request_id}:read", {"upload_id": upload_id}, endpoint, settings, actor, upload_id=upload_id)
                db.commit()
        self._wake()
        return {**self.view(definition_id), "upload_id": upload_id, "repeated": False}

    def extraction(self, upload_id):
        """What the model found in an uploaded PDF (its latest finished reading), each question marked when it is in the bank already."""
        if not isinstance(upload_id, str) or not IDENTIFIER.fullmatch(upload_id):
            raise NotFound("Upload not found.")
        with connect(self.path) as db:
            upload = db.execute("SELECT * FROM question_bank_uploads WHERE id=?", (upload_id,)).fetchone()
            if not upload:
                raise NotFound("Upload not found.")
            run = db.execute("""SELECT * FROM question_bank_runs WHERE upload_id=? AND status='complete' ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                             (upload_id,)).fetchone()
            questions = []
            if run:
                bank = self._latest(db, upload["definition_id"])
                self._sources(db, bank)
                questions = self._marked(db, upload_id, dict(run), _bank_index(bank))
        result = json.loads(run["result_json"]) if run else {"questions": [], "notes": [], "pages": 0}
        return {"upload_id": upload_id, "definition_id": upload["definition_id"], "source_name": upload["source_name"], "run_id": run["id"] if run else None,
                "rule": result.get("rule", ""), "pages": result.get("pages", 0), "notes": result.get("notes", []), "questions": questions}

    @staticmethod
    def _marked(db, upload_id, run, index):
        """The reading's problems, each marked when it is in the bank already: added from this reading (`question_id`), from an earlier
        reading of the same PDF (`earlier`: the same page, printed number and place among that number's problems on the page), or from
        another source (`elsewhere`, that question's label). Requirement, 2026-10-03: Add all must never copy what a blank exam or quiz
        already has in the bank from grading. Another source is the same problem when the PDF is an assessment's version and the bank holds
        that version's problem under the same number (Exam 1's blanks: problems 18-25 retained from grading), or when its text is word for
        word a bank question's (not for a problem that relies on a figure). The PDF is a version when questions added from it are linked to
        one (`printed`), or when at least two of its problems are word for word that version's under the same numbers and none of its other
        text-only problems is anything else (Quiz 2 and 3's blanks). Replayed on the live bank 2026-10-03: the 55 problems of Exam 1, Quiz 2
        and Quiz 3 already banked were all found; none of a textbook's 626 exercises was; a rule of similar text (90%) would have taken 15
        textbook exercises for WebAssign questions with other numbers."""
        items = json.loads(run["result_json"] or "{}").get("questions", [])
        mine = index["by_upload"].get(upload_id, [])
        added = {q["origin_key"]: q["id"] for q in mine}
        earlier, older = {}, {}
        for q in mine:
            source = q["source"]
            if source.get("run_id") and source["run_id"] != run["id"] and type(source.get("index")) is int:
                older.setdefault(source["run_id"], {})[source["index"]] = q["id"]
        for old_run, indices in older.items():
            kept = db.execute("SELECT result_json FROM question_bank_runs WHERE id=?", (old_run,)).fetchone()
            for key, item in _placed(json.loads(kept["result_json"] or "{}").get("questions", []) if kept else []):
                if item["index"] in indices:
                    earlier.setdefault(key, indices[item["index"]])
        places = {index["printed_of"][q["id"]][:2] for q in mine if q["id"] in index["printed_of"]}
        if len(places) != 1:
            places = _version_by_text(items, upload_id, index)
        version = next(iter(places)) if len(places) == 1 else None
        marked = []
        for key, item in _placed(items):
            own = added.get(f"pdf:{run['id']}:{item['index']}")
            if own or key in earlier:
                marked.append({**item, "question_id": own or earlier[key], **({} if own else {"earlier": True})})
                continue
            number = str(item["content"].get("number") or "").strip()
            hit = index["by_number"].get((*version, number)) if version and number else None
            text = None if hit or item.get("figures") else contents.text_key(item["content"])
            if text:
                hit = next(((q, label) for q, label in index["by_text"].get(text, []) if q["upload_id"] != upload_id), None)
            marked.append({**item, "question_id": hit[0]["id"], "elsewhere": hit[1]} if hit else {**item, "question_id": None})
        return marked

    def accept(self, upload_id, data, actor="workspace"):
        """Add the questions the instructor chose from a PDF's reading to the bank (each once); the model then tags them automatically."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        chosen = data.get("indices")
        if not isinstance(chosen, list) or not chosen or len(chosen) > MAX_ACCEPT or not all(type(i) is int for i in chosen):
            raise ValueError("Choose the questions to add.")
        printed = data.get("printed") or {}
        if not isinstance(printed, dict):
            raise ValueError("Say which assignment and version the PDF is, or nothing.")
        assignment_id, version = printed.get("assignment_id") or None, printed.get("exam_version") or ""
        if not isinstance(version, str) or (version and (len(version) != 1 or not "A" <= version <= "Z")):
            raise ValueError("A version is one letter, A to Z.")
        kept = {}  # index: the question it is in the bank as, added now or before
        with self.rosters.lock(), connect(self.path) as db:
            # QUESTION-BANK: compare the displayed reading and current bank while
            # holding the lifecycle lock, including questions added by another request.
            found = self.extraction(upload_id)
            if found["run_id"] != data.get("run_id"):
                raise Conflict("The PDF was read again since you opened it. Look at what it found again.")
            items = {item["index"]: item for item in found["questions"]}
            if any(i not in items for i in chosen):
                raise ValueError("Choose questions the reading found.")
            db.execute("BEGIN IMMEDIATE")
            if assignment_id and not db.execute("SELECT 1 FROM assignments WHERE id=? AND definition_id=?", (assignment_id, found["definition_id"])).fetchone():
                raise ValueError("Choose an assignment of this course.")
            upload = dict(db.execute("SELECT * FROM question_bank_uploads WHERE id=?", (upload_id,)).fetchone())
            for index in dict.fromkeys(chosen):
                item = items[index]
                if item.get("earlier") or item.get("elsewhere"):
                    continue  # in the bank already: added from an earlier reading of this PDF, or from another source
                key = f"pdf:{found['run_id']}:{index}"
                prior = db.execute("SELECT id FROM question_bank_questions WHERE definition_id=? AND origin_key=?", (found["definition_id"], key)).fetchone()
                if prior:
                    kept[index] = prior["id"]
                    continue
                content, section, number = dict(item["content"]), item.get("section") or "", item["content"]["number"]
                if item.get("check"):
                    # What the reading asked the instructor to check stays with the question until they edit it away.
                    content["notes"] = "\n".join(filter(None, [content["notes"], *(f"Check: {c}" for c in item["check"])]))[:contents.LIMITS["notes"]]
                question_id = self._new_question(db, found["definition_id"], "pdf", key, contents.clean(content), actor, upload_id=upload_id,
                                                 family=f"pdf:{found['run_id']}:{section + ':' if section else ''}{number}" if number else "",
                                                 source={"page": item["page"], "run_id": found["run_id"], "index": index, **({"section": section} if section else {})},
                                                 source_ref=found["run_id"], request_id=f"{request_id}:{index}")
                kept[index] = question_id
                if assignment_id:
                    # The PDF is that assignment's version: each problem is printed there under its own number.
                    self._print_link(db, question_id, 1, assignment_id, version, number[:20], actor)
            db.commit()
        # The figures the reading boxed become the questions' pictures (a retry of the same request finishes what is missing).
        cuts = [{"question_id": question_id, "request_id": f"bank-figure:{question_id}:{n}", "page": figure["page"], "region": figure["region"],
                 "caption": figure.get("description") or ""} for index, question_id in kept.items() for n, figure in enumerate(items[index].get("figures") or [])]
        pictures, missed = 0, 0
        if cuts:
            try:
                pictures = self._cut_figures(upload, cuts, actor)
            except Exception as problem:  # noqa: BLE001 - the questions are added; a picture that could not be cut is cut by hand
                LOG.warning("question bank: figures of upload %s were not cut: %s", upload_id, problem)
                missed = len(cuts)
        self._wake()
        return {**self.view(found["definition_id"]), "extraction": self.extraction(upload_id), "pictures": pictures, "pictures_missed": missed}

    def read_again(self, upload_id, data, actor="workspace"):
        """Read an uploaded PDF again with today's reader, on the instructor's request (2026-10-03: the reader changed after a textbook's
        exercises were read). The earlier reading and every question added from it stay; the new reading marks the problems already added."""
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        if not isinstance(upload_id, str) or not IDENTIFIER.fullmatch(upload_id):
            raise NotFound("Upload not found.")
        endpoint, settings = self._ready_model()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            upload = db.execute("SELECT * FROM question_bank_uploads WHERE id=?", (upload_id,)).fetchone()
            if not upload:
                raise NotFound("Upload not found.")
            if _replayed(db, "question_bank_runs", "upload_id", upload_id, request_id):
                return self.view(upload["definition_id"])
            if db.execute("SELECT 1 FROM question_bank_runs WHERE upload_id=? AND status IN ('pending','running')", (upload_id,)).fetchone():
                raise Conflict("The model is reading this PDF already.")
            latest = db.execute("SELECT input_json FROM question_bank_runs WHERE upload_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (upload_id,)).fetchone()
            self._new_run(db, upload["definition_id"], "extract", request_id, json.loads(latest["input_json"]) if latest else {"upload_id": upload_id},
                          endpoint, settings, actor, upload_id=upload_id)
            db.commit()
        self._wake()
        return self.view(upload["definition_id"])
