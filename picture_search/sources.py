"""What picture search indexes (PICTURE-SEARCH "SOURCES"): each source lists its pictures from the catalog, reads one, says
which words Faculty already stores about it, and decides at search time what may be shown.

A source never copies anything: an entry is its row's id (and a page number for a PDF), and a result is a route Faculty
already serves. Every source applies its owner's own rule for what is gone (a scan deleted in the Inbox, a document removed
from an absence, a retired question's picture, a removed notes edition), both when it lists and when it shows.
"""
import hashlib
import json
import re
from urllib.parse import quote

from course_groups import navigation_id
from rosters import connect
from teach_inbox.service import PRESENT

HEX = re.compile(r"[0-9a-f]{6,32}")

HIDDEN_REVIEW = ("ignored", "duplicate", "rejected")
ARCHIVED = ("archived", "purge_pending")  # course_storage: no working copy on NVMe
WORDS_CHARS = 1500  # of the stored words, what is embedded: one request of this size is what the worker's share of the GPU takes


def marks(values):
    return ",".join("?" * len(values))


# How stored words are put together. It is part of their mark, so what was worded another way is read again.
# 2 (2026-10-10): a label alone is not words, and a page with nothing on it is not indexed.
WORDING = "2"


def words_hash(words):
    return hashlib.sha256(f"{WORDING}\n{words}".encode()).hexdigest()


def tidy(words):
    """Stored words as one line, cut to what is embedded."""
    return " ".join(str(words or "").split())[:WORDS_CHARS]


def worded(label, *about):
    """What Faculty stores about a picture, under its label, as the words to embed; "" when there is nothing but the label.

    A label says what kind of thing a picture is and where it sits ("Course notes Front matter", "Exam 1 Version A, page
    3"), which every page of its kind shares. Alone it is near any vague description: requirement, 2026-10-10, "this
    'front matter' page keeps showing up as the top hit for many different searches" (an empty page whose only words were
    its label). Such a page has no words and is found by how it looks."""
    about = [part for part in about if str(part or "").strip()]
    return tidy("\n".join([label, *about])) if about else ""


def blank(picture):
    """Whether a picture has nothing on it, by the measure that calls a scanned side blank (`document_identity.media`
    `extract_scan`): fewer than one pixel in 10,000 darker than 230. An empty page looks like nothing in particular, so it is
    the nearest picture to any description that fits nothing else."""
    import io

    import numpy as np
    from PIL import Image
    try:
        image = Image.open(io.BytesIO(picture))
        if image.mode not in ("L", "RGB"):
            image = image.convert("RGB")
        return bool(np.mean(np.asarray(image) < 230) < .0001)
    except Exception:  # noqa: BLE001 - not for this to judge: the worker refuses a picture it cannot read
        return False


class Source:
    """One kind of picture. `name` is the index's `source`; `kind` is what a result says it is."""
    name = kind = ""

    def units(self, db):
        """Everything worth embedding now, the first to do first, from the catalog alone (no file is read to list): dicts
        with `ref` (the id in the index of the unit's first picture), `sha256` (of the file it is drawn from, or another key
        that changes when the file does) and `words_key` (changes when the words stored about it do; "" when it has none)."""
        raise NotImplementedError

    def read(self, unit):
        """[(ref, PNG or JPEG bytes, words)] of the unit's pictures, its own ref among them: one picture, or a few pages of
        one PDF. `words` is what Faculty stores about that picture, tidied; "" for none."""
        raise NotImplementedError

    def existing(self, db):
        """The refs the catalog still has, shown or not: what the index holds beyond these is dropped."""
        raise NotImplementedError

    def shown(self, db, refs):
        """{ref: result} of those that may be shown now. A result's `same` names what it is a copy of (a printed page of an
        assignment): results with one `same` are one result."""
        raise NotImplementedError

    def same(self, db):
        """{ref: `same`} of everything of this source that is a copy of something, from the catalog alone."""
        return {}

    def gathered(self, db):
        """{`same`: {"count", "link"}}: how many of this source's pictures are copies of each thing and may be shown now, and
        the page that opens the newest."""
        return {}


PRINTED = """JOIN page_stamps ps ON ps.id=sp.page_stamp_id JOIN printed_copies pc ON pc.id=ps.printed_copy_id
    JOIN print_batches pb ON pb.id=pc.print_batch_id JOIN assignment_versions av ON av.id=pb.assignment_version_id"""


def printed_page(assignment_id, page):
    """What a printed page of an assignment is called wherever it turns up: as the blank copy, or as a student's scan of it."""
    return f"{assignment_id}#{page}"


class Scans(Source):
    """Scanned pages: of scans still in the Inbox (INBOX `PRESENT`), never a blank side; found by how they look."""
    name, kind = "scan", "Scanned page"

    def __init__(self, inbox):
        self.inbox = inbox

    def units(self, db):
        # A deleted scan is never read; an archived scan's files come back from the backup drive at a whole-copy check per
        # read, so it is not fetched for an index.
        present = PRESENT.format("sp.message_id")
        return [{"ref": row["id"], "sha256": row["sha256"], "words_key": "", "artifact": row["raster_artifact_id"]}
                for row in db.execute(f"""SELECT sp.id, sp.raster_artifact_id, a.sha256 FROM scan_pages sp
                    JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id JOIN inbox_messages m ON m.id=sp.message_id
                    WHERE {present} AND sp.status!='blank' AND m.storage_status NOT IN ({marks(ARCHIVED)})
                      AND sp.review_state NOT IN ({marks(HIDDEN_REVIEW)})
                    ORDER BY sp.created_at DESC, sp.id""", (*ARCHIVED, *HIDDEN_REVIEW))]

    def read(self, unit):
        return [(unit["ref"], self.inbox.read_artifact(unit["artifact"])[1], "")]

    def existing(self, db):
        # Whether each page still exists, not whether it counts: the one read of scans that must not apply `PRESENT`.
        return {row[0] for row in db.execute("SELECT id FROM scan_pages")}

    def same(self, db):
        present = PRESENT.format("sp.message_id")
        return {row[0]: printed_page(row[1], row[2]) for row in db.execute(
            f"""SELECT sp.id, av.assignment_id, ps.page_number FROM scan_pages sp
                JOIN inbox_messages m ON m.id=sp.message_id {PRINTED} WHERE {present} AND sp.status!='blank'
                AND m.storage_status NOT IN ({marks(ARCHIVED)}) AND sp.review_state NOT IN ({marks(HIDDEN_REVIEW)})""",
            (*ARCHIVED, *HIDDEN_REVIEW))}

    def gathered(self, db):
        present = PRESENT.format("sp.message_id")
        found = {}
        for row in db.execute(f"""SELECT av.assignment_id, ps.page_number, COUNT(*) copies, MAX(sp.received_at || ' ' || j.id) newest
                FROM scan_pages sp JOIN inbox_jobs j ON j.message_id=sp.message_id
                JOIN inbox_messages m ON m.id=sp.message_id {PRINTED}
                WHERE {present} AND sp.status!='blank' AND sp.review_state NOT IN ({marks(HIDDEN_REVIEW)})
                AND m.storage_status NOT IN ({marks(ARCHIVED)})
                GROUP BY av.assignment_id, ps.page_number""", (*HIDDEN_REVIEW, *ARCHIVED)):
            found[printed_page(row[0], row[1])] = {"count": row["copies"], "link": f"#inbox/job/{row['newest'].rpartition(' ')[2]}/pages"}
        return found

    def shown(self, db, refs):
        present = PRESENT.format("sp.message_id")
        rows = db.execute(f"""SELECT sp.id, sp.message_id, sp.source_page_index, sp.received_at, j.id job_id, m.name owner,
                asg.title, asg.id assignment_id, ps.page_number
            FROM scan_pages sp JOIN inbox_jobs j ON j.message_id=sp.message_id JOIN inbox_messages m ON m.id=sp.message_id
            LEFT JOIN page_stamps ps ON ps.id=sp.page_stamp_id LEFT JOIN printed_copies pc ON pc.id=ps.printed_copy_id
            LEFT JOIN print_batches pb ON pb.id=pc.print_batch_id LEFT JOIN assignment_versions av ON av.id=pb.assignment_version_id
            LEFT JOIN assignments asg ON asg.id=av.assignment_id
            WHERE sp.id IN ({marks(refs)}) AND {present} AND sp.status!='blank'
              AND sp.review_state NOT IN ({marks(HIDDEN_REVIEW)})
              AND m.storage_status NOT IN ({marks(ARCHIVED)})""", (*refs, *HIDDEN_REVIEW, *ARCHIVED)).fetchall()
        found = {}
        for row in rows:
            image = f"/api/documents/messages/{row['message_id']}/pages/{row['id']}/image.png"
            # What the page is, never whose: the scan's own page says that, and a likeness is not a finding about a student.
            label = f"{row['title']}, page {row['page_number']}" if row["title"] else f"Scan page {row['source_page_index']}"
            found[row["id"]] = {"image": image, "thumb": image + "?width=300", "open": image + "?width=1700", "label": label,
                                "where": f"{row['owner'].removeprefix('Inbox · ')} · scanned {row['received_at'][:10]}",
                                "link": f"#inbox/job/{row['job_id']}/pages", "job_id": row["job_id"],
                                **({"same": printed_page(row["assignment_id"], row["page_number"])} if row["title"] else {})}
        return found


# -- shared by the sources Faculty added after scans -------------------------------------------------------------------------
# Course storage with no settled working copy on NVMe: an owner that is archived is read from the backup drive at a whole-copy
# check per read, and one that is moving is reconciled by a read. Neither is read for an index.
COLD = ("archived", "restore_pending", "sync_pending", "archive_pending", "purge_pending")
PDF_PAGES = 4  # of a PDF a student sent, the pages that are indexed: one sandbox call draws this many
PICTURE_EDGE = 1568  # a picture is sent to the worker no larger than this (its own `PICTURE_SIDE`, which it scales to itself)
PICTURE_PIXELS = 60_000_000


class NotAPicture(ValueError):
    """The file is neither a picture nor a PDF (a video, a document): there is nothing to embed, and trying again cannot help."""


def as_picture(content):
    """PNG bytes of any picture Faculty keeps (PNG, JPEG, an iPhone's MPO or HEIC, GIF, WebP): upright, flat, no larger than
    PICTURE_EDGE. Decoded here as the viewer and the Assistant decode it; HEIC in the document sandbox."""
    import io

    from PIL import Image, ImageOps

    from document_identity import process
    if process.is_heif(content):
        content, _ = process.heif_picture(content, PICTURE_EDGE)
    try:
        image = Image.open(io.BytesIO(content))
    except Exception:  # noqa: BLE001 - not a picture at all
        raise NotAPicture("This file is not a picture.") from None
    if image.format not in ("PNG", "JPEG", "MPO", "GIF", "WEBP"):
        raise NotAPicture("This kind of picture cannot be embedded.")
    if image.width * image.height > PICTURE_PIXELS:
        raise NotAPicture("This picture is too large to embed.")
    image.load()
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P", "PA"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.getchannel("A"))
        image = flat
    elif image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    image.thumbnail((PICTURE_EDGE, PICTURE_EDGE))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def pdf_pages(content, pages):
    """({page: PNG}, total pages) of a few pages of a PDF, drawn in the document sandbox (no network, nothing kept)."""
    from document_identity import process
    result = process.run("look", content, {"pages": list(pages)[:PDF_PAGES]}, timeout=120)
    return {item["page"]: item["png"] for item in result["pages"]}, result["total"]


def pdf_text(content, pages):
    """{page: the text the PDF itself holds} of a few pages, read in the document sandbox. Not OCR: a scanned page has none."""
    from document_identity import process
    result = process.run("text", content, {"pages": list(pages)}, timeout=120)
    return {item["page"]: item["text"] for item in result["pages"]}


def is_pdf(content):
    return content[:1024].lstrip().startswith(b"%PDF-")


def viewer_page(route, page):
    """The file viewer's picture of one page of a PDF Faculty serves at `route` (VIEWER: drawn in the sandbox, nothing kept)."""
    return f"/api/viewer/page.png?route={quote(route, safe='')}&page={int(page)}"


def viewer_picture(route):
    return f"/api/viewer/picture?route={quote(route, safe='')}"


def course_link(db, definition_id, tab):
    """The course page's own tab for a course-wide thing: there is no address for one question, section or page."""
    row = db.execute("""SELECT id, code, term, term_other, year FROM course_terms WHERE definition_id=? AND deleted_at IS NULL
        ORDER BY is_current DESC, year DESC, created_at DESC LIMIT 1""", (definition_id,)).fetchone()
    return f"#course/{navigation_id(dict(row))}/home/{tab}" if row else "#current-courses"


def split(ref):
    """("<id>", page) of a ref "<id>#<page>"."""
    ident, _, page = ref.rpartition("#")
    return ident, int(page) if page.isdigit() else 0


def standing_event(table, key, column, order="e.created_at DESC, e.rowid DESC"):
    """SQL: the latest event of `table` for `column` is not 'removed' (documents and pictures are removed by record, never
    deleted), in the order the owner's own queries read it."""
    return f"COALESCE((SELECT e.action FROM {table} e WHERE e.{key}={column} ORDER BY {order} LIMIT 1), '') != 'removed'"


class BankPictures(Source):
    """QUESTION-BANK pictures: the figures kept with questions. Words: the question's sections, the picture's caption and the
    question itself. A removed picture and a retired question's pictures are left out."""
    name, kind = "bank", "Question bank"
    SHOWN = f"""{standing_event('question_bank_picture_events', 'picture_id', 'p.id', 'e.rowid DESC')}
        AND COALESCE((SELECT e.action FROM question_bank_events e WHERE e.question_id=q.id ORDER BY e.rowid DESC LIMIT 1), '') != 'retired'
        AND d.storage_status NOT IN ({marks(COLD)})"""
    FROM = """FROM question_bank_pictures p JOIN question_bank_questions q ON q.id=p.question_id
        JOIN course_definitions d ON d.id=q.definition_id
        JOIN question_bank_revisions r ON r.question_id=q.id AND r.revision=(SELECT MAX(revision) FROM question_bank_revisions WHERE question_id=q.id)"""

    def __init__(self, bank):
        self.bank = bank

    @staticmethod
    def _sections(db):
        """{question id: ["2.3 Title", ...]} of each question's latest tags, titled from the outline as it stands."""
        names = {row["id"]: " ".join(part for part in (row["number"], row["title"]) if part) for row in db.execute(
            """SELECT n.id, r.number, r.title FROM question_bank_nodes n JOIN question_bank_node_revisions r ON r.node_id=n.id
               AND r.revision=(SELECT MAX(revision) FROM question_bank_node_revisions WHERE node_id=n.id)""")}
        tagged = {}
        for row in db.execute("""SELECT c.question_id, c.nodes_json FROM question_bank_classifications c
                WHERE c.revision=(SELECT MAX(revision) FROM question_bank_classifications WHERE question_id=c.question_id)
                  AND c.question_id IN (SELECT question_id FROM question_bank_pictures)"""):
            nodes = [node if isinstance(node, str) else (node or {}).get("id") for node in json.loads(row["nodes_json"] or "[]")]
            tagged[row["question_id"]] = [names[node] for node in nodes if node in names]
        return tagged

    def units(self, db):
        from question_bank.content import plain
        tagged = self._sections(db)
        found = []
        for row in db.execute(f"SELECT p.id, p.sha256, p.caption, p.question_id, r.content_json {self.FROM} WHERE {self.SHOWN} ORDER BY p.created_at DESC, p.id", COLD):
            content = json.loads(row["content_json"])
            words = worded("Question bank", "; ".join(tagged.get(row["question_id"], [])), row["caption"], plain(content))
            found.append({"ref": row["id"], "sha256": row["sha256"], "words_key": words, "words": words})
        return found

    def read(self, unit):
        return [(unit["ref"], as_picture(self.bank.read_picture(unit["ref"])[0]), unit["words"])]

    def existing(self, db):
        return {row[0] for row in db.execute("SELECT id FROM question_bank_pictures")}

    def shown(self, db, refs):
        from question_bank.pictures import EXTENSIONS
        tagged = self._sections(db)
        found = {}
        for row in db.execute(f"""SELECT p.id, p.media_type, p.caption, p.question_id, q.definition_id, d.code {self.FROM}
                WHERE p.id IN ({marks(refs)}) AND {self.SHOWN}""", (*refs, *COLD)):
            route = f"/api/question-bank/pictures/{row['id']}/picture.{EXTENSIONS.get(row['media_type'], 'png')}"
            sections = tagged.get(row["question_id"], [])
            found[row["id"]] = {"image": route, "thumb": route, "open": route,
                                "label": " ".join((row["caption"] or "").split())[:70] or "A question's picture",
                                "where": " · ".join(part for part in ("Question bank", row["code"], sections[0] if sections else "") if part),
                                "link": course_link(db, row["definition_id"], "questions")}
        return found


class TemplatePages(Source):
    """The printed pages of quizzes and exams (the blank copies kept for grading). Words: the assignment, the questions printed
    on the page and the page's own text. The same page of another version is the same result."""
    name, kind = "template", "Printed page"
    FROM = """FROM assignment_templates t JOIN assignments a ON a.id=t.assignment_id
        JOIN course_definitions d ON d.id=a.definition_id"""

    def __init__(self, templates):
        self.templates = templates

    @staticmethod
    def _questions(db):
        """{(template id, page): [(question revision's hash, "12. the problem")]} of each question's latest revision."""
        found = {}
        for row in db.execute("""SELECT r.sha256, r.content_json FROM frq_questions q JOIN frq_question_revisions r ON r.question_id=q.id
                AND r.revision=(SELECT MAX(revision) FROM frq_question_revisions WHERE question_id=q.id) ORDER BY q.question_key"""):
            content = json.loads(row["content_json"])
            for page in sorted({region.get("page") for region in content.get("regions") or [] if region.get("page")}):
                found.setdefault((content.get("template_id"), page), []).append((row["sha256"], " ".join(str(content.get("problem") or "").split())))
        return found

    def units(self, db):
        questions = self._questions(db)
        found = []
        for row in db.execute(f"SELECT t.id, t.page_count, t.manifest_sha256, t.label, a.title {self.FROM} WHERE d.storage_status NOT IN ({marks(COLD)}) ORDER BY t.created_at DESC, t.id", COLD):
            for page in range(1, row["page_count"] + 1):
                printed = questions.get((row["id"], page), [])
                found.append({"ref": f"{row['id']}#{page}", "sha256": f"{row['manifest_sha256']}#{page}", "template": row["id"], "page": page,
                              "words_key": "|".join([row["manifest_sha256"], str(page), row["title"], row["label"], *(sha for sha, _ in printed)]),
                              "head": f"{row['title']} {row['label']}, page {page}", "printed": [text for _, text in printed]})
        return found

    def read(self, unit):
        picture = as_picture(self.templates.read(unit["template"], "image", unit["page"])[0])
        text = self.templates.read(unit["template"], "text", unit["page"])[0].decode(errors="replace")
        return [(unit["ref"], picture, worded(unit["head"], *unit["printed"], text))]

    def existing(self, db):
        return {f"{row[0]}#{page}" for row in db.execute("SELECT id, page_count FROM assignment_templates") for page in range(1, row[1] + 1)}

    def shown(self, db, refs):
        wanted = {}
        for ref in refs:
            ident, page = split(ref)
            wanted.setdefault(ident, []).append((ref, page))
        found = {}
        for row in db.execute(f"""SELECT t.id, t.page_count, t.label, t.assignment_id, a.title, d.code {self.FROM}
                WHERE t.id IN ({marks(wanted)}) AND d.storage_status NOT IN ({marks(COLD)})""", (*wanted, *COLD)):
            for ref, page in wanted[row["id"]]:
                if 1 <= page <= row["page_count"]:
                    route = f"/api/assignment-templates/{row['id']}/pages/{page}.png"
                    found[ref] = {"image": route, "thumb": f"/api/assignment-templates/{row['id']}/pages/{page}/preview.webp", "open": route,
                                  "label": f"{row['title']}, page {page}", "where": f"Printed copy · {row['code']} · {row['label']}",
                                  "link": f"#documents?assignment={row['assignment_id']}&step=details", "same": printed_page(row["assignment_id"], page)}
        return found

    def same(self, db):
        return {f"{row[0]}#{page}": printed_page(row[1], page) for row in db.execute("SELECT id, assignment_id, page_count FROM assignment_templates")
                for page in range(1, row[2] + 1)}


class NotesPages(Source):
    """COURSE-NOTES: the pages of each course's current lecture notes, a few a turn. Words: the section, its topics and the
    page's own text. An edition that is removed, or no longer the newest that stands, is left out."""
    name, kind = "notes", "Course notes"

    def __init__(self, notes):
        self.notes = notes

    @staticmethod
    def _current(db):
        """{notes id: its row} of each course's current edition: the newest that stands (COURSE-NOTES), on a working drive."""
        newest = {}
        for row in db.execute(f"""SELECT n.id, n.definition_id, n.title, d.code,
                (SELECT MAX(e.created_at) FROM course_notes_events e WHERE e.notes_id=n.id AND e.action='uploaded') uploaded_at,
                COALESCE((SELECT e.action FROM course_notes_events e WHERE e.notes_id=n.id AND e.action IN ('removed','restored')
                          ORDER BY e.created_at DESC, e.rowid DESC LIMIT 1), '') hiding
            FROM course_notes n JOIN course_definitions d ON d.id=n.definition_id WHERE d.storage_status NOT IN ({marks(COLD)})
            ORDER BY n.created_at, n.id""", COLD):
            if row["hiding"] != "removed":
                held = newest.get(row["definition_id"])
                if not held or ((row["uploaded_at"] or ""), row["id"]) >= ((held["uploaded_at"] or ""), held["id"]):
                    newest[row["definition_id"]] = row
        return {row["id"]: row for row in newest.values()}

    @staticmethod
    def _heading(part):
        section = " ".join(piece for piece in (part["number"], part["title"]) if piece)
        chapter = " ".join(piece for piece in (part["chapter"] and f"Chapter {part['chapter']}", part["chapter_title"]) if piece)
        return section or chapter or "Front matter", chapter

    def units(self, db):
        current = self._current(db)
        found = []
        if current:
            for part in db.execute(f"SELECT * FROM course_notes_parts WHERE notes_id IN ({marks(current)}) ORDER BY notes_id, position", tuple(current)):
                pages = part["last_page"] - part["first_page"] + 1
                section, chapter = self._heading(part)
                topics = json.loads(part["topics_json"] or "[]")
                for start in range(1, pages + 1, PDF_PAGES):
                    found.append({"ref": f"{part['notes_id']}:{part['position']}#{start}", "sha256": part["sha256"], "notes": part["notes_id"],
                                  "position": part["position"], "pages": list(range(start, min(start + PDF_PAGES, pages + 1))),
                                  "first_page": part["first_page"], "section": section, "chapter": chapter, "topics": topics,
                                  "words_key": "|".join([part["sha256"], str(start), section, chapter, part["topics_json"] or ""])})
        return found

    def read(self, unit):
        content = self.notes.read(unit["notes"], unit["position"])[0]
        drawn, _ = pdf_pages(content, unit["pages"])
        text = pdf_text(content, unit["pages"])
        found = []
        for page in unit["pages"]:
            if page in drawn:
                original = unit["first_page"] + page - 1  # a topic's page is a page of the whole notes
                topics = [topic["title"] for topic in unit["topics"] if topic.get("page") == original]
                label = "\n".join(part for part in ("Course notes " + unit["section"], unit["chapter"]) if part)
                found.append((f"{unit['notes']}:{unit['position']}#{page}", drawn[page], worded(label, "; ".join(topics), text.get(page, ""))))
        return found

    def existing(self, db):
        return {f"{row['notes_id']}:{row['position']}#{page}" for row in db.execute("SELECT notes_id, position, first_page, last_page FROM course_notes_parts")
                for page in range(1, row["last_page"] - row["first_page"] + 2)}

    def shown(self, db, refs):
        current = self._current(db)
        found = {}
        for ref in refs:
            ident, page = split(ref)
            notes_id, _, position = ident.rpartition(":")
            edition = current.get(notes_id)
            part = edition and position.isdigit() and db.execute("SELECT * FROM course_notes_parts WHERE notes_id=? AND position=?", (notes_id, int(position))).fetchone()
            if part and 1 <= page <= part["last_page"] - part["first_page"] + 1:
                route = f"/api/course-notes/{notes_id}/parts/{part['position']}/notes.pdf"
                section, _ = self._heading(part)
                found[ref] = {"image": route, "pdf_page": page, "thumb": viewer_page(route, page), "open": route, "open_page": page,
                              "label": f"{section}, page {page}", "where": f"Course notes · {edition['code']}",
                              "link": course_link(db, edition["definition_id"], "notes")}
        return found


class AbsenceDocuments(Source):
    """ABSENCES documentation: what students sent for an absence, found by how it looks. A document removed from its absence
    is left out; a result says what it is and never whose (the document itself, opened, says that)."""
    name, kind = "absence", "Absence documentation"
    # An upload lives in its section's storage, read only while that is on the working drive; a reference into the
    # communications record is read from the record, which lives on the backup drive in small bundles by design.
    SHOWN = f"""t.deleted_at IS NULL AND {standing_event('absence_document_events', 'document_id', 'd.id')}
        AND (d.kind='message' OR s.storage_status NOT IN ({marks(COLD)}))"""
    FROM = """FROM absence_documents d JOIN absences a ON a.id=d.absence_id JOIN course_terms t ON t.id=a.term_id
        JOIN course_sections s ON s.id=a.section_id"""

    def __init__(self, absences):
        self.absences = absences

    def units(self, db):
        return [{"ref": f"{row['id']}#1", "sha256": row["sha256"], "absence": row["absence_id"], "document": row["id"], "words_key": ""}
                for row in db.execute(f"SELECT d.id, d.absence_id, d.sha256 {self.FROM} WHERE {self.SHOWN} ORDER BY d.created_at DESC, d.id", COLD)]

    def read(self, unit):
        return file_pictures(unit["document"], self.absences.read_document(unit["absence"], unit["document"])[2])

    def existing(self, db):
        return {f"{row[0]}#{page}" for row in db.execute("SELECT id FROM absence_documents") for page in range(1, PDF_PAGES + 1)}

    def shown(self, db, refs):
        wanted = {}
        for ref in refs:
            ident, page = split(ref)
            wanted.setdefault(ident, []).append((ref, page))
        found = {}
        for row in db.execute(f"""SELECT d.id, d.absence_id, d.content_type, d.name, d.created_at, t.code, s.number {self.FROM}
                WHERE d.id IN ({marks(wanted)}) AND {self.SHOWN}""", (*wanted, *COLD)):
            route = f"/api/absences/{row['absence_id']}/documents/{row['id']}"
            for ref, page in wanted[row["id"]]:
                found[ref] = {**file_result(route, page, row["content_type"], row["name"]), "label": "Absence documentation" + (f", page {page}" if page > 1 else ""),
                              "where": f"{row['code']} · Section {row['number']} · added {row['created_at'][:10]}", "link": "#absences"}
        return found


def file_pictures(ident, content):
    """[(ref, PNG, "")] of a file a student sent: a picture as it is, a PDF's first pages; anything else is not a picture."""
    if is_pdf(content):
        drawn, _ = pdf_pages(content, range(1, PDF_PAGES + 1))
        return [(f"{ident}#{page}", drawn[page], "") for page in sorted(drawn)]
    return [(f"{ident}#1", as_picture(content), "")]


def file_result(route, page, content_type, name):
    """How the strip and the Assistant reach a kept file at a download route: a PDF by its page, a picture through the viewer."""
    if content_type == "application/pdf" or str(name or "").lower().endswith(".pdf"):
        return {"image": route, "pdf_page": page, "thumb": viewer_page(route, page), "open": route, "open_page": page}
    return {"image": route, "thumb": viewer_picture(route), "open": route}


class StudentAttachments(Source):
    """COMMUNICATIONS: pictures and PDFs attached to mail and Canvas messages a student wrote, found by how they look. Every
    one of them, once (requirement, 2026-10-11): a file is on each
    message that carries it along, and its first copy stands for them all. Left out: a file an absence already holds as
    documentation, what the instructor sent first (a student's reply carries it back), an icon, and anything not kept. A
    result never says whose it is."""
    name, kind = "mail", "From a student"
    KINDS = ("image/jpeg", "image/png", "image/heic", "image/heif", "image/webp", "image/gif", "application/pdf")
    SMALL = 8 * 1024  # a picture smaller than this is an icon, not something a student sent to be read
    CHANNELS = {"gmail": "Mail", "canvas_message": "Canvas message", "canvas_comment": "Canvas comment"}
    LATEST = "r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=r.message_id)"

    def __init__(self, communications):
        self.communications = communications

    def _attachments(self, db):
        """Every kept picture or PDF a student sent, once (the latest revision of each message; the student linked as its author).

        Measured on the live record, 2026-10-11: 14 pictures were on more than one message, each within one thread, because
        a reply carries the pictures of the message it answers; none was a signature's logo (108 KB to 4.9 MB photographs and
        screenshots), and the rule that took them for logos had left 19 of 104 attachments out. So the same bytes are one
        attachment, shown where they were first sent; and a file whose first copy is on a message the instructor wrote is the
        instructor's, whoever's reply carries it."""
        rows = db.execute(f"""SELECT r.message_id, a.key position, json_extract(a.value,'$.sha256') sha256, json_extract(a.value,'$.type') type,
                json_extract(a.value,'$.name') name, json_extract(a.value,'$.size') size, r.sent_at, m.channel,
                json_extract(r.context_json,'$.thread_id') thread, r.author_role='student'
                    AND EXISTS(SELECT 1 FROM comm_links l WHERE l.message_id=r.message_id AND l.role='author') students
            FROM comm_revisions r JOIN comm_messages m ON m.id=r.message_id, json_each(r.attachments_json) a
            WHERE {self.LATEST} AND json_extract(a.value,'$.kept') AND json_extract(a.value,'$.sha256') IS NOT NULL
            ORDER BY r.sent_at DESC, r.message_id, a.key""").fetchall()
        documented = {(row[0], row[1]) for row in db.execute(f"""SELECT d.message_id, d.attachment_index FROM absence_documents d
            WHERE d.kind='message' AND {standing_event('absence_document_events', 'document_id', 'd.id')}""")}
        # An absence's documentation is that absence's, in every copy of the file.
        held = {row["sha256"] for row in rows if (row["message_id"], row["position"]) in documented}
        first = {}
        for row in sorted(rows, key=lambda row: (row["sent_at"], row["message_id"], row["position"])):
            first.setdefault(row["sha256"], (row["message_id"], row["position"]))
        found = []
        for row in rows:
            kind = str(row["type"] or "").split(";")[0].strip().lower()
            if (row["students"] and kind in self.KINDS and first[row["sha256"]] == (row["message_id"], row["position"])
                    and row["sha256"] not in held and not (kind.startswith("image/") and (row["size"] or 0) < self.SMALL)):
                found.append(row)
        return found

    def units(self, db):
        return [{"ref": f"{row['message_id']}:{row['position']}#1", "sha256": row["sha256"], "message": row["message_id"],
                 "position": row["position"], "words_key": ""} for row in self._attachments(db)]

    def read(self, unit):
        return file_pictures(f"{unit['message']}:{unit['position']}", self.communications.attachment(unit["message"], unit["position"])[2])

    def existing(self, db):
        return {f"{row[0]}:{row[1]}#{page}" for row in db.execute(f"""SELECT r.message_id, a.key FROM comm_revisions r, json_each(r.attachments_json) a
            WHERE {self.LATEST}""") for page in range(1, PDF_PAGES + 1)}

    def shown(self, db, refs):
        allowed = {f"{row['message_id']}:{row['position']}": row for row in self._attachments(db)}
        found = {}
        for ref in refs:
            ident, page = split(ref)
            row = allowed.get(ident)
            if row:
                route = f"/api/communications/messages/{row['message_id']}/attachments/{row['position']}"
                thread = str(row["thread"] or "")
                found[ref] = {**file_result(route, page, str(row["type"] or "").split(";")[0].strip().lower(), row["name"]),
                              "label": "Attachment a student sent" + (f", page {page}" if page > 1 else ""),
                              "where": f"{self.CHANNELS.get(row['channel'], 'Message')} · sent {str(row['sent_at'])[:10]}",
                              "link": f"#mail?thread={thread}" if row["channel"] == "gmail" and HEX.fullmatch(thread) else ""}
        return found


class MemoryPictures(Source):
    """ASSISTANT-MEMORY pictures: what the instructor asked the Assistant to remember with a picture. Words: what they said
    about it, their own typed sentence. A memory that no longer stands is left out, and an erased picture leaves the index."""
    name, kind = "memory", "Remembered picture"
    FROM = "FROM assistant_memory_pictures p JOIN assistant_memory_latest l ON l.memory_id=p.memory_id"
    SHOWN = "p.erased=0 AND l.erased=0 AND l.status='current'"

    def __init__(self, path):
        self.path = path

    def units(self, db):
        return [{"ref": row["id"], "sha256": row["sha256"], "words_key": tidy(row["content"]), "words": tidy(row["content"])}
                for row in db.execute(f"SELECT p.id, p.sha256, l.content {self.FROM} WHERE {self.SHOWN} ORDER BY p.created_at DESC, p.id")]

    def read(self, unit):
        with connect(self.path) as db:
            row = db.execute("SELECT image FROM assistant_memory_pictures WHERE id=? AND erased=0", (unit["ref"],)).fetchone()
        if not row:
            raise NotAPicture("This picture is no longer kept.")
        return [(unit["ref"], as_picture(bytes(row["image"])), unit["words"])]

    def existing(self, db):
        return {row[0] for row in db.execute("SELECT id FROM assistant_memory_pictures WHERE erased=0")}

    def shown(self, db, refs):
        found = {}
        for row in db.execute(f"SELECT p.id, p.memory_id, l.content, l.scope, l.first_at {self.FROM} WHERE p.id IN ({marks(refs)}) AND {self.SHOWN}", tuple(refs)):
            route = f"/api/assistant/memory-pictures/{row['id']}"
            said = " ".join(row["content"].split())
            found[row["id"]] = {"image": route, "thumb": route, "open": route, "label": said[:70] + ("…" if len(said) > 70 else ""),
                                "where": f"Remembered {row['first_at'][:10]}", "link": "", "memory_id": row["memory_id"]}
        return found
