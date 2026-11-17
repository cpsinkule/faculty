"""The words of a Word or Excel file, for the Assistant's `fetch_files` (ASSISTANT "FILES" › WORD AND EXCEL FILES; the instructor,
2026-11-15, of the coordinator's mail, whose weekly agendas are Word files: "make sure the assistant has the ability to take
actions on her emails").

Run only inside the document sandbox (`document_identity.process`, operation `office_text`): the file is someone else's. It
is read as what it is, a zip of XML, with the standard library alone: no macro, formula, link or embedded object is run or
followed, nothing is rendered, and a part larger than its limit is refused rather than opened. A .docx gives its paragraphs
and table rows in reading order; an .xlsx gives each sheet's cells as they were last saved (a formula's saved value, never
recomputed), tab-separated by column. Old binary .doc and .xls files, and anything else, are not read.
"""
import io
import re
import zipfile
from xml.etree import ElementTree

PART_BYTES = 24 * 1024 ** 2   # one XML part, unpacked
TEXT_CHARS = 60_000           # what is handed back; the caller shows less and says how much more there is
SHEET_ROWS, SHEET_COLUMNS, SHEETS = 400, 40, 12
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _part(archive, name):
    info = archive.getinfo(name)
    if info.file_size > PART_BYTES:
        raise ValueError("This file is too large to read as text.")
    with archive.open(info) as handle:
        data = handle.read(PART_BYTES + 1)
    if len(data) > PART_BYTES:
        raise ValueError("This file is too large to read as text.")
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data[:4096]:
        raise ValueError("This file declares its own XML entities, which Faculty does not read.")
    return ElementTree.fromstring(data)


def _paragraph(node):
    words = []
    for part in node.iter():
        if part.tag == W + "t":
            words.append(part.text or "")
        elif part.tag == W + "tab":
            words.append("\t")
        elif part.tag in (W + "br", W + "cr"):
            words.append("\n")
    return "".join(words).strip()


def _docx(archive):
    body = _part(archive, "word/document.xml").find(W + "body")
    lines = []
    for block in list(body) if body is not None else []:
        if block.tag == W + "p":
            lines.append(_paragraph(block))
        elif block.tag == W + "tbl":
            for row in block.iter(W + "tr"):
                cells = [" ".join(filter(None, (_paragraph(p) for p in cell.iter(W + "p")))) for cell in row.findall(W + "tc")]
                lines.append(" | ".join(cells))
    # One blank line at most between paragraphs: an agenda's spacing is not its words.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _column(reference):
    letters = re.match(r"[A-Z]+", reference or "")
    number = 0
    for letter in letters.group(0) if letters else "":
        number = number * 26 + ord(letter) - 64
    return number


def _xlsx(archive):
    names = set(archive.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        for item in _part(archive, "xl/sharedStrings.xml").findall(S + "si"):
            shared.append("".join(text.text or "" for text in item.iter(S + "t")))
    book = _part(archive, "xl/workbook.xml")
    targets = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for relation in _part(archive, "xl/_rels/workbook.xml.rels").findall(PR + "Relationship"):
            target = relation.get("Target", "")
            targets[relation.get("Id")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    out = []
    for number, sheet in enumerate(book.iter(S + "sheet")):
        if number >= SHEETS:
            out.append("[more sheets not read]")
            break
        path = targets.get(sheet.get(R + "id"), "")
        if path not in names:
            continue
        out.append(f"Sheet: {sheet.get('name') or number + 1}")
        rows = 0
        for row in _part(archive, path).iter(S + "row"):
            cells = {}
            for cell in row.findall(S + "c"):
                kind, value = cell.get("t"), cell.find(S + "v")
                if kind == "inlineStr":
                    text = "".join(part.text or "" for part in cell.iter(S + "t"))
                elif value is None or value.text is None:
                    continue
                elif kind == "s":
                    index = int(value.text) if value.text.isdigit() else -1
                    text = shared[index] if 0 <= index < len(shared) else ""
                elif kind == "b":
                    text = "TRUE" if value.text == "1" else "FALSE"
                else:
                    text = value.text
                column = _column(cell.get("r"))
                if text and 1 <= column <= SHEET_COLUMNS:
                    cells[column] = " ".join(text.split())
            if not cells:
                continue
            rows += 1
            if rows > SHEET_ROWS:
                out.append("[more rows not read]")
                break
            out.append("\t".join(cells.get(column, "") for column in range(1, max(cells) + 1)))
    return "\n".join(out).strip()


def office_text(content):
    """{"kind": "docx" | "xlsx", "text": its words (at most TEXT_CHARS), "more": how many characters were left out}."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ValueError("This is not a Word or Excel file Faculty can read.") from None
    with archive:
        names = set(archive.namelist())
        try:
            if "word/document.xml" in names:
                kind, text = "docx", _docx(archive)
            elif "xl/workbook.xml" in names:
                kind, text = "xlsx", _xlsx(archive)
            else:
                raise ValueError("This is not a Word or Excel file Faculty can read.")
        except (ElementTree.ParseError, KeyError, zipfile.BadZipFile):
            raise ValueError("This Word or Excel file could not be read.") from None
    return {"kind": kind, "text": text[:TEXT_CHARS], "more": max(0, len(text) - TEXT_CHARS)}
