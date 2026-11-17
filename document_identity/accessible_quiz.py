"""QUIZZES "ACCESSIBLE COPY": a drafted quiz's version as a screen-readable Word file for the DR center.

The copy is built from the same structured value the printed
version is typeset from (`quiz_latex.clean`), never from its PDF: Faculty writes a plain LaTeX body (headings, numbered problems,
lettered choices and parts, the model's statements and tables) and pandoc turns it into .docx, the math into Word equations a screen
reader speaks. Nothing is kept: the copy is made again on each request from the approved revision.

One problem per page (requirement, 2026-10-31):
each problem after the first starts a new page, with its section heading when the kind of problem changes there, so the rest of
its page is the room to work; the break is a page break run, never empty paragraphs a screen reader would read out.

Page codes (requirement, 2026-11-10; GRADING "DR COPIES" > STAMPS): given `stamps`, `build` prints each page's own code at its
places, as pictures anchored to the page and marked decorative, so a screen reader passes over them.

`build` refuses (`Unavailable`) what a screen reader could not read: a figure (no alt text is written for one yet) or any LaTeX
pandoc leaves unconverted. Layout-only commands (spacing, alignment, the table stretch) are dropped first; an answer blank
(`\\underline{\\hspace{..}}`) becomes the words "[blank]"."""
import io
import json
import os
import re
import subprocess
import zipfile

from . import quiz_latex

RULE = "faculty-accessible-quiz-v2"
PANDOC = "/mnt/nvme/Teaching/tools/pandoc/pandoc-3.6.4/bin/pandoc"
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

BLANK = re.compile(r"\\underline\s*\{\s*\\hspace\s*\*?\s*\{[^{}]*\}\s*\}")
# An equation left open for the answer ("$x=$" and a blank): Word draws an equation ending in "=" with a "¿" for the missing side,
# and a screen reader reads that too, so the blank goes inside the equation as its right side.
OPEN_EQUATION = re.compile(r"\$([^$]*?(?:[=<>+\-]|\\(?:leq?|geq?|neq|approx|cdot|times|div|pm)))\s*\$\s*(" + BLANK.pattern + ")")
# Spacing and alignment only: dropped with their arguments, so pandoc never sees them (it would keep them as raw LaTeX).
LAYOUT = re.compile(r"\\renewcommand\s*\{\s*\\arraystretch\s*\}\s*\{[^{}]*\}|\\[hv]space\s*\*?\s*\{[^{}]*\}|"
                    r"\\(?:noindent|hfill|hfil|smallskip|medskip|bigskip|centering|raggedright|raggedleft)(?![a-zA-Z])\s*|"
                    r"(?<=\\\\)\[[^\]]*\]")
PARAGRAPH = re.compile(r"\\par(?![a-zA-Z])\s*")
TABLE = re.compile(r"\\begin\s*\{(tabularx?)\}\s*")
PAGE = "FACULTYNEWPAGE"  # the marker paragraph `source` writes before each problem after the first; `build` makes it a page break
BREAK = ["openxml", '<w:p><w:r><w:br w:type="page"/></w:r></w:p>']


class Unavailable(ValueError):
    """Why a version has no accessible copy: [reason, ...]."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def pandoc():
    return os.environ.get("FACULTY_PANDOC") or PANDOC


def _group(text, at):
    """(the text inside the brace group opening at `at`, the index after it)."""
    depth = 0
    for index in range(at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[at + 1:index], index + 1
    raise ValueError("unbalanced braces")


def columns(spec):
    """A table's column letters only: array's >{..} <{..} @{..} !{..} and rules dropped, p/m/b{width} and X read as plain columns,
    *{n}{..} repeated. Only the columns matter to a screen reader."""
    found, index = [], 0
    while index < len(spec):
        character = spec[index]
        if character in "><@!":
            index = _group(spec, spec.index("{", index))[1]
        elif character in "pmb":
            index = _group(spec, spec.index("{", index))[1]
            found.append("l")
        elif character == "*":
            count, index = _group(spec, spec.index("{", index))
            inner, index = _group(spec, spec.index("{", index))
            found += list(columns(inner)) * int(count)
        else:
            found += {"l": ["l"], "c": ["c"], "r": ["r"], "X": ["l"]}.get(character, [])
            index += 1
    return "".join(found)


def tables(fragment):
    """Every tabular and tabularx as a plain tabular of its columns (`columns`)."""
    out, index = [], 0
    for match in TABLE.finditer(fragment):
        if match.start() < index:
            continue
        at = match.end()
        if match.group(1) == "tabularx":
            at = _group(fragment, fragment.index("{", at))[1]
        spec, after = _group(fragment, fragment.index("{", at))
        out += [fragment[index:match.start()], "\\begin{tabular}{" + columns(spec) + "}"]
        index = after
    out.append(fragment[index:])
    return re.sub(r"\\end\s*\{tabularx\}", r"\\end{tabular}", "".join(out))


ARRAY = re.compile(r"(?:\\\[|\$\$)\s*\\begin\s*\{array\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}(.*?)\\end\s*\{array\}\s*(?:\\\]|\$\$)", re.S)


def arrays(fragment):
    """A displayed array (a simplex tableau, a payoff matrix) as a table a screen reader moves through cell by cell, each cell its
    own equation; its first row is the table's header row where every cell of it names something (holds a letter)."""
    def table(match):
        rows = [[cell.strip() for cell in row.split("&")] for row in re.split(r"\\\\", re.sub(r"\\hline", "", match.group(2)))]
        rows = [row for row in rows if any(row)]
        cells = [" & ".join(f"${cell}$" if cell else "" for cell in row) for row in rows]
        header = rows and all(re.search(r"[A-Za-z]", re.sub(r"\\[a-zA-Z]+", "", cell)) for cell in rows[0])
        body = (cells[0] + r" \\ \hline " + r" \\ ".join(cells[1:])) if header else r" \\ ".join(cells)
        return r"\begin{tabular}{" + columns(match.group(1)) + "}" + body + r" \\ \end{tabular}"
    return ARRAY.sub(table, fragment)


def plain(fragment):
    closed = OPEN_EQUATION.sub(lambda match: f"${match.group(1)} \\text{{[blank]}}$", tables(arrays(fragment or "")))
    return PARAGRAPH.sub("\n\n", LAYOUT.sub(" ", BLANK.sub("[blank]", closed))).strip()


def points_text(points):
    return f"{points:g} point{'' if points == 1 else 's'}"


def figures(quiz, letter):
    """Problem numbers whose version `letter` has a figure."""
    return [problem["number"] for problem in quiz["problems"] if problem["content"][letter]["figure"]]


def source(quiz, letter):
    """The LaTeX body pandoc reads for one version: the sections line and points, the directions, a heading where the kind of
    problem changes, and each problem as a heading of its own followed by its statement, table, lettered choices or parts and
    answer prompts. Correct choices, solutions and final answers are never in it."""
    if letter not in quiz["versions"]:
        raise ValueError(f"Version {letter} is not a version of this quiz.")
    lines = [f"{quiz_latex.escape(quiz['sections_line'])}. {points_text(quiz['total'])} in all."]
    if quiz["directions"]:
        lines.append(r"\textbf{Directions:} " + plain(quiz["directions"]))
    kind = None
    for index, problem in enumerate(quiz["problems"]):
        content = problem["content"][letter]
        if index:
            lines.append(PAGE)
        if problem["kind"] != kind:
            kind = problem["kind"]
            lines.append(rf"\section*{{{quiz_latex.HEADINGS[kind]}}}")
        tag = f" ({quiz_latex.escape(problem['tag'])})" if problem["tag"] else ""
        lines.append(rf"\subsection*{{Problem {problem['number']}{tag}, {points_text(problem['points'])}}}")
        lines.append(plain(content["statement"]))
        if content["left"]:
            lines.append(plain(content["left"]))
        for items in (content["choices"], [part["text"] for part in content["parts"]]):
            if items:
                lines.append(r"\begin{enumerate}[(a)]" + "".join(r"\item " + plain(item) for item in items) + r"\end{enumerate}")
        if content["scaffold"]:
            lines.append(plain(content["scaffold"]))
    return "\n\n".join(lines) + "\n"


def _pages(blocks):
    """The top-level blocks with each PAGE marker paragraph made a page break."""
    return [{"t": "RawBlock", "c": BREAK} if block.get("t") == "Para" and block["c"] == [{"t": "Str", "c": PAGE}] else block
            for block in blocks]


def _raw(node, found):
    if isinstance(node, dict):
        if node.get("t") in ("RawInline", "RawBlock") and node["c"][0] in ("latex", "tex"):
            found.append(" ".join(node["c"][1].split())[:80])
        for value in node.values():
            _raw(value, found)
    elif isinstance(node, list):
        for value in node:
            _raw(value, found)
    return found


def _run(arguments, data):
    result = subprocess.run([pandoc(), "--sandbox", *arguments], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=60, check=False)
    if result.returncode:
        said = " ".join(result.stderr.decode(errors="replace").split())[:160]
        raise Unavailable([f"The converter could not read it ({said})"])
    # pandoc keeps math it cannot turn into a Word equation as its LaTeX text, which a screen reader would spell out.
    unread = re.findall(r"Could not convert TeX math (.*?), rendering as TeX", result.stderr.decode(errors="replace"), re.S)
    if unread:
        raise Unavailable([f"Math the converter cannot read: {', '.join(' '.join(math.split())[:80] for math in unread[:3])}"])
    return result.stdout


STAMPS = "faculty-accessible-stamps-v1"
EMU = 36000  # English Metric Units to a millimetre
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
ANCHOR = (
    '<wp:anchor xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0" '
    'simplePos="0" relativeHeight="{order}" behindDoc="0" locked="1" layoutInCell="0" allowOverlap="1">'
    '<wp:simplePos x="0" y="0"/>'
    '<wp:positionH relativeFrom="page"><wp:posOffset>{left}</wp:posOffset></wp:positionH>'
    '<wp:positionV relativeFrom="page"><wp:posOffset>{top}</wp:posOffset></wp:positionV>'
    '<wp:extent cx="{size}" cy="{size}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
    # Decorative: a screen reader passes over it (WCAG 2.1, 1.1.1: decoration is implemented so that it can be ignored).
    '<wp:docPr id="{identifier}" name="Page code {identifier}">'
    '<a:extLst xmlns:a="' + DRAWING + '"><a:ext uri="{{C183D7F6-B498-43B3-948B-1728B52AA6E4}}">'
    '<adec:decorative xmlns:adec="http://schemas.microsoft.com/office/drawing/2017/decorative" val="1"/></a:ext></a:extLst></wp:docPr>'
    '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="' + DRAWING + '" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
    '<a:graphic xmlns:a="' + DRAWING + '"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
    '<pic:nvPicPr><pic:cNvPr id="{identifier}" name="page-code-{page}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip xmlns:r="' + RELATIONSHIPS + '" r:embed="{relationship}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
    '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{size}" cy="{size}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
    '</pic:pic></a:graphicData></a:graphic></wp:anchor>')


def _stamp_run(stamps, page):
    """Page `page`'s code at each of its places, each a run of its own holding one drawing: pictures anchored to the page itself, so
    they print where they are put whatever font and line breaks the page's words get."""
    size = round(stamps["size_mm"] * EMU)
    runs = "".join("<w:r><w:drawing>" + ANCHOR.format(
        order=251659264 + 16 * page + index, left=round(left * EMU), top=round(top * EMU), size=size,
        identifier=9000 + 16 * page + index, page=page, relationship=f"rIdPageCode{page}") + "</w:drawing></w:r>"
        for index, (left, top) in enumerate(stamps["places_mm"]))
    return {"t": "RawInline", "c": ["openxml", runs]}


def _stamped(blocks, stamps):
    """The blocks with each page's code run put first in the first paragraph or heading of its page (a page starts at the first
    block and after each page break `_pages` made)."""
    page, wanted, out = 0, True, []
    for block in blocks:
        if block.get("t") == "RawBlock" and block["c"] == BREAK:
            wanted = True
        elif wanted and block.get("t") in ("Para", "Plain", "Header"):
            page, wanted = page + 1, False
            if page > len(stamps["pictures"]):
                raise Unavailable(["It has more pages than page codes were made for"])
            inlines = block["c"][2] if block["t"] == "Header" else block["c"]
            block = {**block, "c": [*block["c"][:2], [_stamp_run(stamps, page), *inlines]] if block["t"] == "Header" else [_stamp_run(stamps, page), *inlines]}
        out.append(block)
    if wanted or page != len(stamps["pictures"]):
        raise Unavailable([f"Page codes were made for {len(stamps['pictures'])} pages, but it has {page}"])
    return out


def _with_pictures(document, pictures):
    """The .docx with each page's code picture added as a part of it (`word/media/page-code-<n>.png`, related as `rIdPageCode<n>`).
    The same bytes for the same document and pictures."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(document)) as archive, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as changed:
        for item in archive.infolist():
            content = archive.read(item.filename)
            if item.filename == "[Content_Types].xml" and b'Extension="png"' not in content:
                # Among the other defaults, ahead of the part overrides, where Word itself writes them.
                content = re.sub(rb"(<Types\b[^>]*>)", rb'\1<Default Extension="png" ContentType="image/png"/>', content, count=1)
            elif item.filename == "word/_rels/document.xml.rels":
                added = "".join(f'<Relationship Id="rIdPageCode{page}" Type="{RELATIONSHIPS}/image" Target="media/page-code-{page}.png"/>'
                                for page in range(1, len(pictures) + 1))
                content = content.replace(b"</Relationships>", added.encode() + b"</Relationships>")
            changed.writestr(item, content)
        for page, picture in enumerate(pictures, 1):
            changed.writestr(zipfile.ZipInfo(f"word/media/page-code-{page}.png", date_time=(1980, 1, 1, 0, 0, 0)), picture)
    return out.getvalue()


def build(quiz, letter, title, stamps=None):
    """(.docx bytes, {"rule", "problems", "equations"}) of one version, or Unavailable. `stamps` ({"pictures": one PNG per page,
    "size_mm", "places_mm": [(left, top) from the page's corner, ...]}; GRADING "DR COPIES" > STAMPS) prints each page's own code
    at those places on it: a picture marked decorative, anchored to the page. Without it the copy is exactly what it always was."""
    reasons = [f"Problem {number} has a figure, and Faculty does not write alt text for figures yet" for number in figures(quiz, letter)]
    if reasons:
        raise Unavailable(reasons)
    try:
        body = source(quiz, letter)
    except ValueError as error:
        raise Unavailable([f"A table the converter cannot read ({error})"]) from error
    tree = json.loads(_run(["-f", "latex+raw_tex", "-t", "json"], body.encode()))
    tree["blocks"] = _pages(tree["blocks"])
    left = _raw(tree["blocks"], [])
    if left:
        raise Unavailable([f"LaTeX the converter cannot read: {', '.join(sorted(set(left))[:5])}"])
    if stamps:
        tree["blocks"] = _stamped(tree["blocks"], stamps)
    tree["meta"] = {"title": {"t": "MetaInlines", "c": [{"t": "Str", "c": title}]},
                    "lang": {"t": "MetaString", "c": "en-US"}}
    document = _run(["-f", "json", "-t", "docx", "-o", "-"], json.dumps(tree).encode())
    equations = len(re.findall(rb"<m:oMath>", _document_xml(document)))
    if stamps:
        document = _with_pictures(document, stamps["pictures"])
    return document, {"rule": RULE, "problems": len(quiz["problems"]), "equations": equations, **({"stamps": STAMPS} if stamps else {})}


def _document_xml(document):
    with zipfile.ZipFile(io.BytesIO(document)) as archive:
        return archive.read("word/document.xml")


def for_rendering(document):
    """The same .docx without the page count its properties carry. pandoc's own template writes "1 page" there under Word's
    name, whatever the copy holds; the template converter takes a Word count for how the printed paper broke and refuses a
    rendering that disagrees (ASSIGNMENT_TEMPLATES.md). Only the copy a blank is rendered from loses the count (GRADING "DR
    COPIES"); the file the DR center gets is unchanged."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(document)) as archive, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as changed:
        for item in archive.infolist():
            content = archive.read(item.filename)
            if item.filename == "docProps/app.xml":
                content = re.sub(rb"<Pages>\s*\d+\s*</Pages>", b"", content)
            changed.writestr(item, content)
    return out.getvalue()


# What `build` found for an approved revision's version: an approved revision never changes, so its answer is kept for later reads
# of the DR quizzes tab (PERFORMANCE: immutable rows only, bounded).
_READINESS = {}
_READINESS_LIMIT = 128


def readiness(revision_id, quiz, letter, title):
    """[] when version `letter` of the approved revision `revision_id` has an accessible copy, else the reasons it has none."""
    key = (revision_id, letter, RULE, pandoc())
    if key not in _READINESS:
        try:
            build(quiz, letter, title)
            found = []
        except Unavailable as error:
            found = error.reasons
        except (OSError, subprocess.TimeoutExpired):
            return ["The Word converter (pandoc) could not run on this computer"]
        if len(_READINESS) >= _READINESS_LIMIT:
            _READINESS.pop(next(iter(_READINESS)))
        _READINESS[key] = found
    return list(_READINESS[key])
