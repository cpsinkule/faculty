"""GRADING "DR COPIES": the DR center's copy of a drafted quiz is graded on its own pages, with its version's own rubrics.

The DR center prints the accessible Word copy of one version (QUIZZES
"ACCESSIBLE COPY"): the same problems, one to a page, on plain paper without labels. So the scanned paper has other pages than the
printed version (four sides for Quiz 7's two), and none of the version's answer areas lie on it.

- THE COPY IS A DOCUMENT OF ITS OWN (`DocumentService.register_dr_copy`): a label-only document, as the stamps-only one papers
  without labels are registered under, that names the version it copies. Its papers count as that version's papers everywhere a
  paper's document is asked (`exam_workflow.grading.PAPER_DOCUMENT`): the version is settled by the document, the version's
  questions and rubrics grade it, and its grades are that version's grades.
- ITS OWN BLANK (`dr_copy`): the Word file, made again from the approved revision exactly as it was made for AIM, rendered here
  to a blank of one page per problem. The rendering is not the DR center's print (measured 2026-11-10 on the first scan: Word
  there sets it in another font, and its pages aligned with ours at .12 to .40 against the .55 pass mark, and told the pages
  apart on only 13 of 20), so nothing is aligned with it. It says how many sides a paper has and which way up a sheet is (20 of
  20 sheets fed upside down turned upright, none of 80 upright sheets turned).
- ITS OWN ANSWER AREAS (`dr_copy_areas`): problem k is the whole of sheet k, cut without alignment (`extraction.extract(sheet=True)`),
  so no area is drawn, read or confirmed. On sheet 1 the top margin, where the name and UIN are written, is masked from hosted
  readers as a name line above a printed heading is left out of an ordinary area.

- PAGE CODES (`dr_stamped_copy`, the STAMPS block below): the Word file handed out prints each page's own Data Matrix, the
  same for every student. The codes are copy 1's of a job of unnamed copies of a second, stamped document of the copy; each
  paper that comes back on them takes a copy of its own and is named from the name written on it.

Nothing here changes a question, a rubric or an area of the printed versions. A copy is made when its papers are first registered
(`UnlabeledService.register`), and by the worker once its Word file went up to AIM (`dr_copy_one`), so the scan's papers are
counted at the copy's sides before anyone is asked.
"""
import json
import logging
import time
import uuid

from werkzeug.exceptions import Conflict

from document_identity import accessible_quiz
from document_identity.layouts import CORNERS_PROFILE, MM, barcode_boxes
from rosters import connect
from teach_inbox.service import sha
from . import locations

LOG = logging.getLogger(__name__)

RULE = "dr-copy-sheets-v1"  # problem k is the whole of sheet k; sheet 1's top margin is masked
NAME_BAND = .1              # the top margin of sheet 1 (Word's one inch is .09 of the page): the copy prints nothing there
LOOK_EVERY = 900            # seconds between two looks at one quiz whose copy could not be made
SHEET = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
# STAMPS (requirement, 2026-11-10). The Word file
# prints copy 1's page codes of a job of COPIES unnamed copies; each paper that comes back on them takes a copy of its own
# (QUIZZES "PHOTOCOPIED STAMPS") and is named from the name written on it (QUIZZES "NAMES ON UNNAMED STAMPED COPIES").
STAMP_PROFILE = CORNERS_PROFILE     # the peel-off labels' mark: 10 mm, at the two corners, for paper printed and scanned elsewhere
COPIES = 20                         # the job's copies: one for each paper that may come back on the file's codes
JOB = "unlabeled:dr-copy:"          # the job's request ID, then its document: a registration, never a printing (`origins`)
PAPER = (612, 792)                  # Letter, the page pandoc's Word file has
CHECKED_REMEMBERED = 64


def stamp_places():
    """(the mark's size in mm, [(left, top) in mm from the page's top left corner]) of the STAMP_PROFILE's marks on the Word page."""
    boxes = barcode_boxes(*PAPER, STAMP_PROFILE)
    return boxes[0][2] / MM, [(x / MM, (PAPER[1] - y - size) / MM) for x, y, size in boxes]


class DrCopiesMixin:
    @staticmethod
    def dr_copies(db, assignment_id):
        """{document: {version_id, template_id, pages, ...}} of the assignment's DR copies, oldest first."""
        return {row[0]: json.loads(row[1])["dr_copy"] for row in db.execute(
            """SELECT id,compiler_json FROM assignment_versions WHERE assignment_id=? AND json_extract(compiler_json,'$.dr_copy.version_id') IS NOT NULL
               ORDER BY created_at,id""", (assignment_id,))}

    def _dr_copy_source(self, assignment_id, term_id, letter=None):
        """What this term's DR copy of the assignment is a copy of: the approved quiz, its version `letter` (else the first version a
        screen reader can read, the one Send to AIM uploads, `quiz_accessible_copies`), the document that holds that version's
        questions now and the sections of its assessment; `made` and `stamped`, the newest document already registered for this very
        copy (the same version's document, approved revision and Word file rule) without and with page codes. None for an assignment
        that was not drafted in Faculty, a version with no readable copy, or one whose questions share no one document. The Word file
        is tried outside the lifecycle lock, as on the DR quizzes tab."""
        with self.rosters.lock(), connect(self.path) as db:
            row = next((row for row in db.execute(
                """SELECT a.id,a.revision_id,a.input_json,a.result_json,r.result_json AS revision FROM quiz_draft_approvals a
                   JOIN quiz_draft_revisions r ON r.id=a.revision_id WHERE a.term_id=? AND a.status='complete'
                   AND json_extract(a.result_json,'$.assignment_id')=? ORDER BY a.created_at DESC,a.rowid DESC""", (term_id, assignment_id))
                if json.loads(row["input_json"]).get("rule") == "quiz-draft-v1"), None)
        if not row:
            return None
        quiz = json.loads(row["revision"])["quiz"]
        ready = lambda found: not accessible_quiz.readiness(row["revision_id"], quiz, found, quiz["title_line"])
        letter = (letter if letter in quiz["versions"] and ready(letter) else None) if letter else next((found for found in quiz["versions"] if ready(found)), None)
        if not letter:
            return None
        with self.rosters.lock(), connect(self.path) as db:
            plan_id = self._quiz_current(db, row)["plan_id"]
            documents = db.execute(
                """SELECT DISTINCT q.assignment_version_id,v.label FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                   JOIN assignment_versions v ON v.id=q.assignment_version_id WHERE p.assignment_id=? AND q.exam_version=?""", (plan_id, letter)).fetchall()
            if len(documents) != 1:
                return None
            mine = [(document, copy) for document, copy in reversed(list(self.dr_copies(db, assignment_id).items()))
                    if copy["version_id"] == documents[0][0] and copy.get("revision_id") == row["revision_id"]
                    and copy.get("accessible_rule") == accessible_quiz.RULE]
            sections = [found[0] for found in db.execute(
                "SELECT s.section_id FROM exam_event_sections s JOIN frq_assignments p ON p.event_id=s.event_id WHERE p.id=? ORDER BY 1", (plan_id,))]
        return {"approval_id": row["id"], "revision_id": row["revision_id"], "quiz": quiz, "letter": letter,
                "version_id": documents[0][0], "label": documents[0][1], "sections": sections,
                "made": next((document for document, copy in mine if not copy.get("stamps")), None),
                "stamped": next((document for document, copy in mine if copy.get("stamps") == accessible_quiz.STAMPS), None)}

    def _dr_copy_blank(self, assignment_id, source):
        """(the blank of this copy, its sides, the Word file's sha256): the Word file without page codes, made again and rendered
        here, kept once per version and file. Refused when it does not come out one page per problem."""
        # Rendering takes seconds: outside the lifecycle lock, as every template conversion.
        try:
            content, info = accessible_quiz.build(source["quiz"], source["letter"], source["quiz"]["title_line"])
        except accessible_quiz.Unavailable as error:
            raise ValueError("The DR copy could not be made again: " + "; ".join(error.reasons) + ".") from error
        checksum = sha(content)
        request = str(uuid.uuid5(uuid.NAMESPACE_URL, f"faculty:dr-copy:{source['version_id']}:{checksum}"))
        files, label = {"dr-copy.docx": accessible_quiz.for_rendering(content)}, f"DR copy · {source['label'][:120]}"
        try:
            template = self.templates.upload(assignment_id, label, request, files)
        except Conflict:  # that label names an earlier Word file of this version: the changed file gets its own blank
            template = self.templates.upload(assignment_id, f"{label} · {checksum[:8]}", request, files)
        if template["page_count"] != info["problems"]:
            raise ValueError(f"The DR copy prints one problem on each page, but its {info['problems']} problems came out on {template['page_count']} "
                             "pages here, so its pages cannot be told from the problems.")
        return template, info["problems"], checksum

    def _dr_copy_evidence(self, source, checksum):
        return {"letter": source["letter"], "approval_id": source["approval_id"], "revision_id": source["revision_id"],
                "accessible_rule": accessible_quiz.RULE, "file_sha256": checksum, "rule": RULE}

    def dr_copy(self, assignment_id, term_id):
        """The document a paper of this term's DR copy found WITHOUT page codes is registered under, made now when it is not there
        yet; None when the assignment has no DR copy (`_dr_copy_source`). A copy that cannot be made says why (ValueError, Conflict)."""
        source = self._dr_copy_source(assignment_id, term_id)
        if not source or source["made"]:
            return source["made"] if source else None
        template, pages, checksum = self._dr_copy_blank(assignment_id, source)
        document = self.exams.documents.register_dr_copy(assignment_id, source["version_id"], template["id"], pages,
                                                         self._dr_copy_evidence(source, checksum))
        LOG.info("Assignment %s: DR copy of version %s registered as %s", assignment_id, source["letter"], document["id"])
        return document["id"]

    def dr_stamped_copy(self, assignment_id, term_id, letter, approval_id):
        """STAMPS: the Word file of this approved quiz's version WITH its page codes, or None where it has no DR copy of its own
        (another approval is the term's current one, no readable copy). The first request registers the copy's document (a label
        for every page) and its job of COPIES unnamed copies for the term, renders the job so its stamps are issued, and checks
        the file: rendered here, every page must show its own code at every place and no other. The file prints copy 1's codes;
        the same bytes come back for every later request. A file that cannot be made or checked says why (ValueError, Conflict)."""
        source = self._dr_copy_source(assignment_id, term_id, letter)
        if not source or source["approval_id"] != approval_id:
            return None
        documents, document = self.exams.documents, source["stamped"]
        if not document:
            template, pages, checksum = self._dr_copy_blank(assignment_id, source)
            evidence = {**self._dr_copy_evidence(source, checksum), "stamps": accessible_quiz.STAMPS, "stamp_profile": STAMP_PROFILE, "copies": COPIES}
            document = documents.register_dr_copy(assignment_id, source["version_id"], template["id"], pages, evidence)["id"]
            LOG.info("Assignment %s: DR copy of version %s with page codes registered as %s", assignment_id, source["letter"], document)
        with self.rosters.lock(), connect(self.path) as db:
            batch = db.execute("SELECT id FROM print_batches WHERE request_id=?", (JOB + document,)).fetchone()
        if not batch:
            if not source["sections"]:
                raise ValueError("This quiz has no sections yet, so its DR copy has no students to be read for.")
            batch = documents.create_batch(document, None, COPIES, JOB + document, term_id=term_id, section_ids=source["sections"], actor="automatic")
        documents.build_now(batch["id"])
        with self.rosters.lock(), connect(self.path) as db:
            stamps = db.execute("""SELECT p.token,p.symbology FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id
                WHERE c.print_batch_id=? AND c.copy_number=1 AND p.symbology!='sequence' AND p.state='issued' ORDER BY p.page_number""", (batch["id"],)).fetchall()
            pages = self.dr_copies(db, assignment_id)[document]["pages"]
        if len(stamps) != pages:
            raise Conflict("The DR copy's page codes are not issued yet. Try again in a minute.")
        from document_identity import media
        size, places = stamp_places()
        pictures = [media.barcode_png(row["token"], row["symbology"], STAMP_PROFILE) for row in stamps]
        try:
            content, _ = accessible_quiz.build(source["quiz"], source["letter"], source["quiz"]["title_line"],
                                               {"pictures": pictures, "size_mm": size, "places_mm": places})
        except accessible_quiz.Unavailable as error:
            raise ValueError("The DR copy could not be made with its page codes: " + "; ".join(error.reasons) + ".") from error
        self._dr_stamps_check(content, [row["token"] for row in stamps], len(places))
        return content

    def _dr_stamps_check(self, content, tokens, places):
        """The stamped Word file rendered here shows, on each page, that page's own code at each of its places and no other code.
        Checked once per file in each process (the rendering takes seconds)."""
        checked, checksum = self.__dict__.setdefault("_dr_stamps_checked", {}), sha(content)
        if checksum in checked:
            return
        import io
        from PIL import Image
        from document_identity import media
        from document_identity.template_conversion import convert_template
        pages = convert_template({"dr-copy.docx": accessible_quiz.for_rendering(content)})["pages"]
        seen = [{found["payload"]: len(found["locations"]) for found in media.decode_page(Image.open(io.BytesIO(page["png"])))} for page in pages]
        if seen != [{token: places} for token in tokens]:
            raise ValueError("The DR copy's page codes did not come out on their own pages when the file was rendered here, so it is not handed out with them.")
        if len(checked) >= CHECKED_REMEMBERED:
            checked.pop(next(iter(checked)))
        checked[checksum] = True

    def dr_copy_one(self):
        """The worker's turn: one DR copy made, of a current term's quiz whose Word file went up to AIM (SCHEDULING "SEND TO AIM"; the
        upload's own receipt) and that has none yet, with or without page codes. One quiz is looked at per turn, each again after
        `LOOK_EVERY`. (A file that went up with page codes has its copy from the request that made it.)"""
        now, looked = time.monotonic(), self.__dict__.setdefault("_dr_copy_looked", {})
        with self.rosters.lock(), connect(self.path) as db:
            due = next((key for key in (tuple(row) for row in db.execute(
                """SELECT DISTINCT a.term_id,json_extract(a.result_json,'$.assignment_id') FROM aim_sends s
                   JOIN quiz_draft_approvals a ON a.id=s.approval_id JOIN course_terms t ON t.id=a.term_id
                   WHERE s.kind='upload' AND a.status='complete' AND t.is_current=1 AND t.deleted_at IS NULL ORDER BY 1,2"""))
                if now - looked.get(key, -LOOK_EVERY) >= LOOK_EVERY), None)
        if not due:
            return False
        looked[due] = now
        try:
            source = self._dr_copy_source(due[1], due[0])
            return bool(source and not source["made"] and not source["stamped"] and self.dr_copy(due[1], due[0]))
        except (ValueError, Conflict) as error:
            LOG.warning("The DR copy of assignment %s could not be made: %s", due[1], getattr(error, "description", error))
            return False

    def dr_copy_areas(self, db, plan, question, copy, shared):
        """The answer area of `question` on a paper of the DR copy `copy`: the whole sheet its problem is printed on, the k-th sheet
        for the k-th printed number of its version. Refused where the version's questions are not one to a sheet of the copy."""
        sheets, key = shared.setdefault("dr_copy_sheets", {}), (plan["id"], question["assignment_version_id"])
        if key not in sheets:
            numbers = {}
            for row in db.execute("""SELECT p.question_id,q.question_key,r.content_json FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                    JOIN frq_question_revisions r ON r.id=p.question_revision_id WHERE p.assignment_id=? AND q.assignment_version_id=?""", key):
                numbers[row["question_id"]] = locations.hint({"content": json.loads(row["content_json"]), "question_key": row["question_key"]})[0]
            ordered = sorted(set(numbers.values()) - {""}, key=int)
            sheets[key] = {qid: ordered.index(number) + 1 for qid, number in numbers.items()} if len(ordered) == len(numbers) else None
        found = sheets[key]
        if not found or len(found) != copy["pages"]:
            raise Conflict(f"The DR copy prints one problem on each of its {copy['pages']} pages, but this version's questions are not one to a page "
                           "(a question without its printed number, or another number of questions). Grade this paper by hand.")
        page = found[question["question_id"]]
        return [{"page": page, "region": dict(SHEET), "privacy_masks": [[0.0, 0.0, 1.0, NAME_BAND]] if page == 1 else []}]
