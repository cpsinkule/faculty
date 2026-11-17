"""What a bank question holds (AGENTS.md "Question bank"), and how a question from an approved assessment or quiz becomes one.

A bank question's CONTENT is one object, validated here on every write: its kind (multiple choice or free response), the number it was
printed with, its statement (text with LaTeX between $...$), lettered parts, choices and the correct one, a TikZ figure (quizzes drafted
in Faculty carry their figure's source), its points, final answers per part, a worked solution and notes. Retained questions are taken
from their source as they are, never rewritten by a model: an assessment's question from its grading revision (the statement graders
read, with the printed number, its points marker and page footers set aside), a drafted quiz's from the LaTeX the instructor approved.
"""
from __future__ import annotations

import math
import re

KINDS = ('multiple_choice', 'free_response')
KIND_LABELS = {'multiple_choice': 'Multiple choice', 'free_response': 'Free response'}
LEVELS = ('chapter', 'section', 'topic', 'objective')
DIFFICULTIES = ('easy', 'medium', 'hard')
# The question types the instructor asked for besides the format ("Question type: multiple choice, free response, word problem, graph,
# etc."): what a question asks students to do. A question can be several at once.
TYPES = {
    'computation': ('Computation', 'Carry out a procedure or calculation and give the result.'),
    'word_problem': ('Word problem', 'A real-world situation described in words that students must translate into mathematics.'),
    'setup': ('Set up', 'Write the model, equations, inequalities or matrix for a situation without (or before) solving it.'),
    'graph': ('Graph', 'Draw, read or interpret a graph, region or diagram.'),
    'interpretation': ('Interpretation', 'Explain or interpret what a result means in context.'),
    'concept': ('Concept', 'A definition, property or true/false-style understanding question rather than a calculation.'),
}
LIMITS = {'number': 20, 'statement': 20000, 'part': 5000, 'choice': 2000, 'figure': 20000, 'answer': 2000, 'solution': 20000, 'notes': 5000,
          'answer_label': 60}
# A page footer the scanner's reading kept with a statement: "Version A -- Page 7 of 12", "Page 3 of 4".
FOOTER = re.compile(r'^\s*(?:version\s+[A-Z]\s*[-–—]+\s*)?page\s+\d+\s+of\s+\d+\s*$', re.IGNORECASE)


def _text(value, label, limit, blank=True):
    if value is None:
        value = ''
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{label} must be text of at most {limit} characters.')
    if not blank and not value.strip():
        raise ValueError(f'{label} is required.')
    return value.strip()


def _points(value, label='Points'):
    if value is None or value == '':
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise ValueError(f'{label} must be a number.') from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1000 or not math.isfinite(value):
        raise ValueError(f'{label} must be a number from 0 to 1000.')
    return int(value) if float(value).is_integer() else round(float(value), 4)


def _list(value, label, maximum):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{label} must be a list of at most {maximum}.')
    return value


def clean(value):
    """A bank question's content, checked and normalised: refuses anything a page could not show or a later reader could not trust."""
    if not isinstance(value, dict):
        raise ValueError('Send the question as an object.')
    kind = value.get('kind') or 'free_response'
    if kind not in KINDS:
        raise ValueError('A question is multiple choice or free response.')
    parts = []
    for part in _list(value.get('parts'), 'Parts', 20):
        if not isinstance(part, dict):
            raise ValueError('Each part is an object.')
        label = _text(part.get('label'), 'A part\'s label', 10).strip('() .').lower()
        parts.append({'label': label, 'text': _text(part.get('text'), 'A part\'s text', LIMITS['part']),
                      'points': _points(part.get('points'), 'A part\'s points')})
    choices = [_text(choice, 'A choice', LIMITS['choice'], False) for choice in _list(value.get('choices'), 'Choices', 12)]
    correct = _text(value.get('correct_choice'), 'The correct choice', 2).strip('() .').lower()
    if kind == 'multiple_choice':
        if len(choices) < 2:
            raise ValueError('A multiple-choice question has at least two choices.')
        if correct and (len(correct) != 1 or not 'a' <= correct < chr(ord('a') + len(choices))):
            raise ValueError('The correct choice is the letter of one of the choices.')
    else:
        choices, correct = [], ''
    answers = []
    for answer in _list(value.get('final_answers'), 'Final answers', 20):
        if not isinstance(answer, dict):
            raise ValueError('Each final answer is an object.')
        written = _text(answer.get('answer'), 'A final answer', LIMITS['answer'])
        if written:
            # The part it answers: a part's letter, or an answer key's own label ("corner points", "P", "C(x)"), kept as written.
            answers.append({'part': _text(answer.get('part'), 'A final answer\'s label', LIMITS['answer_label']), 'answer': written})
    return {
        'kind': kind,
        'number': _text(value.get('number'), 'The printed number', LIMITS['number']),
        'statement': _text(value.get('statement'), 'The statement', LIMITS['statement'], False),
        'parts': parts,
        'choices': choices,
        'correct_choice': correct,
        'figure': _text(value.get('figure'), 'The figure', LIMITS['figure']),
        'points': _points(value.get('points')),
        'final_answers': answers,
        'solution': _text(value.get('solution'), 'The solution', LIMITS['solution']),
        'notes': _text(value.get('notes'), 'Notes', LIMITS['notes']),
    }


# A thousands separator inside mathematics ("$\\$8,100$"): KaTeX spaces a bare comma as punctuation ("8, 100"), so a reading writes it {,}.
# A pair such as "(0,100)" is a point, not a number, and is left alone.
THOUSANDS = re.compile(r'(?<![\d(,.])(\d{1,3}(?:,\d{3})+)(?![\d),])')


def _escaped(text, index):
    slashes = 0
    while index - slashes - 1 >= 0 and text[index - slashes - 1] == '\\':
        slashes += 1
    return slashes % 2 == 1


def math_spans(text):
    """The text as the page's renderer splits it (static/grading-math.js `mathParts`): [(source, (opening, body, closing) or None)]."""
    delimiters = (('$$', '$$'), ('\\(', '\\)'), ('\\[', '\\]'), ('$', '$'))
    parts, start, i = [], 0, 0
    while i < len(text):
        pair = next((d for d in delimiters if text.startswith(d[0], i) and not _escaped(text, i)
                     and (d[0] != '$' or not (text[i + 1] if i + 1 < len(text) else ' ').isspace())), None)
        if not pair:
            i += 1
            continue
        end = i + len(pair[0])
        while end < len(text) and not (text.startswith(pair[1], end) and not _escaped(text, end) and (pair[1] != '$' or not text[end - 1].isspace())):
            end += 1
        if end == len(text):
            i += len(pair[0])
            continue
        if start < i:
            parts.append((text[start:i], None))
        parts.append((text[i:end + len(pair[1])], (pair[0], text[i + len(pair[0]):end], pair[1])))
        start = i = end + len(pair[1])
    if start < len(text):
        parts.append((text[start:], None))
    return parts


def _close_spans(text):
    """"$(x, y) = $": a span whose body ends (or starts) with a space is not mathematics to the page, which would show its dollar signs.
    The single dollar signs are paired in order, as LaTeX pairs them, and each body trimmed; left alone unless they pair evenly."""
    marks, i = [], 0
    while i < len(text):
        if text.startswith('$$', i) and not _escaped(text, i):
            close = text.find('$$', i + 2)
            if close < 0:
                return text
            i = close + 2
            continue
        if text[i] == '$' and not _escaped(text, i):
            marks.append(i)
        i += 1
    if len(marks) % 2:
        return text
    out, last = [], 0
    for opening, closing in zip(marks[::2], marks[1::2]):
        body = text[opening + 1:closing]
        out += [text[last:opening], '$', body.strip() or body, '$']
        last = closing + 1
    return ''.join(out) + text[last:]


def tidy_latex(text):
    """A reading's LaTeX as the page shows it: "$x = $" spans closed, thousands separators in mathematics written {,}."""
    out = []
    for source, math in math_spans(_close_spans(text)):
        out.append(source if math is None else math[0] + THOUSANDS.sub(lambda n: n.group(1).replace(',', '{,}'), math[1]) + math[2])
    return ''.join(out)


def tidied(content):
    """A cleaned reading with `tidy_latex` applied to every text it shows."""
    return {**content, 'statement': tidy_latex(content['statement']), 'solution': tidy_latex(content['solution']),
            'choices': [tidy_latex(choice) for choice in content['choices']],
            'parts': [{**part, 'text': tidy_latex(part['text'])} for part in content['parts']],
            'final_answers': [{**answer, 'answer': tidy_latex(answer['answer'])} for answer in content['final_answers']]}


def several_keyed(value, choices):
    """The letters a "select all that apply" key names ("d, e", "D and E", "de"), when it names two or more of the choices; else []."""
    text = str(value or '').strip().lower()
    letters = [t for t in re.split(r'[\s,;&/]+|\band\b', text) if t]
    if len(letters) == 1 and re.fullmatch(r'[a-z]{2,}', letters[0]):
        letters = list(letters[0])
    if len(letters) < 2 or any(len(t) != 1 or not 'a' <= t < chr(ord('a') + choices) for t in letters):
        return []
    return sorted(set(letters))


def tidy_statement(problem, number=''):
    """A statement as graders read it, without what belongs to its printing: the leading "18. (4 pts)" (the number and points are kept on
    their own) and page footers the scan's reading carried along."""
    lines = [line for line in str(problem or '').replace('\r\n', '\n').split('\n') if not FOOTER.match(line)]
    text = '\n'.join(lines).strip()
    if number:
        text = re.sub(rf'^\s*(?:problem|question)?\s*{re.escape(str(number))}\s*[.)]\s*(?:\(\s*\d+(?:\.\d+)?\s*(?:pts?|points?)\.?\s*\)\s*)?', '', text,
                      count=1, flags=re.IGNORECASE)
    return text.strip()


def from_frq(content):
    """An assessment's question, from its grading revision: the statement, the answer key as final answers, the worked solution."""
    number = str(content.get('printed_number') or '').strip()[:LIMITS['number']]
    answers = []
    for key in content.get('answer_key') or []:
        accepted = [str(a).strip() for a in key.get('accept') or [] if str(a).strip()]
        if accepted:
            answers.append({'part': str(key.get('label') or '').strip()[:LIMITS['answer_label']], 'answer': ' or '.join(accepted)[:LIMITS['answer']]})
    if not answers and str(content.get('expected_answer') or '').strip():
        answers.append({'part': '', 'answer': str(content['expected_answer']).strip()[:LIMITS['answer']]})
    solution = next((str(content.get(k) or '').strip() for k in ('worked_solution', 'instructor_solution', 'coordinator_solution')
                     if str(content.get(k) or '').strip()), '')
    points = content.get('max_points')
    return clean({'kind': 'free_response', 'number': number, 'statement': tidy_statement(content.get('problem'), number)[:LIMITS['statement']],
                  'points': points if isinstance(points, (int, float)) and not isinstance(points, bool) and 0 <= points <= 1000 else None,
                  'final_answers': answers, 'solution': solution[:LIMITS['solution']], 'notes': str(content.get('notes') or '').strip()[:LIMITS['notes']]})


def from_quiz(problem, letter):
    """A drafted quiz's problem, as approved, for one of its versions: the LaTeX fragments as they were printed."""
    content = problem['content'][letter]
    statement = content['statement'] + (f"\n\n{content['left']}" if content.get('left') else '')
    worth = {part['label']: part.get('points') for part in problem.get('parts') or []}
    return clean({'kind': problem['kind'], 'number': str(problem['number']), 'statement': statement[:LIMITS['statement']],
                  'parts': [{'label': part['label'], 'text': part['text'], 'points': worth.get(part['label'])} for part in content.get('parts') or []],
                  'choices': content.get('choices') or [], 'correct_choice': content.get('correct_choice') or '', 'figure': content.get('figure') or '',
                  'points': problem.get('points'), 'final_answers': content.get('final_answers') or [], 'solution': content.get('solution') or ''})


def plain(content):
    """The question as one block of text, for a model's request and for search."""
    lines = [content['statement']]
    lines += [f"({part['label']}) {part['text']}" + (f" [{part['points']} pts]" if part.get('points') is not None else '') for part in content['parts']]
    lines += [f"({chr(ord('a') + i)}) {choice}" for i, choice in enumerate(content['choices'])]
    return '\n'.join(line for line in lines if line)


SOLUTION_READ = 3000  # characters of a worked solution the tagger reads


def figures_described(content):
    """The descriptions of the figures a question relies on, from its notes' "Figure: ..." lines (a PDF's reading writes one per figure)."""
    return [line[len("Figure:"):].strip() for line in str(content.get("notes") or "").splitlines() if line.startswith("Figure:") and line[7:].strip()]


def tagger_reads(content):
    """What the tagging model reads of a question's content: a revision that leaves this unchanged (the correct choice, notes other than
    their "Figure:" lines, the printed number, the figure's source) keeps the model's tags. A question whose figure is only a picture
    (2026-10-03: a textbook's "write the inequality shown by the graph" exercises share one statement) is read with what its figures show."""
    reads = {"kind": KIND_LABELS[content["kind"]], "points": content["points"], "text": plain(content), "final_answers": content["final_answers"],
             "solution": content["solution"][:SOLUTION_READ]}
    figures = figures_described(content)
    return {**reads, "figures": figures} if figures else reads


def text_key(content):
    """A question's text as compared with the bank's other questions (2026-10-03, ADD ALL: a quiz's blank read again must not be added a
    second time): what students read (statement, parts and choices), with LaTeX spacing and wrappers, case and every mark set aside. None
    for a question that relies on a figure (two "write the inequality shown by the graph" problems share their words, not their graphs)
    or too short to tell apart."""
    if figures_described(content):
        return None
    text = re.sub(r'\\(?:left|right|displaystyle|mathrm|text|quad|qquad|[,;:!])', '', plain(content).lower())
    key = re.sub(r'[^a-z0-9]+', '', text)
    return key if len(key) >= 20 else None


# A section a PDF printed a problem under (bank_extract v2): its number ("2.1", "Section 2.1") or a heading such as "Chapter 1 Review".
SECTION_NUMBER = re.compile(r'(?:section\s*)?(\d+(?:\.\d+)+)', re.IGNORECASE)
CHAPTER_NUMBER = re.compile(r'\bchapter\s+(\d+)\b', re.IGNORECASE)


def section_label(section):
    """How a printed section is shown: "Section 2.1" for a number, otherwise its heading as printed ("Chapter 1 Review")."""
    section = " ".join(str(section or "").split())
    return f"Section {SECTION_NUMBER.fullmatch(section).group(1)}" if SECTION_NUMBER.fullmatch(section) else section


def printed_entry(section, outline):
    """The outline entry a printed section names: the section with that number, or for a chapter's heading ("Chapter 1 Review") the
    chapter; None when it names neither. `outline` lists entries with their level and number."""
    section = " ".join(str(section or "").split())
    number = SECTION_NUMBER.fullmatch(section)
    chapter = None if number else CHAPTER_NUMBER.search(section)
    level, wanted = ("section", number.group(1)) if number else ("chapter", chapter.group(1)) if chapter else (None, None)
    found = [node for node in outline if level and node["level"] == level and str(node.get("number") or "").strip() == wanted]
    return found[0]["id"] if len(found) == 1 else None


def norm(text):
    """A title compared across editions and requests: case, spacing and punctuation set aside."""
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').casefold()).strip()
