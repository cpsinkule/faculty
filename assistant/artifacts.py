"""ASSISTANT-ARTIFACTS: immutable generated files in existing retained owners."""
import hashlib
import io
import json
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import escape

from werkzeug.exceptions import Conflict, NotFound
from rosters import connect
from document_identity.storage import publish, read_verified
from .evidence import digest
from .store import now, text

FORMATS = {"md": "text/markdown", "txt": "text/plain", "tex": "text/x-tex", "csv": "text/csv", "json": "application/json", "pdf": "application/pdf"}
OWNERS = {"definition": ("course_definitions", "definition_id"), "term": ("course_terms", "term_id"),
          "section": ("course_sections", "section_id")}
FONT_LOCK = threading.Lock()


def pdf_font():
    """Embed an installed Unicode font, with ReportLab's bundled font as the portable fallback."""
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    name = "FacultyArtifactText"
    with FONT_LOCK:
        if name not in pdfmetrics.getRegisteredFontNames():
            path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
            if not path.is_file():
                path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
            pdfmetrics.registerFont(TTFont(name, str(path)))
        return pdfmetrics.getFont(name)


class Artifacts:
    def __init__(self, service):
        self.service = service

    def owner(self, level, ident):
        if not isinstance(level, str) or level not in OWNERS:
            raise ValueError("Choose a course definition, term or section owner.")
        if not isinstance(ident, str) or not ident:
            raise ValueError("Choose the retained owner's ID.")
        table, _ = OWNERS[level]
        with connect(self.service.database) as db:
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
        if not row:
            raise NotFound("That retained owner does not exist.")
        row = dict(row)
        label = row.get("code") or row.get("number") or ident
        if level == "term":
            label = f"{row['code']} · {row['term_other'] or row['term']} {row['year']}"
        elif level == "section":
            term = self.owner("term", row["term_id"])
            label = f"{term['label']} · Section {row['number']}"
        return {**row, "label": str(label), "level": level}

    @contextmanager
    def files(self, owner, write=False):
        if owner["level"] == "term":
            with self.service.rosters.files(owner, write=write) as root:
                yield root
        else:
            with self.service.app.extensions["documents"].files.files(owner["level"], owner["id"], write=write) as root:
                yield root

    def preview(self, data):
        owner = self.owner(data.get("level"), data.get("owner_id"))
        title = text(data.get("title"), "Title", 200)
        filename = text(data.get("filename"), "Filename", 120)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]*\.(md|txt|tex|csv|json|pdf)", filename) or ".." in filename:
            raise ValueError("Use a simple filename ending in .md, .txt, .tex, .csv, .json or .pdf.")
        content = data.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 180000:
            raise ValueError("File content must contain 1 to 180000 characters.")
        fmt = filename.rsplit(".", 1)[-1]
        if fmt == "json":
            json.loads(content)
        elif fmt == "pdf":
            glyphs = pdf_font().face.charToGlyph
            missing = sorted({char for char in title + content if not char.isspace() and not glyphs.get(ord(char))})
            if missing:
                raise ValueError("The PDF font cannot render " + ", ".join(repr(c) for c in missing[:8]) + ". Use Markdown or text to preserve those characters.")
        canonical = {"level": owner["level"], "owner_id": owner["id"], "title": title, "filename": filename, "content": content}
        return {**canonical, "owner": owner["label"], "media_type": FORMATS[fmt], "fingerprint": digest(canonical),
                "note": "Creates a new retained file. Existing files are unchanged. PDF content is rendered as plain paragraphs."}

    def create(self, data):
        with self.service.rosters.lock():
            preview = self.preview(data)
            if data.get("fingerprint") != preview["fingerprint"]:
                raise Conflict("The artifact preview changed. Review it again.")
            request_id = text(data.get("request_id"), "Request ID", 120)
            with connect(self.service.database) as db:
                old = db.execute("SELECT * FROM assistant_artifacts WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["request_sha256"] != preview["fingerprint"]:
                    raise Conflict("This request ID already created a different artifact.")
                return self.view(old)
            content = preview["content"].encode("utf-8")
            if preview["media_type"] == "application/pdf":
                from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet
                buffer = io.BytesIO()
                styles = getSampleStyleSheet()
                for style in (styles["Title"], styles["BodyText"]):
                    style.fontName = pdf_font().fontName
                story = [Paragraph(escape(preview["title"]), styles["Title"]), Spacer(1, 12)]
                for paragraph in preview["content"].split("\n\n"):
                    story.extend((Paragraph(escape(paragraph).replace("\n", "<br/>"), styles["BodyText"]), Spacer(1, 8)))
                SimpleDocTemplate(buffer, title=preview["title"], author="Faculty").build(story)
                content = buffer.getvalue()
            ident = uuid.uuid4().hex
            path = f"assistant/{ident}/{preview['filename']}"
            owner = self.owner(preview["level"], preview["owner_id"])
            with self.files(owner, write=True) as root:
                publish(root / path, content)
                owners = [owner["id"] if owner["level"] == level else None for level in OWNERS]
                with connect(self.service.database) as db:
                    db.execute("INSERT INTO assistant_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ident, request_id, preview["fingerprint"], *owners, preview["title"], preview["filename"],
                         preview["media_type"], path, hashlib.sha256(content).hexdigest(), len(content), now().isoformat()))
            return self.get(ident)

    @staticmethod
    def view(row):
        return {k: row[k] for k in ("id", "definition_id", "term_id", "section_id", "title", "filename", "media_type", "sha256", "size", "created_at")} | {
            "url": f"/api/assistant/artifacts/{row['id']}/file"}

    def row(self, ident):
        with connect(self.service.database) as db:
            row = db.execute("SELECT * FROM assistant_artifacts WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise NotFound("This artifact does not exist.")
        return row

    def get(self, ident):
        return self.view(self.row(ident))

    def listing(self, level=None, owner_id=None):
        clause, args = "", ()
        if level or owner_id:
            self.owner(level, owner_id)
            clause, args = f"WHERE {OWNERS[level][1]}=?", (owner_id,)
        with connect(self.service.database) as db:
            rows = db.execute(f"SELECT * FROM assistant_artifacts {clause} ORDER BY created_at DESC,id LIMIT 100", args).fetchall()
        return {"artifacts": [self.view(r) for r in rows], "note": "Newest 100; select an owner to narrow."}

    def read(self, ident):
        with self.service.rosters.lock():
            row = self.row(ident)
            level = next(level for level, (_, column) in OWNERS.items() if row[column])
            owner = self.owner(level, row[OWNERS[level][1]])
            with self.files(owner) as root:
                content = read_verified(root, row["path"], row["sha256"])
            return self.view(row), content
