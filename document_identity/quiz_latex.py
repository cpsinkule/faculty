"""A drafted quiz as LaTeX: Faculty writes main.tex, the model writes only what goes inside each problem.

The earlier quizzes (Chapter 2 and 3 Quizzes, MATH 140) reached Faculty only as PDFs, so their look
is rebuilt in `templates/facultyquiz.sty`, inside the `facultyassignment` class whose stamp box every printed page keeps empty.

A quiz is a structured value (`clean`): its title line, sections line, directions, and its problems in printed order, each with the
kind (multiple choice or free response), points, the page it is printed on, how much of the page's free space it gets, and per
version the LaTeX fragments of its statement, choices, parts, figure and answer scaffold. `render` assembles one version's main.tex
from that value and nothing else; every fragment is checked first (`fragment`), so a model can neither load files nor change the
page layout. The class writes each problem's start and end to the build log (`\\pdfsavepos`), which `layout` reads: whether each
problem stayed on its page, how much room is left to write in, and the answer area of each problem, from its heading to the next
problem's heading or the page's end across the whole sheet (the rule `grading.locations.AREAS` proposes from a template's reading).
"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"
STYLE = "facultyquiz.sty"
RULE = "faculty-quiz-v1"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
KINDS = ("multiple_choice", "free_response")
HEADINGS = {"multiple_choice": "Multiple Choice", "free_response": "Free Response"}
MAX_PROBLEMS = 20
MAX_PAGES = 8
MAX_FRAGMENT = 6000
MAX_FIGURE = 12000
MAX_CHOICES = 8
MAX_PARTS = 8
# Lengths below are TeX points (72.27 to the inch), as pdfTeX reports positions and the page height.
MM = 72.27 / 25.4
# Room above a problem's first baseline that its area includes, so the printed heading stays in the crop.
ABOVE = 5.5 * MM
# Less room than this between a free-response problem's end and what follows it is reported: 3 cm of paper to write on.
ROOM = 30 * MM
# The class's reserved bottom margin: nothing is written below it. 20 mm since the two 7 mm corner codes became the class default
# (2026-10-18; 30 mm under the 10 mm corner codes from 2026-10-01; 40 mm under the single bottom-right code, `pagecode`).
BOTTOM = 20 * MM
SP = 65536  # scaled points per TeX point

# Anything that reads or writes files, defines or redefines commands, changes category codes or the page itself. The sandbox
# already has no network and no shell escape; this keeps each fragment to content, so the stamp box and the layout stay Faculty's.
FORBIDDEN = re.compile(
    r"\\(?:input|include|includeonly|InputIfFileExists|openin|openout|read|readline|write|immediate|message|special|"
    r"catcode|lccode|uccode|csname|endcsname|expandafter|makeatletter|makeatother|def|edef|gdef|xdef|let|futurelet|"
    r"newcommand|renewcommand|providecommand|DeclareRobustCommand|newenvironment|renewenvironment|newcounter|setcounter|"
    r"addtocounter|stepcounter|usepackage|RequirePackage|documentclass|usetikzlibrary|tikzset|pgfkeys|"
    r"newpage|clearpage|cleardoublepage|pagebreak|nopagebreak|enlargethispage|newgeometry|geometry|thispagestyle|pagestyle|"
    r"AtBeginShipout|AtEndDocument|AtBeginDocument|shipout|output|vsize|"
    r"pdf[a-zA-Z]*|includegraphics|verbatim|lstinputlisting|quiz[a-zA-Z@]*setup|quiznameline|quizheader|quiztitle|quizsubtitle|"
    r"quizdirections|quizsection|quizproblem|quizbody|quizend|end\s*\{document\}|begin\s*\{document\}|begin\s*\{filecontents)(?![a-zA-Z])")
# The one redefinition the instructor's own quizzes use, for a taller table: \renewcommand{\arraystretch}{1.5}.
ALLOWED = re.compile(r"\\renewcommand\s*\{\s*\\arraystretch\s*\}\s*\{\s*\d(?:\.\d+)?\s*\}")
# A negative or starred vertical skip can pull text over the heading above or push it into the stamp band.
SKIP = re.compile(r"\\(?:vspace\s*\*|vspace\s*\{\s*-|vskip|kern|vfil|vfill|vss|moveup|movedown|raisebox\s*\{\s*-)")
# Reading the text width is fine (a table as wide as the text); changing a page dimension is not.
LENGTHS = re.compile(r"\\(?:setlength|addtolength)\s*\{?\s*\\(?:text|paper|line|column|head|foot|top|odd|even|marginpar)|"
                     r"\\(?:hsize|textwidth|textheight|linewidth|paperheight|paperwidth|topmargin|voffset|hoffset)\s*=")


# Nothing printed may say which version a paper is (requirement, 2026-10-01): Faculty's header and footer print none, and a printed field naming one is refused.
VERSION_NAMED = re.compile(r"\b[Vv]ersions?\s*~?\s*(?:\\[a-zA-Z]+\s*\{\s*)?[A-Z]\b")


def unversioned(value, what):
    """`value`, or ValueError when it names a version ("Version A") on paper the students get."""
    found = VERSION_NAMED.search(value or "")
    if found:
        raise ValueError(f"{what} says \"{found.group(0)}\": nothing printed may say which version a paper is; leave the version out.")
    return value


# A percent sign no odd backslash escapes starts a TeX comment; so does one after a line break (two backslashes).
COMMENT = re.compile(r"(?<!\\)(?:\\\\)*%")
BRACE = re.compile(r"(\\*)([{}])")


def fragment(value, what, limit=MAX_FRAGMENT):
    """One model-written LaTeX fragment, or ValueError naming what to change."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be LaTeX text.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{what} is longer than {limit} characters.")
    if "\x00" in value or COMMENT.search(value):
        raise ValueError(f"{what} contains a comment character: write a percent sign as \\%.")
    checked = ALLOWED.sub("", value)
    found = FORBIDDEN.search(checked) or SKIP.search(checked) or LENGTHS.search(checked)
    if found:
        raise ValueError(f"{what} uses {found.group(0).strip()}, which a quiz fragment may not use.")
    unversioned(value, what)
    depth = 0
    for brace in BRACE.finditer(value):
        if len(brace.group(1)) % 2:
            continue  # an odd run of backslashes escapes the brace; an even one (a line break) leaves it live
        depth += 1 if brace.group(2) == "{" else -1
        if depth < 0:
            break
    if depth:
        raise ValueError(f"{what} has unbalanced braces.")
    begins = re.findall(r"\\begin\s*\{([^}]*)\}", value)
    ends = re.findall(r"\\end\s*\{([^}]*)\}", value)
    if sorted(begins) != sorted(ends):
        raise ValueError(f"{what} opens and closes different environments.")
    return value


def points_text(points):
    value = f"{points:g}"
    return f"{value} pt" if points == 1 else f"{value} pts"


def plain(value, what, limit):
    """The title line of the header: one line of at most `limit` characters with its whitespace collapsed (LaTeX special characters
    are escaped later by `escape` in `render`)."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\n" in value.strip():
        raise ValueError(f"{what} must be one line of at most {limit} characters.")
    value = " ".join(value.split())
    return value


def escape(value):
    table = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
             "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "–": "--", "—": "---"}
    return "".join(table.get(character, character) for character in value)


def render(quiz, letter):
    """main.tex of one version. `quiz` is a value `clean` accepted; `letter` one of its versions."""
    if letter not in quiz["versions"]:
        raise ValueError(f"Version {letter} is not a version of this quiz.")
    pages = max(problem["page"] for problem in quiz["problems"])
    lines = [r"\documentclass[letterpaper,12pt]{facultyassignment}", r"\usepackage{facultyquiz}",
             rf"\quizsetup{{{letter}}}{{{pages}}}", r"\begin{document}", r"\quiznameline",
             rf"\quizheader{{{escape(quiz['title_line'])}}}{{{escape(quiz['sections_line'])}}}{{{letter}}}{{{quiz['total']:g} point{'' if quiz['total'] == 1 else 's'}}}"]
    if quiz["directions"]:
        lines.append(rf"\quizdirections{{{quiz['directions']}}}")
    previous = None
    for page in range(1, pages + 1):
        on = [p for p in quiz["problems"] if p["page"] == page]
        if page > 1:
            lines.append(r"\newpage")
        for index, problem in enumerate(on):
            if problem["kind"] != (previous or {}).get("kind") or index == 0:
                continued = previous is not None and problem["kind"] == previous["kind"]
                lines.append(rf"\quizsection{{{HEADINGS[problem['kind']]}{' (continued)' if continued else ''}}}")
            lines += problem_lines(problem, problem["content"][letter])
            space = problem["space"]
            lines.append(rf"\vspace{{\stretch{{{space:g}}}}}" if space > 0 else r"\vspace{0.05in}")
            previous = problem
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def problem_lines(problem, content):
    marker = f"P{problem['number']}"
    head = rf"\quizproblem{{{problem['number']}}}{{{points_text(problem['points'])}}}{{{escape(problem['tag'])}}}{{{marker}}}"
    lines = [head + content["statement"]]
    beside = []
    if content["left"]:
        beside.append(content["left"])
    if content["parts"]:
        beside.append(r"\begin{quizparts}" + "".join(rf"\quizpart{{{part['label']}}}{{{part['text']}}}" for part in content["parts"]) + r"\end{quizparts}")
    if problem["kind"] == "multiple_choice":
        beside.append(r"\begin{quizchoices}" + "".join(rf"\quizchoice{{{LETTERS[i].lower()}}}{{{choice}}}" for i, choice in enumerate(content["choices"]))
                      + r"\end{quizchoices}")
    if content["figure"] and content["figure_side"] == "right":
        lines.append(r"\par\noindent\begin{minipage}[t]{0.52\linewidth}\vspace{0pt}" + "\n".join(beside)
                     + r"\end{minipage}\hfill\begin{minipage}[t]{0.46\linewidth}\vspace{0pt}\centering" + "\n" + content["figure"] + r"\end{minipage}\par")
    else:
        lines += beside
        if content["figure"]:
            lines.append(r"\par\vspace{3mm}\begin{center}" + "\n" + content["figure"] + "\n" + r"\end{center}")
    lines.append(rf"\quizbody{{{marker}}}")
    if content["scaffold"]:
        lines.append(r"\par\smallskip" + "\n" + content["scaffold"])
    lines.append(rf"\quizend{{{marker}}}")
    return lines


def style():
    return (TEMPLATES / STYLE).read_bytes()


def sources(quiz, letter):
    """The files a version is compiled from: Faculty's main.tex and the quiz style (the class is added by the compiler)."""
    return {"main.tex": render(quiz, letter).encode(), STYLE: style()}


MARK = re.compile(r"FACULTYQUIZ (start|body|end) (P\d+) (\d+) (-?\d+) (-?\d+) (\d+(?:\.\d+)?)pt (\d+(?:\.\d+)?)pt")


def layout(log, quiz, page_count):
    """What the build log says about where each problem was printed, from the last pass's marks: {"problems": {number: {page, top,
    end_page, bottom, height, body, scaffold}} (page fractions from the top), "faults": [layout faults to fix], "notes": [little
    room to write]}."""
    marks = {}
    for kind, marker, page, x, y, height, width in MARK.findall(log.decode("utf-8", "replace") if isinstance(log, bytes) else log):
        height = float(height)
        marks[(kind, marker)] = {"page": int(page), "y": 1 - (int(y) / SP) / height, "height": height}
    faults, notes, placed = [], [], {}
    expected = max(p["page"] for p in quiz["problems"])
    if page_count != expected:
        faults.append(f"The quiz was laid out for {expected} page{'s' if expected != 1 else ''} but printed on {page_count}: "
                      "shorten or move problems so each page's problems fit on it.")
    for problem in quiz["problems"]:
        marker = f"P{problem['number']}"
        start, body, end = marks.get(("start", marker)), marks.get(("body", marker)), marks.get(("end", marker))
        if not start or not end:
            faults.append(f"Problem {problem['number']} was not printed; check its LaTeX.")
            continue
        if start["page"] != problem["page"]:
            faults.append(f"Problem {problem['number']} was planned for page {problem['page']} but starts on page {start['page']}: "
                          "the problems before it do not fit on their page.")
        if end["page"] != start["page"]:
            faults.append(f"Problem {problem['number']} starts on page {start['page']} and runs onto page {end['page']}: shorten it or "
                          "move it to the next page.")
        placed[problem["number"]] = {"page": start["page"], "top": max(0.0, start["y"] - ABOVE / start["height"]),
                                     "end_page": end["page"], "bottom": end["y"], "height": start["height"],
                                     # Without an answer scaffold the body ends where the problem does; a scaffold's own blanks are room to write.
                                     "body": body["y"] if body and body["page"] == start["page"] else end["y"],
                                     "scaffold": bool(problem["content"] and any(c["scaffold"] for c in problem["content"].values()))}
    for number, room in rooms(placed, quiz).items():
        if room < ROOM:
            notes.append(f"Problem {number} leaves {room / MM / 10:.1f} cm to write in.")
    return {"problems": placed, "faults": faults, "notes": notes}


def rooms(placed, quiz):
    """{number: points of room to write in} for each free-response problem that was printed: from the end of its body (or of its
    scaffold's printed text) to the next problem's heading on its page, or to the reserved bottom margin."""
    found = {}
    for problem in quiz["problems"]:
        here = placed.get(problem["number"])
        if not here or problem["kind"] != "free_response":
            continue
        after = [p for n, p in placed.items() if p["page"] == here["page"] and p["top"] > here["top"] and n != problem["number"]]
        limit = min((p["top"] for p in after), default=1 - BOTTOM / here["height"])
        found[problem["number"]] = (limit - (here["body"] if here["scaffold"] else here["bottom"])) * here["height"]
    return found


def areas(placed):
    """Each problem's answer area as grading stores regions: from its heading to the next problem's heading on the same page, or to
    the end of the page, across the whole sheet (`grading.locations.AREAS`, heading-to-next-problem-v1)."""
    found = {}
    for number, here in placed.items():
        below = [p["top"] for n, p in placed.items() if n != number and p["page"] == here["page"] and p["top"] > here["top"]]
        bottom = min(below, default=1.0)
        found[number] = [{"page": here["page"], "region": {"x": 0.0, "y": round(here["top"], 6), "width": 1.0,
                                                           "height": round(bottom - here["top"], 6)}, "privacy_masks": []}]
    return found


def _number(value, what, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not low <= value <= high:
        raise ValueError(f"{what} must be a number from {low:g} to {high:g}.")
    return round(float(value), 4)


def _text(value, what, limit):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{what} must be text of at most {limit} characters.")
    return value.strip()


def clean(value, letters, *, title_line, total=None):
    """A drafted quiz, checked and normalized, or ValueError saying what to change. `letters` are the versions asked for, in order;
    `total` the points the quiz must add up to (None: any). Problems are numbered by position."""
    if not isinstance(value, dict):
        raise ValueError("The quiz must be an object.")
    letters = list(letters)
    sections_line = unversioned(_text(value.get("sections_line"), "The sections line", 200), "The sections line")
    if not sections_line or "\n" in sections_line:
        raise ValueError("Give the sections line, for example \"Sections 4.1–4.3\".")
    problems = value.get("problems")
    if not isinstance(problems, list) or not 1 <= len(problems) <= MAX_PROBLEMS:
        raise ValueError(f"A quiz has 1 to {MAX_PROBLEMS} problems.")
    found, page = [], 1
    for index, problem in enumerate(problems, 1):
        where = f"Problem {index}"
        if not isinstance(problem, dict):
            raise ValueError(f"{where} must be an object.")
        kind = problem.get("kind")
        if kind not in KINDS:
            raise ValueError(f"{where} must be multiple_choice or free_response.")
        points = _number(problem.get("points"), f"{where}'s points", 0.5, 100)
        on = problem.get("page")
        if type(on) is not int or not page <= on <= min(page + 1, MAX_PAGES):
            raise ValueError(f"{where}'s page must be the page of the problem before it or the next one (pages start at 1, at most {MAX_PAGES}).")
        page = on
        space = _number(problem.get("space", 1 if kind == "free_response" else 0), f"{where}'s space", 0, 10)
        tag = unversioned(_text(problem.get("tag"), f"{where}'s tag", 80), f"{where}'s tag")
        parts, labels = [], []
        for part in problem.get("parts") or []:
            if not isinstance(part, dict):
                raise ValueError(f"{where} has an invalid part.")
            label = _text(part.get("label"), "A part label", 2).strip("()").lower()
            if not re.fullmatch(r"[a-h]", label) or label in labels or label != "abcdefgh"[len(labels)]:
                raise ValueError(f"{where}'s parts must be lettered a, b, c ... in order.")
            labels.append(label)
            parts.append({"label": label, "points": _number(part.get("points"), f"{where} part ({label})'s points", 0.5, points)})
        if len(parts) > MAX_PARTS:
            raise ValueError(f"{where} has more than {MAX_PARTS} parts.")
        if parts and abs(sum(p["points"] for p in parts) - points) > 1e-9:
            raise ValueError(f"{where}'s parts add up to {sum(p['points'] for p in parts):g} points, but it is worth {points:g}.")
        if kind == "multiple_choice" and parts:
            raise ValueError(f"{where} is multiple choice and has no parts.")
        content = {}
        for entry in problem.get("versions") or []:
            letter = entry.get("version") if isinstance(entry, dict) else None
            if letter not in letters or letter in content:
                raise ValueError(f"{where} must give each of Versions {', '.join(letters)} exactly once.")
            said = f"{where}, Version {letter}"
            statement = fragment(entry.get("statement"), f"{said}'s statement")
            if not statement:
                raise ValueError(f"{said} has no statement.")
            choices, correct = [], ""
            if kind == "multiple_choice":
                raw = entry.get("choices")
                if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_CHOICES:
                    raise ValueError(f"{said} needs 2 to {MAX_CHOICES} choices.")
                choices = [fragment(choice, f"{said}'s choice ({LETTERS[i].lower()})") for i, choice in enumerate(raw)]
                if not all(choices):
                    raise ValueError(f"{said} has an empty choice.")
                correct = _text(entry.get("correct_choice"), f"{said}'s correct choice", 3).strip("()").lower()
                if correct not in [LETTERS[i].lower() for i in range(len(choices))]:
                    raise ValueError(f"{said}'s correct choice must be one of its choice letters.")
            texts = entry.get("parts") or []
            if not isinstance(texts, list) or [(t.get("label") or "").strip("()").lower() if isinstance(t, dict) else None for t in texts] != labels:
                raise ValueError(f"{said} must give the text of parts {', '.join(labels) or '(none)'} in order.")
            figure = fragment(entry.get("figure"), f"{said}'s figure", MAX_FIGURE)
            if figure and not re.fullmatch(r"\\begin\{tikzpicture\}.*\\end\{tikzpicture\}", figure, re.S):
                raise ValueError(f"{said}'s figure must be one tikzpicture environment.")
            side = entry.get("figure_side") or ("right" if figure else "")
            if side not in ("right", "below", ""):
                raise ValueError(f"{said}'s figure goes right or below.")
            answers = []
            for answer in entry.get("final_answers") or []:
                if isinstance(answer, dict) and _text(answer.get("answer"), "A final answer", 2000):
                    answers.append({"part": _text(answer.get("part"), "A final answer's part", 3).strip("()").lower(),
                                    "answer": _text(answer.get("answer"), "A final answer", 2000)})
            content[letter] = {"statement": statement, "left": fragment(entry.get("left"), f"{said}'s left column"),
                               "parts": [{"label": label, "text": fragment(t.get("text"), f"{said} part ({label})")} for label, t in zip(labels, texts)],
                               "choices": choices, "correct_choice": correct, "figure": figure, "figure_side": side if figure else "",
                               "scaffold": fragment(entry.get("scaffold"), f"{said}'s answer scaffold"),
                               "solution": _text(entry.get("solution"), f"{said}'s solution", 20000), "final_answers": answers}
        if set(content) != set(letters):
            raise ValueError(f"{where} must give each of Versions {', '.join(letters)}.")
        found.append({"number": index, "kind": kind, "tag": tag, "points": points, "page": page, "space": space, "parts": parts,
                      "content": {letter: content[letter] for letter in letters}})
    worth = round(sum(p["points"] for p in found), 4)
    if total is not None and abs(worth - total) > 1e-9:
        raise ValueError(f"The problems add up to {worth:g} points, but the quiz is worth {total:g}.")
    return {"rule": RULE, "title_line": plain(title_line, "The title line", 200), "sections_line": sections_line,
            "directions": fragment(value.get("directions"), "The directions"), "total": worth, "versions": letters, "problems": found}
