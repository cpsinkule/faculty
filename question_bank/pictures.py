"""Pictures that go with a bank question (requirement, 2026-10-01).

A picture is a graph, diagram or photograph a question relies on: an image the instructor uploads (PNG, JPEG, GIF or WebP, checked by
opening it) or a region cut from a page of a PDF read for the course's bank (from the page picture kept beside the upload, saved as PNG).
Each belongs to one question and so to its course definition; its file lives in the definition's verified storage under
`question-bank/pictures/<picture id>/`, written before the catalog row that names it (the ID derives from the question and the request,
so a retry finishes its own folder). A picture never changes the question's content: it serves every revision, and a question retained
from an assessment keeps following its source. Removing a picture is an event; Restore brings it back. Nothing here is sent anywhere.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

MAX_PICTURE = 10 * 1024**2      # bytes of an uploaded picture
MAX_PIXELS = 40_000_000         # width x height, read from the header before anything is decoded
MAX_CAPTION = 1000
REGION_MIN = .02                # the smallest region cut from a page, as a share of its width and height
FORMATS = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "GIF": ("image/gif", "gif"), "WEBP": ("image/webp", "webp")}
EXTENSIONS = {media: extension for media, extension in FORMATS.values()}
PICTURES = uuid.UUID("6f1d2c8e-3a4b-4e9f-b2d7-5c0a9e8f1b34")


def _caption(value):
    value = " ".join(str(value or "").split())
    if len(value) > MAX_CAPTION:
        raise ValueError(f"A caption is at most {MAX_CAPTION} characters.")
    return value


def _region(value):
    if not isinstance(value, dict) or any(isinstance(value.get(k), bool) or not isinstance(value.get(k), (int, float)) or not 0 <= value[k] <= 1
                                          for k in ("left", "top", "right", "bottom")):
        raise ValueError("Give the region as left, top, right and bottom shares of the page, from 0 to 1.")
    if value["right"] - value["left"] < REGION_MIN or value["bottom"] - value["top"] < REGION_MIN:
        raise ValueError("Draw a larger box: a region is at least 2% of the page wide and high.")
    return {k: round(float(value[k]), 5) for k in ("left", "top", "right", "bottom")}


def inspect(data):
    """(media type, width, height) of an uploaded picture, refusing anything that is not one whole PNG, JPEG, GIF or WebP image."""
    from PIL import Image, UnidentifiedImageError
    if not isinstance(data, bytes) or not data:
        raise ValueError("Choose the picture.")
    if len(data) > MAX_PICTURE:
        raise ValueError(f"That picture is larger than {MAX_PICTURE // 1024**2} MB. Save a smaller copy, then add that.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            kind = FORMATS.get(image.format or "")
            if not kind:
                raise ValueError
            width, height = image.size
            if not width or not height or width * height > MAX_PIXELS:
                raise ValueError
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, SyntaxError):
        raise ValueError("Add a picture as a PNG, JPEG, GIF or WebP image (a screenshot works).") from None
    return kind[0], width, height


FIGURE_PAD = .012             # a reader's figure box is widened by this share of the page on every side before it is trimmed
INK = 235                     # a pixel darker than this (0-255 grey) is drawn on; JPEG noise on white paper stays lighter
INK_MARGIN = 6                # pixels of white kept around what is drawn
GAP = 2                       # rows (or columns) of white that separate the figure from text printed beside it
SPECK = 3                     # a row (or column) with fewer dark pixels than this is white: a speck of JPEG noise joins nothing


def _bands(counts):
    """[(start, end)] of the runs of inked rows (or columns), given each one's count of dark pixels; runs fewer than GAP apart joined."""
    bands = []
    for index in (counts >= SPECK).nonzero()[0].tolist():
        if bands and index - bands[-1][1] < GAP:
            bands[-1][1] = index + 1
        else:
            bands.append([index, index + 1])
    return bands


def _kept(bands, inner):
    """The span of the bands that lie mostly inside the reader's own box (`inner`, start and end): a line of the problem's text the
    widening reached, or the edge of a neighbouring figure, lies mostly outside it and is left out."""
    kept = [(a, b) for a, b in bands if min(b, inner[1]) - max(a, inner[0]) >= (b - a) / 2]
    return (kept[0][0], kept[-1][1]) if kept else None


def figure_cut(page, region):
    """(PNG bytes, width, height, region) of a figure cut from a page picture: the box widened by FIGURE_PAD, then trimmed to what is
    drawn inside it plus a thin margin, leaving out text strips that lie mostly in the widening (a reader's box is seldom exact, 2026-10-03:
    widened boxes caught a line of the problem above). The region returned is what was cut, as shares of the page."""
    import numpy
    from PIL import Image
    with Image.open(io.BytesIO(page)) as image:
        image = image.convert("RGB")
        width, height = image.size
        box = (max(0, round((region["left"] - FIGURE_PAD) * width)), max(0, round((region["top"] - FIGURE_PAD) * height)),
               min(width, round((region["right"] + FIGURE_PAD) * width)), min(height, round((region["bottom"] + FIGURE_PAD) * height)))
        inner = (round(region["left"] * width) - box[0], round(region["top"] * height) - box[1],
                 round(region["right"] * width) - box[0], round(region["bottom"] * height) - box[1])
        ink = numpy.asarray(image.crop(box).convert("L")) < INK
        rows = _kept(_bands(ink.sum(axis=1)), (inner[1], inner[3]))
        columns = _kept(_bands(ink[rows[0]:rows[1]].sum(axis=0)), (inner[0], inner[2])) if rows else None
        if rows and columns:
            box = (max(box[0], box[0] + columns[0] - INK_MARGIN), max(box[1], box[1] + rows[0] - INK_MARGIN),
                   min(box[2], box[0] + columns[1] + INK_MARGIN), min(box[3], box[1] + rows[1] + INK_MARGIN))
        cut = image.crop(box)
        out = io.BytesIO()
        cut.save(out, "PNG", optimize=True)
    return out.getvalue(), cut.width, cut.height, {"left": round(box[0] / width, 5), "top": round(box[1] / height, 5),
                                                   "right": round(box[2] / width, 5), "bottom": round(box[3] / height, 5)}


class QuestionBankPictures:
    @staticmethod
    def _pictures(db, question_ids):
        """Each question's pictures in the order they were added, each marked removed when its latest event removed it."""
        question_ids = sorted(set(question_ids))
        if not question_ids:
            return {}
        found = {}
        marks = ",".join("?" * len(question_ids))
        rows = db.execute(f"""SELECT p.*, u.source_name AS upload_name,
                (SELECT e.action FROM question_bank_picture_events e WHERE e.picture_id=p.id ORDER BY e.rowid DESC LIMIT 1) AS latest
            FROM question_bank_pictures p LEFT JOIN question_bank_uploads u ON u.id=p.upload_id
            WHERE p.question_id IN ({marks}) ORDER BY p.created_at, p.rowid""", question_ids)
        for row in rows:
            found.setdefault(row["question_id"], []).append({
                "id": row["id"], "url": f"/api/question-bank/pictures/{row['id']}/picture.{EXTENSIONS[row['media_type']]}", "caption": row["caption"],
                "width": row["width"], "height": row["height"], "origin": row["origin"], "source_name": row["source_name"] or row["upload_name"] or "",
                "page": row["page"], "removed": row["latest"] == "removed", "created_at": row["created_at"]})
        return found

    def _keep_picture(self, question_id, request_id, picture, media_type, width, height, actor, *, caption="", source_name="", upload_id=None,
                      page=None, region=None):
        """Write the picture's file into the definition's verified storage, then the catalog row that names it (one transaction)."""
        from document_identity.storage import publish
        digest = hashlib.sha256(picture).hexdigest()
        def existing(db):
            row = db.execute("SELECT question_id,sha256 FROM question_bank_pictures WHERE request_id=?", (request_id,)).fetchone()
            if row and (row["question_id"] != question_id or row["sha256"] != digest):
                raise Conflict("This request ID already belongs to another picture.")
            return bool(row)
        with connect(self.path) as db:
            question = self._question_row(db, question_id)
            prior = existing(db)
        if prior:
            return self.question(question_id)
        picture_id = str(uuid.uuid5(PICTURES, f"{question_id}:{request_id}"))
        path = f"question-bank/pictures/{picture_id}/picture.{EXTENSIONS[media_type]}"
        with self.rosters.lock(), self.documents.files.files("definition", question["definition_id"], write=True) as root:
            with connect(self.path) as db:
                if existing(db):
                    return self.question(question_id)
            publish(root / path, picture)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if not db.execute("SELECT 1 FROM question_bank_pictures WHERE request_id=?", (request_id,)).fetchone():
                    db.execute("""INSERT INTO question_bank_pictures(id,question_id,origin,media_type,sha256,size,width,height,path,caption,source_name,upload_id,
                                  page,region_json,request_id,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (picture_id, question_id, "page" if upload_id else "file", media_type, hashlib.sha256(picture).hexdigest(), len(picture),
                                width, height, path, caption, source_name[:200], upload_id, page, json.dumps(region, sort_keys=True) if region else None,
                                request_id, actor, timestamp()))
                    db.commit()
        return self.question(question_id)

    def add_picture(self, question_id, data, filename, caption, request_id, actor="workspace"):
        """A picture the instructor uploads for a question: kept as it was sent once it opens as an image."""
        from .service import _file_name, _request
        request_id = _request(request_id)
        caption = _caption(caption)
        media_type, width, height = inspect(data)
        return self._keep_picture(question_id, request_id, data, media_type, width, height, actor, caption=caption,
                                  source_name=_file_name(str(filename or "").replace("\\", "/").rsplit("/", 1)[-1], "picture")[:200])

    def cut_picture(self, question_id, data, actor="workspace"):
        """A region of a page of a PDF read for this course's bank (by default the PDF the question was read from), cut from the page
        picture kept beside the upload and saved as PNG."""
        from PIL import Image
        from .service import IDENTIFIER, _replayed, _request
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        caption = _caption(data.get("caption"))
        region = _region(data.get("region"))
        page = data.get("page")
        with connect(self.path) as db:
            question = self._question_row(db, question_id)
            upload_id = data.get("upload_id") or question["upload_id"]
            if not isinstance(upload_id, str) or not IDENTIFIER.fullmatch(upload_id):
                raise ValueError("Choose the PDF to cut the picture from.")
            upload = db.execute("SELECT * FROM question_bank_uploads WHERE id=? AND definition_id=?", (upload_id, question["definition_id"])).fetchone()
            if not upload:
                raise ValueError("Choose a PDF read for this course's bank.")
            if _replayed(db, "question_bank_pictures", "question_id", question_id, request_id):
                return self.question(question_id)
        picture = self._page_picture(dict(upload), page)
        with Image.open(io.BytesIO(picture)) as image:
            image = image.convert("RGB")
            width, height = image.size
            box = (round(region["left"] * width), round(region["top"] * height), round(region["right"] * width), round(region["bottom"] * height))
            cut = image.crop(box)
            out = io.BytesIO()
            cut.save(out, "PNG", optimize=True)
        return self._keep_picture(question_id, request_id, out.getvalue(), "image/png", cut.width, cut.height, actor, caption=caption,
                                  upload_id=upload["id"], page=page, region=region)

    def _cut_figures(self, upload, cuts, actor):
        """The figures a PDF's reading boxed (bank_extract v2), cut from the kept page pictures for the questions added from it: each box
        widened a little and trimmed to what is drawn inside it, saved as PNG with the reading's description as its caption. Every file is
        written before the rows that name it (one transaction); a cut whose request is kept already is skipped, so a retry finishes the
        rest. `cuts`: [{question_id, request_id, page, region, caption}]. Returns how many pictures were added."""
        from document_identity.storage import publish
        if not cuts:
            return 0
        with connect(self.path) as db:
            done = {row[0] for row in db.execute(f"SELECT request_id FROM question_bank_pictures WHERE request_id IN ({','.join('?' * len(cuts))})",
                                                  [cut["request_id"] for cut in cuts])}
        pages, files, rows = {}, {}, []
        for cut in cuts:
            if cut["request_id"] in done:
                continue
            if cut["page"] not in pages:
                pages[cut["page"]] = self._page_picture(upload, cut["page"])
            picture, width, height, region = figure_cut(pages[cut["page"]], cut["region"])
            picture_id = str(uuid.uuid5(PICTURES, f"{cut['question_id']}:{cut['request_id']}"))
            path = f"question-bank/pictures/{picture_id}/picture.png"
            files[path] = picture
            rows.append((picture_id, cut["question_id"], "page", "image/png", hashlib.sha256(picture).hexdigest(), len(picture), width, height, path,
                         _caption(cut["caption"][:MAX_CAPTION]), "", upload["id"], cut["page"], json.dumps(region, sort_keys=True), cut["request_id"],
                         actor, timestamp()))
        if not rows:
            return 0
        with self.rosters.lock(), self.documents.files.files("definition", upload["definition_id"], write=True) as root:
            for path, data in files.items():
                publish(root / path, data)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                added = 0
                for row in rows:
                    if db.execute("SELECT 1 FROM question_bank_pictures WHERE request_id=?", (row[14],)).fetchone():
                        continue
                    db.execute("""INSERT INTO question_bank_pictures(id,question_id,origin,media_type,sha256,size,width,height,path,caption,source_name,upload_id,
                                  page,region_json,request_id,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
                    added += 1
                db.commit()
        return added

    def _page_picture(self, upload, page):
        """The kept picture of one page of an uploaded PDF (the pages are read in the document sandbox once, if they were not yet)."""
        from document_identity.storage import read_verified
        if type(page) is not int or page < 1:
            raise ValueError("Choose a page of the PDF.")
        with self.rosters.lock(), self.documents.files.files("definition", upload["definition_id"]) as root:
            manifest = root / f"question-bank/uploads/{upload['id']}/pages/pages.json"
            listing = json.loads(manifest.read_text())["pages"] if manifest.is_file() else None
            if listing is not None:
                entry = next((p for p in listing if p["number"] == page), None)
                if entry is None:
                    raise ValueError(f"Choose a page from 1 to {len(listing)}.")
                return read_verified(root, entry["path"], entry["sha256"])
        pages = self._prepared_pages(upload)
        entry = next((p for p in pages if p["number"] == page), None)
        if entry is None:
            raise ValueError(f"Choose a page from 1 to {len(pages)}.")
        return entry["picture"]

    def page_picture(self, upload_id, page):
        """(JPEG bytes, sha256) of one kept page picture, for choosing a region to cut."""
        from .service import IDENTIFIER
        if not isinstance(upload_id, str) or not IDENTIFIER.fullmatch(upload_id):
            raise NotFound("Upload not found.")
        with connect(self.path) as db:
            upload = db.execute("SELECT * FROM question_bank_uploads WHERE id=?", (upload_id,)).fetchone()
        if not upload:
            raise NotFound("Upload not found.")
        try:
            picture = self._page_picture(dict(upload), page)
        except ValueError as problem:
            raise NotFound(str(problem)) from None
        return picture, hashlib.sha256(picture).hexdigest()

    def read_picture(self, picture_id):
        """(bytes, sha256, media type) of a kept picture, checked against its checksum."""
        from document_identity.storage import read_verified
        from .service import IDENTIFIER
        if not isinstance(picture_id, str) or not IDENTIFIER.fullmatch(picture_id):
            raise NotFound("Picture not found.")
        with connect(self.path) as db:
            row = db.execute("""SELECT p.*, q.definition_id FROM question_bank_pictures p JOIN question_bank_questions q ON q.id=p.question_id
                WHERE p.id=?""", (picture_id,)).fetchone()
        if not row:
            raise NotFound("Picture not found.")
        with self.rosters.lock(), self.documents.files.files("definition", row["definition_id"]) as root:
            data = read_verified(root, row["path"], row["sha256"])
        return data, row["sha256"], row["media_type"]

    def _picture_event(self, picture_id, data, action, actor):
        from .service import IDENTIFIER, _request
        data = data if isinstance(data, dict) else {}
        request_id = _request(data.get("request_id"))
        if not isinstance(picture_id, str) or not IDENTIFIER.fullmatch(picture_id):
            raise NotFound("Picture not found.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT question_id FROM question_bank_pictures WHERE id=?", (picture_id,)).fetchone()
            if not row:
                raise NotFound("Picture not found.")
            prior = db.execute("SELECT picture_id, action FROM question_bank_picture_events WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if (prior["picture_id"], prior["action"]) != (picture_id, action):
                    raise Conflict("This request ID already belongs to another change.")
                return self.question(row["question_id"])
            latest = db.execute("SELECT action FROM question_bank_picture_events WHERE picture_id=? ORDER BY rowid DESC LIMIT 1", (picture_id,)).fetchone()
            removed = bool(latest) and latest["action"] == "removed"
            if action == "removed" and removed:
                raise Conflict("This picture was removed already.")
            if action == "restored" and not removed:
                raise Conflict("This picture is with its question already.")
            db.execute("INSERT INTO question_bank_picture_events(id,picture_id,action,request_id,actor,created_at) VALUES (?,?,?,?,?,?)",
                       (str(uuid.uuid4()), picture_id, action, request_id, actor, timestamp()))
            db.commit()
        return self.question(row["question_id"])

    def remove_picture(self, picture_id, data, actor="workspace"):
        """Take a picture away from its question; it stays kept, and Restore brings it back."""
        return self._picture_event(picture_id, data, "removed", actor)

    def restore_picture(self, picture_id, data, actor="workspace"):
        return self._picture_event(picture_id, data, "restored", actor)
