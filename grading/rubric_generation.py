"""Rubrics drafted by a model from a blank assignment (requirement, 2026-09-23): the alternative to uploading Gradescope CSVs.

Any assignment, not only exams (instructor, same day): a quiz, homework set, worksheet, lab or project handout is drafted the same
way. Problems labeled otherwise than by whole numbers are numbered by position and keep their printed heading as the title; points
need not be printed, and an open response is drafted with a complete-response line instead of a final answer.

The TERM grading plan owns a generation. It references the course-wide template it read and the printed document (and exam
version) its questions will describe. One request surveys the whole blank template for its printed problems; each problem's rubric is
then written in its own request, alone in its context, as the rubric check asks its questions. The result is a DRAFT: nothing
becomes a question or a rubric until the instructor accepts it, edited or not, and what they accept goes through the rubric
upload's own path (`create_draft_question`: retained source, draft revision, term pin, receipt). It is checked, clarified and
approved later in Key & Rubric Finalization exactly like an uploaded rubric. The model is the one that reviews rubrics (the
instructor's "Rubric drafting and review model", 2026-09-23); it must read images, because the template is read from its pages.

A generation can also ADAPT one version's rubrics to another version (requirement, 2026-09-25). The same model surveys the other version's blank
template, pairs its problems with the reference version's by what they ask (never by number alone: versions may reorder problems and
parts), then rewrites each reference rubric for its paired problem in its own request: the same lines with their IDs, points, rules and
conditions, only the version's values and answers changed. The result is a draft exactly like a drafted rubric, kept per question by
the instructor; what is kept keeps the reference rubric's structure (`adapted_rules`) and names the revision it was adapted from.
"""
import collections
import copy
import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from itertools import count

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from . import answers, config, locations, rubric_choices, rubrics
from .cloud import RetryLater, later
from .drafting_tools import definition_of, rubric_tools
from .models import digest, encoded, identifier, items, number, text, uid
from .providers.base import ProviderError
from .rubric_quality import LINK_KINDS, PARALLEL, says_no_credit

LOG = logging.getLogger(__name__)

# v4 (requirement, 2026-09-25): multiple-choice problems are pass/fail on the choice alone, never credit for work.
# v5 (2026-09-28): the writer settles what graders of earlier drafts stopped at (accepted forms, omissions, methods, whose labels,
# where the answer is, one mistake one line) and never writes a partial-credit line worth as much as its part's answer line.
RULE = 'rubric-draft-v5'
COMPILER = 'model-drafted-v1'
MAX_PAGES = 40
MAX_PROBLEMS = 60
# The survey transcribes every problem of the assignment, so it has room for a long homework set.
SURVEY_TOKENS = 32768
MAX_LINES = 30
IMAGES = 20 * 1024**2
# A drafted line's clarification: internal guidance for the graders beside the wording Gradescope shows (2026-09-28). On acceptance it
# becomes the line's comment, so its bound is a comment's.
CLARIFICATION = 2000
PAGE_TEXT = 8000
SURVEY = 'rubric_draft:survey'
WRITE = 'rubric_draft:q'
CREATED_BY = 'Drafted by a model from the blank assignment and accepted by the instructor. Check the lines, their points and which of them are alternatives in Key & Rubric Finalization before grading.'
# Adapting one version's rubrics to another version (requirement, 2026-09-25).
ADAPT_RULE = 'rubric-adapt-v1'
ADAPT_COMPILER = 'model-adapted-v1'
MATCH = 'rubric_adapt:match'
ADAPT = 'rubric_adapt:q'
ADAPTED_BY = "Adapted by a model from {source}'s rubric for the same problem and accepted by the instructor. Check the lines, their points and which of them are alternatives in Key & Rubric Finalization before grading."
PART = re.compile(r'\s*part\s+([a-z])\s*:', re.I)


def scoring_mode(value='positive'):
    if value not in ('positive', 'negative'):
        raise ValueError('Scoring must be positive or negative.')
    return value


def printed_points(value):
    """A printed point value, or None when none is printed. Zero is no value a question can be graded out of."""
    if value is None:
        return None
    return number(value, 'Printed points', 0, 1000) or None


def title(printed, label):
    """A question's title: its printed heading when that says more than its number ("Exercise 2.3"), otherwise "Question N"."""
    if label and not re.fullmatch(r'(?:(?:question|problem|q)\.?\s*)?#?\s*\d+\s*[.):]?', label, re.I):
        return label.rstrip(' .:')[:200]
    return f'Question {printed}'


def survey_problems(value, pages):
    """The printed problems a survey found, or ValueError: the survey is then asked once more."""
    if not isinstance(value, dict):
        raise ValueError('The survey returned an invalid object.')
    found, seen = [], set()
    for entry in items(value.get('problems'), 'Printed problems', MAX_PROBLEMS):
        if not isinstance(entry, dict):
            raise ValueError('Invalid printed problem.')
        printed = locations.printed_number(str(entry.get('number', '')).strip().rstrip('.'))
        if not printed or printed in seen:
            raise ValueError('Every printed problem needs its own whole number.')
        seen.add(printed)
        on = sorted({p for p in items(entry.get('pages'), 'Problem pages', 100) if type(p) is int and 1 <= p <= pages})
        if not on:
            raise ValueError(f'Problem {printed} names no page of this template.')
        parts, labels = [], set()
        for part in items(entry.get('parts'), 'Problem parts', 12):
            label = part.get('label', '').strip().strip('()').lower() if isinstance(part, dict) and isinstance(part.get('label'), str) else ''
            if not re.fullmatch(r'[a-z]', label) or label in labels:
                raise ValueError(f'Problem {printed} has a part without its own letter.')
            labels.add(label)
            parts.append({'label': label, 'points': printed_points(part.get('points'))})
        label = text(entry.get('label', ''), 'Printed heading', 200).strip()
        found.append({'number': printed, 'label': label, 'title': title(printed, label), 'points': printed_points(entry.get('points')), 'parts': parts, 'pages': on,
            'points_text': text(entry.get('points_text', ''), 'Printed points', 200).strip(),
            'statement': text(entry.get('statement', ''), 'Problem statement', 20000).strip()})
    if not found:
        raise ValueError('The survey found no printed problems on this template.')
    notes = [note.strip() for note in items(value.get('notes', []), 'Survey notes', 50) if isinstance(note, str) and note.strip()]
    return sorted(found, key=lambda p: int(p['number'])), [text(note, 'Survey note', 2000) for note in notes]


def draft_warnings(problem, total, lines, mode='positive'):
    """What the instructor should look at before accepting a draft. Findings only: nothing here changes a line or a point."""
    found = []
    if problem['points'] is None:
        found.append(f"No points are printed for this problem; the draft is written for {total:g}. Set the question's points.")
    parts = [p['points'] for p in problem['parts'] if p['points'] is not None]
    if problem['points'] is not None and len(parts) == len(problem['parts']) and parts and abs(sum(parts) - problem['points']) > 1e-9:
        found.append(f"The parts print {sum(parts):g} points, but the problem prints {problem['points']:g}.")
    if mode == 'negative':
        # A deducting rubric has no no-credit line (requirement, 2026-09-28): a full-credit line and its deductions, which normally add up
        # to the problem's points; alternatives may add up to more, and the score floors at 0.
        if not any(line['points'] == 0 for line in lines):
            found.append('No zero-deduction line for a complete and correct response.')
        if any(line['points'] < 0 and says_no_credit(line['description']) for line in lines):
            found.append('Negative scoring has no "No answer or completely incorrect" line: the deductions grade a blank or wholly wrong response. Remove it.')
        deducted = -sum(line['points'] for line in lines if line['points'] < 0)
        if deducted and deducted < total - 1e-9:
            found.append(f'The deductions add up to {deducted:g} of {total:g} points: a response with every one of them would keep {total - deducted:g}.')
        return found
    if not any(abs(line['points'] - total) < 1e-9 for line in lines):
        found.append('No line is worth the whole problem. Add a full-credit line if a correct answer with its work earns every point.')
    if not any(line['points'] == 0 and says_no_credit(line['description']) for line in lines):
        found.append('No zero line for a missing or completely incorrect answer.')
    if not problem['parts']:
        steps = sum(line['points'] for line in lines if 0 < line['points'] < total)
        if steps and abs(steps - total) > 1e-9:
            found.append(f'The partial-credit lines add up to {steps:g} of {total:g} points.')
    return found


def stated_parts(value):
    """{letter: points} the writer stated as its parts' worths (`parts`, GRADING "PART CAPS"); a malformed entry is left out, and its
    part's lines then stay uncapped unless the part prints its points."""
    stated = {}
    for entry in value.get('parts') if isinstance(value.get('parts'), list) else []:
        label = str(entry.get('part') or '').strip().strip('()').lower() if isinstance(entry, dict) else ''
        points = entry.get('points') if isinstance(entry, dict) else None
        if re.fullmatch(r'[a-h]', label) and label not in stated and isinstance(points, (int, float)) and not isinstance(points, bool) and points > 0:
            try:
                points = float(points)
            except OverflowError:
                continue
            if math.isfinite(points):
                stated[label] = round(points, 4)
    return stated


def uncapped_parts(parts, stated, total, descriptions, mode):
    """The draft warning for a deducting rubric whose part lines cannot be capped (GRADING "PART CAPS"), else None: exactly when
    accepting it would leave the rubric's own warning (`rubrics.capped_parts`)."""
    capped = rubrics.capped_parts({'mode': mode, 'items': [{'description': d} for d in descriptions]}, rubrics.part_worths(parts, stated), total)
    if rubrics.PARTS_UNCAPPED not in (capped.get('warnings') or []):
        return None
    return f"The parts' points are not all known or do not add up to {total:g}, so one part's deductions could take points another part earned."


def drafted_final_answers(raw):
    """A writer's final answers as [{part, answer}], one with no answer left out; ValueError when malformed (the draft is then asked once more)."""
    kept = [{'part': text(a.get('part', ''), 'Answer part', 20).strip().strip('()').lower(), 'answer': text(a.get('answer', ''), 'Final answer', 2000).strip()}
            for a in items(raw, 'Final answers', 20) if isinstance(a, dict)]
    return [a for a in kept if a['answer']]


def written_rubric(value, problem, mode='positive'):
    """One problem's drafted rubric, or ValueError: it is then asked once more."""
    if not isinstance(value, dict):
        raise ValueError('The rubric writer returned an invalid object.')
    mode = scoring_mode(mode)
    total = number(value.get('points'), 'Rubric total', 0, 1000)
    if not total:
        raise ValueError('The drafted rubric must be worth some points.')
    if problem['points'] is not None and abs(total - problem['points']) > 1e-9:
        raise ValueError(f"The rubric was written for {total:g} points, but problem {problem['number']} prints {problem['points']:g}.")
    lines = []
    for entry in items(value.get('items'), 'Rubric lines', MAX_LINES):
        if not isinstance(entry, dict):
            raise ValueError('Invalid rubric line.')
        lines.append({'description': text(entry.get('description'), 'Rubric line', 1000, False).strip(),
                      'points': round(number(entry.get('points'), 'Line points', -total if mode == 'negative' else 0, 0 if mode == 'negative' else total), 4),
                      'clarification': text(entry.get('clarification', ''), 'Clarification', CLARIFICATION).strip()})
    if len(lines) < 2:
        raise ValueError('A drafted rubric needs at least a full-credit line and one other line.')
    answers = drafted_final_answers(value.get('final_answers', []))
    stated = stated_parts(value)
    uncapped = uncapped_parts(problem['parts'], stated, total, [line['description'] for line in lines], mode)
    return {'mode': mode, 'points': total, 'items': lines, 'final_answers': answers,
            'solution': text(value.get('solution', ''), 'Solution', 20000).strip(), 'notes': text(value.get('notes', ''), 'Notes', 4000).strip(),
            'warnings': draft_warnings(problem, total, lines, mode) + ([uncapped] if uncapped else []), **({'part_points': stated} if stated else {})}


def accepted_lines(value, maximum, mode='positive'):
    """The rubric lines the instructor accepted, in the generation's saved scoring mode, IDs assigned in order."""
    mode = scoring_mode(mode)
    lines = []
    for entry in items(value, 'Rubric lines', MAX_LINES):
        if not isinstance(entry, dict):
            raise ValueError('Invalid rubric line.')
        description = text(entry.get('description', ''), 'Rubric line', 1000).strip()
        if description:
            lines.append({'id': f'R{len(lines) + 1}', 'description': description,
                          'points': number(entry.get('points'), 'Line points', -maximum if mode == 'negative' else 0, 0 if mode == 'negative' else maximum),
                          'clarification': text(entry.get('clarification') or '', 'Clarification', CLARIFICATION).strip()})
    if not lines:
        raise ValueError('Keep at least one rubric line.')
    return lines


def drafted_notes(lines):
    """Split the drafted clarifications off accepted lines (in place): {line ID: clarification} for the lines that have one. A
    clarification is never part of the rubric (department policy): it becomes the line's comment for the rubric review."""
    return {line['id']: note for line in lines if (note := line.pop('clarification', ''))}


def version_pairs(value, targets, references):
    """Which reference problem each problem of this version is a version of, or ValueError: the pairing is then asked once more.
    Every problem of this version once; a reference problem at most once; null where none asks the same task."""
    if not isinstance(value, dict):
        raise ValueError('The pairing returned an invalid object.')
    wanted, known = [p['number'] for p in targets], {r['number'] for r in references}
    pairs, used = {}, set()
    for entry in items(value.get('pairs'), 'Paired problems', MAX_PROBLEMS):
        if not isinstance(entry, dict):
            raise ValueError('Invalid pair.')
        target = locations.printed_number(str(entry.get('target', '')).strip().rstrip('.'))
        if target not in wanted or target in pairs:
            raise ValueError('Name every problem of this version once.')
        reference = entry.get('reference')
        if reference is not None:
            reference = locations.printed_number(str(reference).strip().rstrip('.'))
            if reference not in known:
                raise ValueError(f'Problem {target} is paired with a problem the reference version does not have.')
            if reference in used:
                raise ValueError(f'Problem {reference} of the reference version is paired twice.')
            used.add(reference)
        pairs[target] = {'reference': reference, 'reason': text(entry.get('reason', ''), 'Pairing reason', 2000).strip()}
    if set(pairs) != set(wanted):
        raise ValueError('Pair every problem of this version, with null where none fits.')
    notes = [note.strip() for note in items(value.get('notes', []), 'Pairing notes', 50) if isinstance(note, str) and note.strip()]
    return pairs, [text(note, 'Pairing note', 2000) for note in notes]


# What a line holds that may belong to one version only (GRADING "LEFTOVER VALUES"): its numbers, the single letters of its mathematics
# and each comma-separated piece of its mathematics, compared with the mathematics spelled one way.
SPELLINGS = (('\\left', ''), ('\\right', ''), ('\\dfrac', '\\frac'), ('\\geq', '\\ge'), ('\\leq', '\\le'), ('\\,', ''), ('\\ ', ''), ('\\!', ''), ('\\;', ''))
THOUSANDS = re.compile(r'(?<=\d),(?=\d{3}\b)|\{,\}')
NUMBER = re.compile(r'\d+(?:\.\d+)?')
LINE_ID = re.compile(r'\bR\d+\b')
LETTER = re.compile(r'(?<![\\A-Za-z])[A-Za-z](?![A-Za-z])')
WORDS = re.compile(r'\\(?:text|mathrm)\{[^}]*\}|\\[A-Za-z]+')
MATH = re.compile(r'\$\$(.+?)\$\$', re.S)
TOP_COMMA = re.compile(r',(?![^()\[\]{}]*[)\]}])')


def spelled(value):
    """Mathematics spelled one way, without spaces: the same value then compares equal however it was typed."""
    found = THOUSANDS.sub('', value or '')
    for written, plain in SPELLINGS:
        found = found.replace(written, plain)
    return re.sub(r'\s+', '', found)


def named_numbers(value):
    """The numbers a text names (0.20 and 0.2 are one), without the rubric line IDs it refers to."""
    return {n.rstrip('0').rstrip('.') if '.' in n else n for n in NUMBER.findall(LINE_ID.sub('', THOUSANDS.sub('', value or '')))}


def named_letters(value):
    """The single letters a text uses as symbols, without LaTeX commands or the words in \\text{}."""
    return set(LETTER.findall(WORDS.sub('', value or '')))


def math_pieces(value):
    """Each comma-separated piece of a line's mathematics, spelled one way: `$$x\\ge 0,\\ y\\ge 0$$` is two."""
    return [piece for span in MATH.findall(value or '') for piece in TOP_COMMA.split(spelled(span)) if piece]


def answer_forms(answers):
    """Every accepted form of a list of final answers, spelled one way."""
    return {spelled(form).replace('$$', '') for answer in answers for form in str(answer.get('answer', '')).split(' or ') if form.strip()}


def leftover_values(line, before, target, reference):
    """What a rubric line still says of the reference problem and this version's problem does not have (GRADING "LEFTOVER VALUES").

    `line` and `before` are the line and the reference line it came from: only what the adaptation left as it was can be left over, so a
    value it rewrote is never read. `target` and `reference` are each problem's `text` (its statement, worked solution and final answers)
    and `answers` (the final answers). A formula with no version's values in it, such as the union rule, names nothing to check, and
    a value printed on both versions stands. A whole expression is compared only on a line left word for word: a rewritten line that kept
    one piece (a matrix row whose value is the same) wrote the rest for this version. Returns the values, in the line's order."""
    target_text, reference_text = target['text'].replace('$$', ''), reference['text'].replace('$$', '')
    target_spelled, reference_spelled = spelled(target_text), spelled(reference_text)
    numbers, letters = named_numbers(target_text), named_letters(target_text)
    reference_answers = answer_forms(reference['answers'])
    said = ' '.join(answer_forms(target['answers']))
    answer_numbers = named_numbers(said)
    kept, held = set(math_pieces(before)), named_numbers(before.replace('$$', ''))
    found = []
    for piece in math_pieces(line):
        if piece not in kept:
            continue
        if piece in reference_answers and (named_numbers(piece) - answer_numbers if re.search(r'\d', piece) else piece not in said):
            found.append(piece)
        elif line == before and re.search(r'\d', piece) and piece not in target_spelled and piece in reference_spelled:
            found.append(piece)
        elif named_letters(piece) - letters:
            found.append(piece)
    found += sorted((named_numbers(line.replace('$$', '')) & held) - numbers - {n for piece in found for n in named_numbers(piece)})
    return found


def problem_values(statement, solution, answers):
    """A problem as `leftover_values` reads it."""
    return {'text': ' '.join([statement or '', solution or '', *(str(a.get('answer', '')) for a in answers)]), 'answers': answers}


def leftover_warning(problem, reference, lines, before, solution, answers, source):
    """The finding for the lines that still say something of `source`'s problem, naming each value, or None."""
    target = problem_values(problem.get('statement'), solution, answers)
    origin = problem_values(reference.get('statement'), reference.get('solution'), reference.get('final_answers') or [])
    named = [f"{line['id']} ({', '.join(left)})" for line in lines if (left := leftover_values(line['description'], before.get(line['id'], ''), target, origin))]
    if not named:
        return None
    return (f"{', '.join(named)} still {'names' if len(named) == 1 else 'name'} {source}'s values, which this version's problem and answers "
            f"do not have: check {'it' if len(named) == 1 else 'them'}.")


def adaptation_warnings(problem, reference, lines, source, solution='', answers=()):
    """What the instructor should look at before keeping an adapted rubric. Findings only: nothing here changes a line or a point."""
    found = []
    if problem['points'] is not None and abs(problem['points'] - reference['points']) > 1e-9:
        found.append(f"This version prints {problem['points']:g} points for the problem; {source}'s rubric is worth {reference['points']:g}. Check the points before creating it.")
    letters = {m.group(1).lower() for line in reference['rubric']['items'] if (m := PART.match(line['description']))}
    if letters and problem['parts'] and len(letters) != len(problem['parts']):
        found.append(f"This version prints {len(problem['parts'])} lettered parts; {source}'s rubric has lines for {len(letters)}.")
    left = leftover_warning(problem, reference, lines, {line['id']: line['description'] for line in reference['rubric']['items']}, solution, answers, source)
    if left:
        found.append(left)
    return found


def adapted_rubric(value, reference, problem, source):
    """One reference rubric adapted to its paired problem, or ValueError: it is then asked once more. Every reference line once, by
    its ID; the points are the reference line's, never the model's."""
    if not isinstance(value, dict):
        raise ValueError('The adaptation returned an invalid object.')
    originals = {line['id']: line for line in reference['rubric']['items']}
    lines = []
    for entry in items(value.get('items'), 'Rubric lines', max(MAX_LINES, len(originals))):
        if not isinstance(entry, dict) or entry.get('id') not in originals or any(entry['id'] == line['id'] for line in lines):
            raise ValueError('Adapt every line of the reference rubric once, by its ID.')
        lines.append({'id': entry['id'], 'description': text(entry.get('description'), 'Rubric line', 4000, False).strip(), 'points': originals[entry['id']]['points']})
    missing = [iid for iid in originals if not any(line['id'] == iid for line in lines)]
    if missing:
        raise ValueError(f"The adaptation left out {', '.join(missing)}.")
    answers = drafted_final_answers(value.get('final_answers', []))
    solution = text(value.get('solution', ''), 'Solution', 20000).strip()
    return {'mode': reference['rubric']['mode'], 'points': reference['points'], 'items': lines, 'final_answers': answers,
            'solution': solution, 'notes': text(value.get('notes', ''), 'Notes', 4000).strip(),
            'warnings': adaptation_warnings(problem, reference, lines, source, solution, answers),
            'reference_items': [{k: line[k] for k in ('id', 'description', 'points') if k in line} | ({'comment': line['instructor_comment']} if line.get('instructor_comment') else {})
                                for line in reference['rubric']['items']]}


def adapted_rules(source, accepted, maximum, source_maximum, warnings, worths=None):
    """The reference rubric's structure with the wording and points the instructor accepted for this version.

    A line that names its reference line keeps that line's ID, group, follow-through, dependencies, exclusions and conditions, so both
    versions are graded by the same rules; the importer's own mark (`exceptions` equal to the wording) follows the new wording, as an
    edited line's does. A line the instructor cleared is dropped with every reference to it; a line they added is new, read by the
    ordinary wording rules. A structured condition that no longer validates goes back to review, with a warning. With `worths` (this
    version's parts, GRADING "PART CAPS") its parts are capped at them; without (a makeup) the reference's own caps are kept."""
    negative = source['mode'] == 'negative'
    originals = {item['id']: item for item in source['items']}
    fresh = (f'N{n}' for n in count(1) if f'N{n}' not in originals)
    rows, kept = [], set()
    for entry in items(accepted, 'Rubric lines', max(MAX_LINES, len(originals) + MAX_LINES)):
        if not isinstance(entry, dict):
            raise ValueError('Invalid rubric line.')
        description = text(entry.get('description', ''), 'Rubric line', 4000).strip()
        if not description:
            continue
        points = number(entry.get('points'), 'Line points', -maximum if negative else 0, 0 if negative else maximum)
        sid = entry.get('source') or ''
        if sid:
            if not isinstance(sid, str) or sid not in originals or sid in kept:
                raise ValueError('Each adapted line must name one line of the reference rubric once.')
            kept.add(sid)
            item = copy.deepcopy(originals[sid])
            if (item.get('exceptions') or '').strip() == item['description'].strip():
                item['exceptions'] = description
            item.update(description=description, points=points, original_source_text=description)
        else:
            item = {'id': next(fresh), 'description': description, 'points': points}
            rubrics.wording_rules([item], warnings)
        rows.append(item)
    if not rows:
        raise ValueError('Keep at least one rubric line.')
    dropped = set(originals) - kept
    for item in rows:
        links = {key: [i for i in item.get(key, []) if i not in dropped] for key in ('requires', 'excludes')}
        changed = links != {key: item.get(key, []) for key in ('requires', 'excludes')}
        item.update(links)
        if item.get('condition_handling') == 'structured' and (changed or not rubrics.conditional(item)
                                                               or not (item['requires'] or item['excludes'] or not item.get('stackable', True)
                                                                       or (item.get('group') and not rubrics.part_cap(item.get('group'))))):
            item.pop('condition_handling')
            warnings.append(f"{item['id']}: the structured condition carried from the reference rubric needs your confirmation again.")
    ceiling = maximum if abs(source['ceiling'] - source_maximum) < 1e-9 else min(source['ceiling'], maximum)
    groups = [{**g, 'floor': max(g['floor'], -maximum), 'ceiling': min(g['ceiling'], maximum)} for g in source.get('groups', [])]
    for group in groups:
        group['floor'] = min(group['floor'], group['ceiling'])
    value = {'mode': source['mode'], 'floor': min(source['floor'], ceiling), 'ceiling': ceiling, 'items': rows, 'groups': groups,
             'notes': source.get('notes', ''), 'warnings': warnings}
    if worths is not None and (worths or not any(rubrics.part_cap(g['id']) for g in groups)):
        value = rubrics.capped_parts(value, worths, maximum)
    return rubrics.validate(value, maximum)


# Every version drafted at once (requirement, 2026-09-25). Each version's template is surveyed as a
# drafted one is, ONE request groups each problem with its copies on the other versions, and one request per problem writes ONE list
# of lines for all its versions: every line's points are shared and its wording is given per version, so the versions cannot differ
# in the kinds of lines they use. One generation row covers the plan (exam_version ALL); its input names every version's template.
VERSIONS_RULE = 'rubric-draft-versions-v3'  # v2: multiple choice is pass/fail on the choice alone (requirement, 2026-09-25); v3: as RULE v5
VERSIONS_COMPILER = 'model-drafted-versions-v1'
# A drafted rubric given to a question that ALREADY exists, in place of its rubric (requirement, 2026-09-27: every question of a plan
# existed, so a regenerated draft could not be used at all). The draft's own record is kept under `based_on`, so the rubric check
# still finds the problem it was drafted with (`rubric_quality.origins`), and the draft never lists it as a question it created.
REPLACE_COMPILER = 'draft-replaced-v1'
ALL = '*'
SURVEY_EACH = 'rubric_draft:versions:survey:'
GROUP = 'rubric_draft:versions:group'
WRITE_ALL = 'rubric_draft:versions:problem:'
ALL_PAGES = 120
WRITE_IMAGES = 12
CREATED_TOGETHER = ("Drafted by a model for every version at once from the blank assignments and accepted by the instructor; its lines match "
                    "the other versions' line for line. Check the lines, their points and which of them are alternatives in Key & Rubric Finalization before grading.")


def version_groups(value, surveyed):
    """Each problem with its copies on the other versions, or ValueError: the grouping is then asked once more. `surveyed` maps each
    version's name to its surveyed problems. Every problem of every version exactly once; at most one of each version per group."""
    if not isinstance(value, dict):
        raise ValueError('The grouping returned an invalid object.')
    order = list(surveyed)
    known = {(name, problem['number']): problem for name, problems in surveyed.items() for problem in problems}
    groups, seen = [], set()
    for group in items(value.get('groups'), 'Problem groups', max(1, len(known))):
        if not isinstance(group, dict):
            raise ValueError('Invalid problem group.')
        members, named = [], set()
        for member in items(group.get('members'), 'Group members', len(order)):
            if not isinstance(member, dict):
                raise ValueError('Invalid group member.')
            name = member.get('version')
            number = locations.printed_number(str(member.get('number', '')).strip().rstrip('.'))
            if (name, number) not in known or (name, number) in seen or name in named:
                raise ValueError('Group each problem of each version exactly once, with at most one problem of each version per group.')
            seen.add((name, number))
            named.add(name)
            members.append({'version': name, **known[(name, number)]})
        if not members:
            raise ValueError('A problem group has no problem.')
        groups.append(sorted(members, key=lambda m: order.index(m['version'])))
    missing = [f'{name} {number}' for name, number in known if (name, number) not in seen]
    if missing:
        raise ValueError(f"The grouping left out {', '.join(missing[:5])}.")
    groups.sort(key=lambda members: (order.index(members[0]['version']), int(members[0]['number'])))
    notes = [note.strip() for note in items(value.get('notes', []), 'Grouping notes', 50) if isinstance(note, str) and note.strip()]
    return [{'id': f'P{index}', 'versions': members} for index, members in enumerate(groups, 1)], [text(note, 'Grouping note', 2000) for note in notes]


def line_kinds(lines):
    """{line ID: 'same' | 'related'} of a family rubric's shared lines (R1.. by position), for the created rubric's provenance: where
    the links between the versions' lines start (`rubric_links`), until the rubric reviewer or the instructor decides otherwise."""
    return {f'R{index}': line['link'] for index, line in enumerate(lines, 1) if line.get('link') in LINK_KINDS}


def family_rubric(value, family, mode, names):
    """One problem's rubric for all its versions, or ValueError: it is then asked once more. One list of lines: each line's points
    are shared and it has exactly one wording per version. `names` are every version drafted, to say which one lacks the problem."""
    if not isinstance(value, dict):
        raise ValueError('The rubric writer returned an invalid object.')
    mode = scoring_mode(mode)
    negative = mode == 'negative'
    members = [m['version'] for m in family['versions']]
    total = number(value.get('points'), 'Rubric total', 0, 1000)
    if not total:
        raise ValueError('The drafted rubric must be worth some points.')
    printed = {m['points'] for m in family['versions'] if m['points'] is not None}
    if len(printed) == 1 and abs(total - next(iter(printed))) > 1e-9:
        raise ValueError(f"The rubric was written for {total:g} points, but the problem prints {next(iter(printed)):g}.")
    lines = []
    for entry in items(value.get('items'), 'Rubric lines', MAX_LINES):
        if not isinstance(entry, dict):
            raise ValueError('Invalid rubric line.')
        points = round(number(entry.get('points'), 'Line points', -total if negative else 0, 0 if negative else total), 4)
        wording, notes = {}, {}
        for said in items(entry.get('descriptions'), 'Line wordings', len(members)):
            if not isinstance(said, dict) or said.get('version') not in members or said['version'] in wording:
                raise ValueError('Give each line one wording for each version.')
            wording[said['version']] = text(said.get('description'), 'Rubric line', 1000, False).strip()
            notes[said['version']] = text(said.get('clarification', ''), 'Clarification', CLARIFICATION).strip()
        if set(wording) != set(members):
            raise ValueError('Every line needs its wording on every version.')
        # LINKED VERSIONS (2026-10-02): whether the line is the same on every version or the same step with another outcome. An
        # older writer gives none; the line is then linked only by its shared ID, unverified.
        link = entry.get('link') if entry.get('link') in LINK_KINDS else None
        lines.append({'points': points, 'descriptions': wording, 'clarifications': notes, **({'link': link} if link else {})})
    if len(lines) < 2:
        raise ValueError('A drafted rubric needs at least a full-credit line and one other line.')
    solutions = {}
    for said in items(value.get('solutions', []), 'Solutions', len(members)):
        if isinstance(said, dict) and said.get('version') in members and said['version'] not in solutions:
            answers = drafted_final_answers(said.get('final_answers', []))
            solutions[said['version']] = {'solution': text(said.get('solution', ''), 'Solution', 20000).strip(), 'final_answers': answers}
    stated = stated_parts(value)

    def member_warnings(member):
        wording = [{'description': line['descriptions'][member['version']], 'points': line['points']} for line in lines]
        uncapped = uncapped_parts(member.get('parts'), stated, total, [w['description'] for w in wording], mode)
        return draft_warnings(member, total, wording, mode) + ([uncapped] if uncapped else [])
    versions = [{**member, **solutions.get(member['version'], {'solution': '', 'final_answers': []}), 'warnings': member_warnings(member)}
                for member in family['versions']]
    warnings = [f"{', '.join(n for n in names if n not in members)} {'does' if len(names) - len(members) == 1 else 'do'} not print this problem."] if len(members) < len(names) else []
    if len(printed) > 1:
        warnings.append(f"The versions print different points for this problem ({', '.join(f'{p:g}' for p in sorted(printed))}); the draft is written for {total:g}.")
    return {'id': family['id'], 'mode': mode, 'points': total, 'items': lines, 'versions': versions,
            'notes': text(value.get('notes', ''), 'Notes', 4000).strip(), 'warnings': warnings, **({'part_points': stated} if stated else {})}


# ANSWER KEYS READ FROM THE BLANK EXAM (requirement, 2026-09-25). Where
# nothing can be read from a rubric's full-credit line (`answers.key_from_rubric`: a matrix written after `M=` in words), the rubric
# drafting and review model works the key out from the question's printed problem (its text read from the blank exam and a picture of
# it) and the rubric lines, one request per question. Each reading is a generation of kind `key`, owned by the term plan like a draft
# and read from that question's version's template; it is kept out of the drafts. It creates and saves nothing: the key is shown on
# the question as "not saved yet", and only Save this key or Confirm all saves it, through the ordinary answer-key save.
# v2 (requirement, 2026-10-11): a reading is also sent the question's worked solution where it has one, and a quiz or
# makeup drafted by a model has each problem's key read ONCE while it is drafted, with the writer's solution, final answers and
# correct choice (`quiz_drafts._quiz_keys`: the same prompt, schema and validator, owned by the draft's revision), shown on the draft
# and saved when the instructor approves it. The rule names what a reading was sent; a reading recorded under v1 reads as before.
KEY_RULE = 'answer-key-read-v2'
KEY_STEP = 'answer_key:read'
KEY_KIND = 'key'

# ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11, chosen from a measured list: on the paper path they read the same drafted
# rubric three times, to accept the draft, to press Confirm all for rubric review and to confirm the finalization, with Run rubric
# check and the key's reading each a click of their own between). Three reads become two: accepting a draft is the confirmation
# for the rubric review of the questions it creates (`_after_acceptance`, recorded as READIED); each one's answer key is read
# without being asked once its answer area is confirmed (`_read_keys_of_confirmed_areas`: the ORDINARY reading, with the picture of
# the confirmed area, the problem's text and the worked solution, so the proposal is no worse than the button's; still a proposal,
# used only once the instructor saves it); and the rubric check is asked for by itself once the plan is ready for it
# (`request_rubric_review_when_ready`). Uploaded CSVs keep the explicit confirmation, the button and Run rubric check; a quiz's or
# a makeup's approval keeps its own confirmation and check request (`quiz_drafts`, `makeup_drafts`).
READIED = 'draft-acceptance'
READY_REQUEST = 'draft-review'
KEYS_REQUEST = 'area-confirmed'
YOURS_TO_RUN = 'The rubric check of these rubrics is started in Key & Rubric Finalization.'


def read_key(value):
    """The model's answer key for one question, or ValueError: its parts as `answers.clean_key` keeps them, each with how it was found,
    or no parts and the reason nothing could be read."""
    if not isinstance(value, dict):
        raise ValueError('The answer key reading is not an object.')
    rows = [row for row in items(value.get('parts'), 'Answer key parts', 12)]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('Each answer key part must be an object.')
    key = answers.clean_key([{'label': row.get('label', ''), 'accept': row.get('accept', [])} for row in rows])
    # ONE PART, SEVERAL VALUES (2026-10-29): the prompt gives each value of a part its own entry, labelled by the part's letter (""
    # for a single answer), so "How many ... ? What is the maximum revenue?" comes back as two parts labelled "b". Refusing that
    # left Quiz 6's Problem 4 without a key on Versions B, C and D (both attempts, each version, answered a, b, b, c) and the Quiz 2
    # makeup's Problem 3 without one on Version E ("", ""). A label repeated with different answers is numbered in order ("b (1)",
    # "b (2)"; "(1)", "(2)"); the same label with the same answers twice is still refused.
    repeated = collections.Counter(part['label'].casefold() for part in key)
    if all(len({tuple(p['accept']) for p in key if p['label'].casefold() == label}) == times for label, times in repeated.items() if times > 1):
        seen = collections.Counter()
        for part in key:
            if repeated[part['label'].casefold()] > 1:
                seen[part['label'].casefold()] += 1
                part['label'] = f"{part['label']} ({seen[part['label'].casefold()]})".strip()
    labels = [part['label'].casefold() for part in key]
    if len(set(labels)) != len(labels) or (len(key) > 1 and '' in labels):
        raise ValueError('Label each part of the answer key once.')
    unreadable = text(value.get('unreadable', ''), 'Why the key cannot be read', 2000).strip()
    if not key and not unreadable:
        raise ValueError('Give the answer key, or say why it cannot be read from the problem.')
    return {'key': key, 'basis': [text(row.get('basis', ''), 'How the answer was found', 1000).strip() for row in rows],
            'unreadable': '' if key else unreadable}


def key_request(question, lines, full, problem_text, pictures, *, worked_solution='', author_answers=(), correct_choice=''):
    """What one answer-key reading is sent (`answer_key_read`), the plan's own (`_read_answer_key`) and a drafted quiz's
    (`quiz_drafts._quiz_keys`) alike: the question (title, printed number, maximum points), its rubric lines ({id, points,
    description}) and which of them is worth the whole question, the problem's text and how many pictures of it are attached; and,
    only where there is one (v2), the worked solution, the drafting model's final answers ({part, answer}) and its correct choice."""
    return {'question': question, 'rubric_lines': lines, 'full_credit_line': full, 'problem_text': problem_text, 'pictures': pictures,
            **({'worked_solution': worked_solution} if worked_solution else {}),
            **({'author_answers': list(author_answers)} if author_answers else {}),
            **({'correct_choice': correct_choice} if correct_choice else {})}


def key_stands(answer):
    """Where a question's answer key stands, from what the plan read says of it (`answer`: its saved key, the key its rubric
    proposes, its latest reading): 'saved'; 'unsaved', a key its rubric proposes, or its latest reading found under the current
    rubric, waits for the instructor's save; 'unread', none of these and never read; else 'none', a reading that is waiting or
    running, or that left no key (unreadable, failed, cancelled, of an earlier rubric)."""
    read = answer.get('read_key') or {}
    if answer['key']:
        return 'saved'
    if answer['proposed_key'] or (read.get('status') == 'complete' and read.get('current') and read.get('key')):
        return 'unsaved'
    return 'none' if read else 'unread'


class RubricGenerationMixin:
    # Status --------------------------------------------------------------------------------------------------------------------
    def rubric_generation_summary(self, db, row, *, details=False):
        state = 'cancelled' if row['cancelled'] else row['status']
        profile = json.loads(row['profile_json'])
        context = json.loads(row['input_json'])
        result = {k: row[k] for k in ('id', 'document_id', 'exam_version', 'template_id', 'error', 'created_at', 'updated_at')}
        adapting, together = context.get('kind') == 'adapt', context.get('kind') == 'versions'
        # An adaptation keeps each reference rubric's own scoring, so its questions may differ.
        modes = {q['problem']['rubric']['mode'] for q in context['source']['questions']} if adapting else {context.get('mode', 'positive')}
        result.update(key=row['document_id'] + ':' + row['exam_version'], status=state, model=profile['endpoint']['name'],
                      mode=next(iter(modes)) if len(modes) == 1 else 'mixed', kind='adapt' if adapting else 'versions' if together else 'draft')
        if adapting:
            result['source'] = {'key': context['source']['key'], 'label': context['source']['label']}
        if together:
            result['versions'] = [{k: v[k] for k in ('name', 'key', 'label', 'template_id', 'template_label')} for v in context['versions']]
        created =[dict(r) for r in db.execute("""SELECT r.question_id,json_extract(r.compiler_json,'$.problem') problem FROM frq_rubric_revisions r
            JOIN frq_assignment_questions p ON p.question_id=r.question_id AND p.assignment_id=?
            WHERE json_extract(r.compiler_json,'$.generation_id')=?""", (row['assignment_id'], row['id']))]
        # A printed number, or a problem and version ("P1:Version A") of a generation that drafted every version at once.
        result['created'] = sorted({c['problem'] for c in created if c['problem']}, key=lambda problem: (len(problem), problem))
        if state in ('pending', 'running') and together:
            surveyed = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE rubric_generation_id=? AND step LIKE ? AND state='completed'",
                                  (row['id'], SURVEY_EACH + '%')).fetchone()[0]
            grouped = db.execute("SELECT receipt_json FROM frq_cloud_calls WHERE rubric_generation_id=? AND step=? AND state='completed' ORDER BY rowid DESC LIMIT 1",
                                 (row['id'], GROUP)).fetchone()
            groups = json.loads(grouped[0] or '{}').get('result', {}).get('output', {}).get('groups') if grouped else None
            written = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE rubric_generation_id=? AND step LIKE ? AND state='completed'",
                                 (row['id'], WRITE_ALL + '%')).fetchone()[0]
            # A single version needs no grouping: its problems are known once it is surveyed.
            single = len(context['versions']) == 1 and surveyed
            survey = db.execute("SELECT receipt_json FROM frq_cloud_calls WHERE rubric_generation_id=? AND step=? AND state='completed' ORDER BY rowid DESC LIMIT 1",
                                (row['id'], SURVEY_EACH + '0')).fetchone() if single else None
            asked = json.loads(survey[0] or '{}').get('result', {}).get('output', {}).get('problems') if survey else None
            total = len(groups) if isinstance(groups, list) else len(asked) if isinstance(asked, list) else None
            result['progress'] = {'surveyed': surveyed, 'versions': len(context['versions']), 'grouped': bool(grouped) or bool(single),
                                  'completed': written, 'total': total}
        elif state in ('pending', 'running'):
            survey = db.execute("SELECT receipt_json FROM frq_cloud_calls WHERE rubric_generation_id=? AND step=? AND state='completed' ORDER BY rowid DESC LIMIT 1",
                                (row['id'], SURVEY)).fetchone()
            asked = json.loads(survey[0] or '{}').get('result', {}).get('output', {}).get('problems') if survey else None
            written = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE rubric_generation_id=? AND step LIKE ? AND state='completed'",
                                 (row['id'], (ADAPT if adapting else WRITE) + '%')).fetchone()[0]
            result['progress'] = {'surveyed': bool(survey), 'completed': written, 'total': len(asked) if isinstance(asked, list) else None}
            if adapting:
                # One request is written per PAIRED problem: until the pairing is answered the total is unknown.
                paired = db.execute("SELECT receipt_json FROM frq_cloud_calls WHERE rubric_generation_id=? AND step=? AND state='completed' ORDER BY rowid DESC LIMIT 1",
                                    (row['id'], MATCH)).fetchone()
                pairs = json.loads(paired[0] or '{}').get('result', {}).get('output', {}).get('pairs') if paired else None
                result['progress'].update(paired=bool(paired), total=sum(1 for p in pairs if isinstance(p, dict) and p.get('reference')) if isinstance(pairs, list) else None)
        if row['status'] == 'complete' and row['result_json']:
            drafted = json.loads(row['result_json'])
            result['counts'] = {'drafted': len(drafted['problems'] if together else drafted['questions']), 'failed': len(drafted['failed'])}
            if details:
                result['result'] = drafted
                if adapting:
                    # A question whose reference rubric changed since it was adapted is no longer the adaptation of what is there now.
                    current = dict(db.execute('SELECT question_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=?', (row['assignment_id'],)).fetchall())
                    result['stale'] = [q['number'] for q in drafted['questions'] if current.get(q['source']['question_id']) != q['source']['rubric_id']]
        return result

    def rubric_generations(self, db, aid):
        """The latest generation of each version, for the Rubrics & Templates page."""
        latest = {}
        for row in db.execute("SELECT * FROM frq_rubric_generations WHERE assignment_id=? AND COALESCE(json_extract(input_json,'$.kind'),'')!=? ORDER BY rowid", (aid, KEY_KIND)):
            latest[(row['document_id'], row['exam_version'])] = row
        return [self.rubric_generation_summary(db, row) for row in latest.values()]

    def rubric_generation(self, aid, gid):
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            row = db.execute('SELECT * FROM frq_rubric_generations WHERE id=? AND assignment_id=?', (gid, aid)).fetchone()
            if not row:
                raise NotFound('Rubric draft not found.')
            return self.rubric_generation_summary(db, row, details=True)

    # Request -------------------------------------------------------------------------------------------------------------------
    def _drafting_model(self):
        settings = self.settings()['settings']
        endpoint = config.rubric_reviewer(settings)
        if not endpoint:
            raise ValueError('Choose a Rubric drafting and review model in Compute settings first.')
        if endpoint['modality'] != 'vision':
            raise ValueError(f"{endpoint['name']} cannot read images. Drafting reads the template's pages: choose an image-capable Rubric drafting and review model in Compute settings.")
        return {**endpoint, 'max_tokens': max(endpoint['max_tokens'], 16384)}, settings

    @staticmethod
    def _template_slot(setup, key, template_id, what):
        slot = next((s for s in setup['slots'] if s['key'] == key), None)
        if not slot:
            raise ValueError('Choose the version whose blank template the model should read.')
        template = next((t for t in slot['options'] if t['id'] == template_id), None)
        if not template:
            raise ValueError(f"Choose or upload a blank template for {slot['label']} first.")
        if template['page_count'] > MAX_PAGES:
            raise ValueError(f'A template of more than {MAX_PAGES} pages is too long to {what} in one go.')
        return slot, template

    def _record_generation(self, db, aid, request_id, checksum, slot, template, context, endpoint, settings, event, detail):
        running = db.execute("SELECT document_id,exam_version FROM frq_rubric_generations WHERE assignment_id=? AND status IN ('pending','running') AND COALESCE(json_extract(input_json,'$.kind'),'')!=?",
                             (aid, KEY_KIND)).fetchall()
        if any(r['exam_version'] == ALL for r in running) or (slot['exam_version'] == ALL and running):
            raise Conflict('Rubrics of this assignment are already being drafted. Wait for that draft, or cancel it.')
        if any((r['document_id'], r['exam_version']) == (slot['document_id'], slot['exam_version']) for r in running):
            raise Conflict(f"Rubrics for {slot['label']} are already being drafted.")
        if db.execute("SELECT COUNT(*) FROM frq_rubric_generations WHERE status IN ('pending','running')").fetchone()[0] >= 20:
            raise Conflict('Too many rubric drafts are waiting.')
        gid, now = uid(), timestamp()
        db.execute('INSERT INTO frq_rubric_generations(id,assignment_id,request_id,request_sha256,document_id,exam_version,template_id,input_json,profile_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,\'pending\',?,?)',
            (gid, aid, request_id, checksum, slot['document_id'], slot['exam_version'], template['id'], encoded(context),
             encoded({'endpoint': endpoint, 'settings': settings}), now, now))
        self.event(db, aid, event, {'generation_id': gid, 'template_id': template['id'], 'document_id': slot['document_id'],
            'exam_version': slot['exam_version'], 'model': endpoint['name'], **detail})
        return self.rubric_generation_summary(db, db.execute('SELECT * FROM frq_rubric_generations WHERE id=?', (gid,)).fetchone())

    def queue_rubric_generation(self, aid, data):
        if data.get('adapt_from'):
            return self.queue_rubric_adaptation(aid, data)
        if data.get('all_versions'):
            return self.queue_rubric_versions(aid, data)
        request_id = text(data.get('request_id'), 'Request ID', 128, False)
        mode = scoring_mode(data.get('mode', 'positive'))
        choices = rubric_choices.clean(data.get('rubric_choices'))
        with self.rosters.lock(), connect(self.path) as db:
            setup = self.template_setup(db, aid)
            slot, template = self._template_slot(setup, data.get('key'), data.get('template_id'), 'draft rubrics from')
            endpoint, settings = self._drafting_model()
            pages = template['page_count']
            # The mode belongs to this term-owned generation, not the shared template or assignment.
            context = {'rule': RULE, 'mode': mode, 'document_id': slot['document_id'], 'exam_version': slot['exam_version'], 'label': slot['label'],
                       'template_id': template['id'], 'manifest_sha256': template['manifest_sha256'], 'template_label': template['label'], 'pages': pages}
            if choices:  # RUBRIC CHOICES (2026-10-24), as for every version at once
                context['rubric_choices'] = choices
            checksum = digest([aid, context, endpoint])
            prior = db.execute('SELECT * FROM frq_rubric_generations WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                old = json.loads(prior['input_json'])
                legacy = {**context, 'rule': old['rule']}
                legacy.pop('mode')
                same_legacy = ('mode' not in old and mode == 'positive' and prior['request_sha256'] == digest([aid, legacy, endpoint]))
                if prior['request_sha256'] != checksum and not same_legacy:
                    raise Conflict('This request ID already belongs to another rubric draft.')
                return self.rubric_generation_summary(db, prior)
            if 'rubric_choices' in data and rubric_choices.record(db, aid, choices, 'workspace'):
                self.event(db, aid, 'rubric_choices_saved', {'choices': choices})
            summary = self._record_generation(db, aid, request_id, checksum, slot, template, context, endpoint, settings, 'rubric_generation_queued', {'mode': mode})
        self.inbox.wake.set()
        return summary

    def queue_answer_key_reading(self, aid, data):
        """Read the answer key of each listed question from its blank exam (see `KEY_RULE`): only questions without a saved key whose
        rubric gives none to read, each with its confirmed template and printed problem, and its stored worked solution where it has
        one (v2). One generation per question; the same request again returns the same readings."""
        request_id = identifier(data.get('request_id'))
        wanted = items(data.get('question_ids'), 'Questions to read', 100)
        if any(not isinstance(qid, str) for qid in wanted):
            raise ValueError('Choose valid questions whose answer keys should be read.')
        if not wanted:
            raise ValueError('Choose the questions whose answer keys should be read from the blank assignment.')
        self._queue_key_readings(aid, request_id, wanted)
        return self.assignment(aid)

    def _queue_key_readings(self, aid, request_id, wanted, *, actor='workspace'):
        """The readings of `queue_answer_key_reading`, with its conditions and refusals, returning the questions queued: the route
        answers with the whole plan, and the reading that follows a confirmed answer area by itself (`_read_keys_of_confirmed_areas`,
        `actor` then `automatic`) needs neither the plan nor a refusal that fails the confirmation."""
        settings = self.settings()['settings']
        endpoint = config.rubric_reviewer(settings)
        if not endpoint:
            raise ValueError('Choose a Rubric drafting and review model in Compute settings first.')
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            reference = self.rubric_reference(db, aid)
            readings = self.answer_key_readings(db, aid)
            db.execute('BEGIN IMMEDIATE')
            queued = []
            for qid in dict.fromkeys(wanted):
                q = self.question(db, aid, qid)
                content, rubric = q['content'], q['rubric']
                label = (f"{content['title']} · Version {q['exam_version']}" if q['exam_version'] else content['title'])
                if not rubric:
                    raise Conflict(f'{label} has no rubric yet.')
                if content.get('answer_key') or answers.key_from_rubric(rubric['rules']):
                    raise Conflict(f'{label} already has an answer key to check: save or edit that one.')
                if not content.get('template_id') or not (content.get('problem') or reference.get(qid, {}).get('printed')):
                    raise Conflict(f'{label} has no confirmed answer area on a blank exam to read its problem from. Confirm it in Answer areas first.')
                context = {'rule': KEY_RULE, 'kind': KEY_KIND, 'question_id': qid, 'rubric_id': q['rubric_id'], 'label': label,
                           'question': {'title': content['title'], 'printed_number': content.get('printed_number', ''), 'max_points': content['max_points']},
                           'rubric_lines': [{'id': item['id'], 'points': item['points'], 'description': item['description']} for item in rubric['rules']['items']],
                           'full_credit_line': answers.rubric_roles(rubric['rules'])['full'], 'problem_text': content.get('problem', ''),
                           'printed': reference.get(qid, {}).get('printed', []),
                           **({'worked_solution': content['worked_solution']} if content.get('worked_solution') else {})}
                checksum = digest([aid, context, endpoint])
                prior = db.execute('SELECT * FROM frq_rubric_generations WHERE request_id=?', (f'{request_id}:{qid}',)).fetchone()
                if prior:
                    # The same request sent again after KEY_RULE v2 arrived (a tab opened before the restart) is still that reading:
                    # it was queued under the earlier rule, which sent no worked solution.
                    earlier = json.loads(prior['input_json'])['rule']
                    legacy = {k: v for k, v in {**context, 'rule': earlier}.items() if k != 'worked_solution'}
                    if prior['request_sha256'] != checksum and (earlier == KEY_RULE or prior['request_sha256'] != digest([aid, legacy, endpoint])):
                        raise Conflict('This request ID already belongs to another reading.')
                    continue
                if (readings.get(qid) or {}).get('status') in ('pending', 'running'):
                    continue  # Already being read.
                gid, now = uid(), timestamp()
                db.execute('INSERT INTO frq_rubric_generations(id,assignment_id,request_id,request_sha256,document_id,exam_version,template_id,input_json,profile_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,\'pending\',?,?)',
                    (gid, aid, f'{request_id}:{qid}', checksum, q['assignment_version_id'], q['exam_version'], content['template_id'], encoded(context),
                     encoded({'endpoint': endpoint, 'settings': settings}), now, now))
                queued.append(qid)
            if queued:
                self.event(db, aid, 'answer_key_reading_queued', {'request_id': request_id, 'question_ids': queued, 'model': endpoint['name']}, actor)
        self.inbox.wake.set()
        return queued

    def answer_key_readings(self, db, aid):
        """{question: its latest reading from the blank exam}: status, the key read (parts), how each was found, why nothing could be
        read, and whether it was read under the question's current rubric. A view of retained generations; it decides nothing."""
        latest = {}
        for row in db.execute("""SELECT id,status,cancelled,error,result_json,profile_json,json_extract(input_json,'$.question_id') question_id,
                json_extract(input_json,'$.rubric_id') rubric_id FROM frq_rubric_generations WHERE assignment_id=? AND json_extract(input_json,'$.kind')=? ORDER BY rowid""",
                (aid, KEY_KIND)):
            latest[row['question_id']] = row
        current = dict(db.execute('SELECT question_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=?', (aid,)).fetchall())
        found = {}
        for qid, row in latest.items():
            result = json.loads(row['result_json']) if row['status'] == 'complete' and row['result_json'] else {}
            found[qid] = {'id': row['id'], 'status': 'cancelled' if row['cancelled'] else row['status'], 'error': row['error'],
                          'model': json.loads(row['profile_json'])['endpoint']['name'], 'current': current.get(qid) == row['rubric_id'],
                          'key': result.get('key', []), 'basis': result.get('basis', []), 'unreadable': result.get('unreadable', '')}
        return found

    def _read_answer_key(self, task, context, endpoint, settings):
        """One question's answer key from its printed problem: the text read from the blank exam, a picture of the problem as printed
        where the model reads images, and the rubric lines, whose full-credit line says what the correct answer is; and the question's
        worked solution where it was queued with one (v2), to be used instead of solving the problem again."""
        pictures = self.rubric_problem_images({'reference': {'printed': context['printed']}}) if endpoint['modality'] == 'vision' else []
        if sum(map(len, pictures)) > IMAGES:
            pictures = []
        payload = key_request(context['question'], context['rubric_lines'], context['full_credit_line'], context['problem_text'], len(pictures),
                              worked_solution=context.get('worked_solution', ''))
        try:
            found, receipt = self._ask(task, endpoint, settings, KEY_STEP, 'answer_key_read', payload, pictures, read_key)
        except RetryLater as waiting:
            return 'pending', None, None, '', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError) as failure:
            return 'failed', None, None, f'The answer key could not be read: {failure}', ''
        result = {'rule': context['rule'], 'kind': KEY_KIND, 'question_id': context['question_id'], 'rubric_id': context['rubric_id'], **found}
        return 'complete', result, {'receipt': receipt, 'pictures': len(pictures)}, '', ''

    def reference_problems(self, db, aid, slot):
        """One version's questions with their CURRENT rubrics, as an adaptation sends them: printed number, title, points, the statement,
        worked solution and answer key where known, and every rubric line with the instructor's comment on it. The question and rubric
        IDs are kept beside what is sent, never sent: acceptance refuses a question whose rubric changed since it was adapted."""
        comments = self.line_comments(db, aid)
        found, seen = [], set()
        for (qid,) in db.execute("""SELECT p.question_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                WHERE p.assignment_id=? AND q.assignment_version_id=? AND q.exam_version=? AND p.rubric_id IS NOT NULL ORDER BY p.rowid""",
                                 (aid, slot['document_id'], slot['exam_version'])).fetchall():
            question = self.question(db, aid, qid)
            printed = self.location_context(db, aid, question)['printed_number']
            if not printed or printed in seen:
                continue
            seen.add(printed)
            content, rules, noted = question['content'], question['rubric']['rules'], comments.get(qid, {})
            found.append({'question_id': qid, 'rubric_id': question['rubric_id'], 'problem': {
                'number': printed, 'title': content['title'], 'points': content['max_points'], 'statement': content.get('problem') or '',
                'solution': content.get('worked_solution') or '',
                'final_answers': [{'part': part.get('label', ''), 'answer': ' or '.join(part.get('accept', []))} for part in content.get('answer_key') or []],
                'rubric': {'mode': rules['mode'], 'items': [{'id': item['id'], 'points': item['points'], 'description': item['description'],
                                                             **({'instructor_comment': noted[item['id']]} if noted.get(item['id']) else {})} for item in rules['items']]}}})
        return sorted(found, key=lambda q: int(q['problem']['number']))

    def queue_rubric_adaptation(self, aid, data):
        """Adapt one version's rubrics (`adapt_from`, a template slot key) to another version (`key`), whose problems are read from its
        blank template (`template_id`). One generation per version adapted to; its result is a draft like any other."""
        request_id = text(data.get('request_id'), 'Request ID', 128, False)
        with self.rosters.lock(), connect(self.path) as db:
            setup = self.template_setup(db, aid)
            source = next((s for s in setup['slots'] if s['key'] == data.get('adapt_from')), None)
            if not source:
                raise ValueError('Choose the version whose rubrics should be adapted.')
            if data.get('key') == source['key']:
                raise ValueError(f"Choose another version than {source['label']} to adapt its rubrics to.")
            slot, template = self._template_slot(setup, data.get('key'), data.get('template_id'), 'adapt rubrics to')
            endpoint, settings = self._drafting_model()
            references = self.reference_problems(db, aid, source)
            if not references:
                raise ValueError(f"{source['label']} has no rubric with a printed question number to adapt yet.")
            # The reference version's blank pages help to pair problems whose statement is not known (an uploaded CSV carries none).
            blank = next((t for t in source['options'] if t['id'] == source['template_id']), None)
            blank = blank if blank and blank['page_count'] <= MAX_PAGES else None
            context = {'rule': ADAPT_RULE, 'kind': 'adapt', 'document_id': slot['document_id'], 'exam_version': slot['exam_version'], 'label': slot['label'],
                       'template_id': template['id'], 'manifest_sha256': template['manifest_sha256'], 'template_label': template['label'], 'pages': template['page_count'],
                       'source': {'key': source['key'], 'document_id': source['document_id'], 'exam_version': source['exam_version'], 'label': source['label'],
                                  'template_id': blank['id'] if blank else None, 'template_pages': blank['page_count'] if blank else 0,
                                  'manifest_sha256': blank['manifest_sha256'] if blank else None, 'questions': references}}
            checksum = digest([aid, context, endpoint])
            prior = db.execute('SELECT * FROM frq_rubric_generations WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another rubric draft, or the rubrics changed since. Adapt them again.')
                return self.rubric_generation_summary(db, prior)
            summary = self._record_generation(db, aid, request_id, checksum, slot, template, context, endpoint, settings, 'rubric_adaptation_queued',
                                              {'adapted_from': source['key'], 'questions': [q['question_id'] for q in references]})
        self.inbox.wake.set()
        return summary

    def cancel_rubric_generation(self, aid, gid):
        """Stop a waiting draft. A call already submitted keeps its receipt; nothing further is sent for it."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            db.execute('BEGIN IMMEDIATE')
            if db.execute("UPDATE frq_rubric_generations SET status='failed',cancelled=1,updated_at=? WHERE id=? AND assignment_id=? AND status IN ('pending','running')",
                          (timestamp(), gid, aid)).rowcount:
                self.event(db, aid, 'rubric_generation_cancelled', {'generation_id': gid})
        return self.rubric_generation(aid, gid)

    # Worker ---------------------------------------------------------------------------------------------------------------------
    def _ask(self, task, endpoint, settings, step, prompt, payload, images, check, tools=None):
        """One request, asked once more when its answer is refused. What was answered before is replayed, never sent again. `tools`
        (a subscription connection only, `drafting_tools.rubric_tools`): what the model may call while it answers."""
        refused = None
        for attempt in (0, 1):
            reply = self.cloud_call({'rubric_generation_id': task['id'], 'step': step, 'task_attempt': attempt}, 'rubric_reviewer', endpoint,
                {'prompt': prompt, 'payload': payload, 'images': images, **({'reask': True} if attempt else {}), **({'tools': tools} if tools else {})}, settings)
            try:
                return check(reply['output']), {'metadata': reply['metadata'], 'raw': reply['raw']}
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    def _memory_rules(self, definition, task):
        """The course's rubric memories as they stood when this draft was asked for (GRADING "RUBRIC MEMORIES"), the same on every retry
        of the draft: what each rubric writer reads beside the problem. [] when the course has none."""
        from .rubric_memory import standing_rules
        with connect(self.path) as db:
            return standing_rules(db, definition, task['created_at'])

    def _course_of(self, task):
        """(course definition, course-wide assignment) of a draft's grading plan: whose notes, bank and approved rubrics its tools read,
        and whose own questions they leave out."""
        with connect(self.path) as db:
            assignment = self.plan(db, task['assignment_id'])['source_assignment_id']
            return definition_of(db, assignment), assignment

    def _pair_versions(self, task, context, endpoint, settings, problems):
        """Which reference problem each problem of this version is a version of: ONE request that sees both versions' problems, never a
        pairing by printed number (versions may reorder problems). The reference version's blank pages are attached where some reference
        statement is unknown, as it is for an uploaded CSV; without them the rubric lines, which usually name the answer, still say much."""
        source = context['source']
        references = [q['problem'] for q in source['questions']]
        pictures = []
        if source['template_id'] and any(not r['statement'] for r in references):
            try:
                if self.templates.get(source['template_id'])['page_count'] == source['template_pages']:
                    pictures = [self.templates.read(source['template_id'], 'image', page)[0] for page in range(1, source['template_pages'] + 1)]
            except (KeyError, ValueError, NotFound, OSError):
                pictures = []
            if sum(map(len, pictures)) > IMAGES:
                pictures = []
        payload = {'reference': {'version': source['label'], 'pictures': len(pictures), 'problems': [
                       {**{k: r[k] for k in ('number', 'title', 'points', 'statement', 'final_answers')}, 'rubric_lines': [line['description'] for line in r['rubric']['items']]}
                       for r in references]},
                   'target': {'version': context['label'], 'problems': [{k: p[k] for k in ('number', 'label', 'points', 'parts', 'statement')} for p in problems]}}
        return self._ask(task, endpoint, settings, MATCH, 'rubric_adapt_match', payload, pictures, lambda output: version_pairs(output, problems, references))

    def run_rubric_generation(self, task):
        context, profile = json.loads(task['input_json']), json.loads(task['profile_json'])
        mode = scoring_mode(context.get('mode', 'positive'))  # Existing generations were positive-only.
        endpoint, settings = profile['endpoint'], profile['settings']
        if context.get('kind') == KEY_KIND:
            outcome = self._read_answer_key(task, context, endpoint, settings)
            self._publish_generation(task, *outcome)
            if outcome[0] != 'pending':  # Read, unreadable or failed: where no key is left to save, an accepted draft's check may start.
                self.rubric_review_follows(task['assignment_id'])
            return
        if context.get('kind') == 'versions':
            return self._publish_generation(task, *self._draft_versions(task, context, endpoint, settings))
        adapting = context.get('kind') == 'adapt'
        result, metadata, error, status, after = None, None, '', 'complete', ''
        try:
            template = self.templates.get(context['template_id'])
            if template['page_count'] != context['pages']:
                raise ValueError('The template changed after this draft was requested. Request a new draft.')
            pictures = [self.templates.read(context['template_id'], 'image', page)[0] for page in range(1, context['pages'] + 1)]
            if sum(map(len, pictures)) > IMAGES:
                raise ValueError("The template's page pictures exceed 20 MiB and cannot be sent in one request.")
            page_text = self._page_text(context['template_id'], context['pages'])
            survey = {'assignment': context['label'], 'pages': context['pages'], 'page_text': page_text}
            surveyor = {**endpoint, 'max_tokens': max(endpoint['max_tokens'], SURVEY_TOKENS)}
            definition, assignment = self._course_of(task)
            (problems, notes), survey_receipt = self._ask(task, surveyor, settings, SURVEY, 'rubric_survey', survey, pictures,
                                                          lambda output: survey_problems(output, context['pages']),
                                                          rubric_tools(self, endpoint, definition, {'': pictures}, survey=True))
            writer_tools = rubric_tools(self, endpoint, definition, {'': pictures}, assignment_id=assignment)
            standing = self._memory_rules(definition, task)
            chosen = rubric_choices.stated(context.get('rubric_choices'))
            if adapting:
                source = context['source']
                references = {q['problem']['number']: q for q in source['questions']}
                try:
                    (pairs, paired_notes), pairing_receipt = self._pair_versions(task, context, endpoint, settings, problems)
                except (ProviderError, ValueError, TypeError, KeyError) as failure:
                    raise ValueError(f"the problems of {context['label']} could not be paired with {source['label']}'s: {failure}") from None
                used = {pair['reference'] for pair in pairs.values()}
                notes = notes + paired_notes + [f"{source['label']}'s {q['problem']['title']} was paired with no problem of {context['label']}."
                                                for number, q in references.items() if number not in used]

            def one(problem):
                if adapting:
                    pair = pairs[problem['number']]
                    if not pair['reference']:
                        return problem, None, None, None, (f"No problem of {source['label']} asks the same task{' (' + pair['reason'] + ')' if pair['reason'] else ''}. "
                                                           'Draft it from this template or upload its Gradescope CSV.')
                    reference = references[pair['reference']]
                    payload = {'reference': {'version': source['label'], **reference['problem']},
                               'target': {'version': context['label'], **{k: problem[k] for k in ('number', 'label', 'points', 'points_text', 'parts', 'statement')}},
                               'pictures_show_pages': problem['pages'][:3]}
                    origin = {'question_id': reference['question_id'], 'rubric_id': reference['rubric_id'], 'number': reference['problem']['number'],
                              'title': reference['problem']['title'], 'label': source['label'], 'reason': pair['reason']}
                    step, prompt, check = ADAPT + problem['number'], 'rubric_adapt', lambda output: {
                        **adapted_rubric(output, reference['problem'], problem, source['label']), 'source': origin}
                else:
                    payload = {'assignment': context['label'], 'problem': {k: problem[k] for k in ('number', 'label', 'points', 'points_text', 'parts', 'statement')},
                               'pictures_show_pages': problem['pages'][:3]}
                    # Keep historical request fingerprints replayable; the v3 prompt defaults a missing mode to positive.
                    if 'mode' in context:
                        payload['mode'] = mode
                    if standing:  # RUBRIC MEMORIES (2026-10-14): the course's kept rules and watches, as they stood when the draft was asked for
                        payload['standing_rules'] = standing
                    if chosen:  # RUBRIC CHOICES (2026-10-24): the instructor's quick answers given with this draft
                        payload['assignment_choices'] = chosen
                    step, prompt, check = WRITE + problem['number'], 'rubric_write', lambda output: written_rubric(output, problem, mode)
                try:
                    drafted, receipt = self._ask(task, endpoint, settings, step, prompt, payload, [pictures[page - 1] for page in problem['pages'][:3]], check,
                                                 writer_tools)
                    return problem, drafted, receipt, None, None
                except RetryLater as waiting:
                    return problem, None, None, waiting.seconds, None
                except (ProviderError, ValueError, TypeError, KeyError) as failure:
                    return problem, None, None, None, str(failure)
            # Every problem is its own request and its own context, asked side by side. Each settled answer is a retained call of this
            # draft, so a later turn (after a wait, or a restart) sends only what is still missing.
            with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(problems)))) as pool:
                outcomes = list(pool.map(one, problems))
            waits = [seconds for *_, seconds, _ in outcomes if seconds is not None]
            if waits:
                status, after = 'pending', later(min(waits))
            else:
                questions = [{**{k: problem[k] for k in ('number', 'label', 'title', 'points_text', 'parts', 'pages', 'statement')}, 'printed_points': problem['points'], **drafted}
                             for problem, drafted, _, _, failure in outcomes if not failure]
                failed = [{'number': problem['number'], 'error': failure} for problem, *_, failure in outcomes if failure]
                done = 'adapted' if adapting else 'drafted'
                result = {'rule': context['rule'], 'mode': mode, 'questions': questions, 'failed': failed, 'notes': notes,
                    'summary': (f"{len(questions)} of {len(problems)} printed problems adapted from {source['label']}'s rubrics." if adapting else
                                f"{len(questions)} of {len(problems)} printed problems drafted from {context['template_label']}.")
                        + ((f" {len(failed)} could not be adapted; draft them from this template or upload their Gradescope CSVs." if adapting else
                            f" {len(failed)} could not be drafted; upload their Gradescope CSVs or draft again.") if failed else '')}
                if adapting:
                    result.update(kind='adapt', source={'key': source['key'], 'label': source['label']})
                    result.pop('mode')  # Each adapted question keeps its reference rubric's own scoring.
                metadata = {'survey': survey_receipt, 'questions': {problem['number']: receipt for problem, _, receipt, _, _ in outcomes if receipt},
                            **({'pairing': pairing_receipt} if adapting else {})}
                if not questions:
                    status, error, result = 'failed', f'No problem could be {done}. ' + ' '.join(f"Problem {f['number']}: {f['error']}" for f in failed[:3]), None
        except RetryLater as waiting:
            status, after = 'pending', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound) as failure:
            status, error = 'failed', (f'The rubrics could not be adapted: {failure}' if adapting else f'The template could not be read for problems: {failure}')
        self._publish_generation(task, status, result, metadata, error, after)

    def _publish_generation(self, task, status, result, metadata, error, after):
        with self.rosters.lock(), connect(self.path) as db:
            published = db.execute("UPDATE frq_rubric_generations SET status=?,result_json=?,provider_json=?,error=?,not_before=?,updated_at=? WHERE id=? AND status='running'",
                (status, encoded(result) if result else None, encoded(metadata) if metadata else None, error, after, timestamp(), task['id']))
            if status == 'complete' and published.rowcount:
                self.event(db, task['assignment_id'], 'rubric_generation_ready', {'generation_id': task['id']}, 'worker')

    # Acceptance -----------------------------------------------------------------------------------------------------------------
    def accept_rubric_generation(self, aid, gid, data):
        """The instructor's decision on a draft: the questions they keep, as they edited them. Each becomes an ordinary draft question
        and rubric revision with its upload receipt, independently: a question that is refused never hides one that was created.
        Nothing is approved here; the rubric finalization of the third step does that for drafted and uploaded rubrics alike."""
        request_id = identifier(data.get('request_id'))
        chosen = items(data.get('questions'), 'Accepted questions', MAX_PROBLEMS)
        if not chosen:
            raise ValueError('Choose at least one drafted question to create.')
        if any(not isinstance(entry, dict) or not isinstance(entry.get('draft'), str) for entry in chosen):
            raise ValueError('Each accepted question must name one drafted problem.')
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            row = db.execute('SELECT * FROM frq_rubric_generations WHERE id=? AND assignment_id=?', (gid, aid)).fetchone()
            if not row:
                raise NotFound('Rubric draft not found.')
            if row['status'] != 'complete' or row['cancelled']:
                raise Conflict('This draft is not finished.')
            if json.loads(row['input_json']).get('kind') == KEY_KIND:
                raise Conflict('An answer key read from the blank assignment is saved with the answer key, not accepted as a draft.')
            together = json.loads(row['input_json']).get('kind') == 'versions'
        if together:  # Outside the catalog connection, like every acceptance: each created question opens its own transaction.
            return self._accept_versions(aid, gid, row, chosen, request_id)
        with self.rosters.lock(), connect(self.path) as db:
            drafted = {q['number']: q for q in json.loads(row['result_json'])['questions']}
            context, model = json.loads(row['input_json']), json.loads(row['profile_json'])['endpoint']
            mode = scoring_mode(context.get('mode', 'positive'))
            if context.get('kind') == 'adapt':
                # The reference rubrics as they are NOW: an adapted question is kept only while its reference is the rubric it was adapted from.
                current = {}
                for q in drafted.values():
                    qid = q['source']['question_id']
                    pinned = db.execute('SELECT 1 FROM frq_assignment_questions WHERE assignment_id=? AND question_id=?', (aid, qid)).fetchone()
                    current[qid] = self.question(db, aid, qid) if pinned else None
        if context.get('kind') == 'adapt':
            return self._accept_adaptation(aid, gid, row, context, model, drafted, current, chosen, request_id, data.get('copy_comments') is True)
        results, seen, made = [], set(), {}
        for entry in chosen:
            draft = drafted.get(entry.get('draft')) if isinstance(entry, dict) else None
            if not draft or draft['number'] in seen:
                raise ValueError('Each accepted question must name one drafted problem once.')
            seen.add(draft['number'])
            try:
                if entry.get('mode', mode) != mode:
                    raise ValueError('The scoring mode must match the saved draft. Generate a new draft to change scoring.')
                printed = locations.printed_number(str(entry.get('printed_number', '')))
                if not printed:
                    raise ValueError('Give the printed question number.')
                maximum = number(entry.get('max_points'), 'Question points', 0, 1000)
                if not maximum:
                    raise ValueError('A question must be worth some points.')
                lines = accepted_lines(entry.get('items'), maximum, mode)
                notes = drafted_notes(lines)
                statement = text(entry.get('statement', ''), 'Problem statement', 20000).strip()
                named = text(entry.get('title', ''), 'Question title', 200).strip() or draft.get('title') or f'Question {printed}'
                solution = text(entry.get('solution', ''), 'Worked solution', 20000).strip()
                warnings = [CREATED_BY]
                rows = [dict(line) for line in lines]
                rubrics.wording_rules(rows, warnings)
                worths = rubrics.part_worths(draft.get('parts'), draft.get('part_points'))
                rules = rubrics.validate(rubrics.capped_parts({'mode': mode, 'items': rows, 'warnings': warnings}, worths, maximum), maximum)
                edited = (printed, named, maximum, [(l['description'], l['points']) for l in lines], statement, solution) != (
                    draft['number'], draft.get('title') or f"Question {draft['number']}", draft['points'], [(l['description'], l['points']) for l in draft['items']], draft['statement'], draft['solution'])
                # The retained source is what the instructor accepted, beside the model's own draft of it: the rubric's "original file".
                source = json.dumps({'mode': mode, 'items': lines, 'notes': f"Drafted by {model['name']} ({model['model']}) from {context['template_label']}, "
                                     f"problem {draft['number']}, and accepted by the instructor{' after editing' if edited else ''}."}, indent=2, ensure_ascii=False)
                content = source.encode()
                exam_version = row['exam_version']
                created = self.create_draft_question(aid, filename=f"drafted-question-{printed}.json", content=content, source=source,
                    rules=rules, max_points=maximum, printed=printed, exam_version=exam_version, key=f"q{printed}" + (f"-{exam_version}" if exam_version else ''),
                    document_id=row['document_id'], request_id=f"{request_id}:{draft['number']}", title=named,
                    compiler={'name': COMPILER, 'rule': context['rule'], 'mode': mode, 'generation_id': gid, 'problem': draft['number'], 'template_id': row['template_id'],
                              'model': model['name'], 'requested_model': model['model'], 'edited': edited},
                    event='rubric_draft_accepted', statement=statement, solution=solution, detail={'generation_id': gid, 'mode': mode, 'edited': edited})
                created['clarifications_saved'] = self._save_drafted_clarifications(aid, created, notes)
                made[created['question_id']] = notes
                results.append({'draft': draft['number'], **created})
            except (ValueError, Conflict) as failure:
                results.append({'draft': draft['number'], 'status': 'failed', 'error': getattr(failure, 'description', None) or str(failure)})
        return {'results': results, **self._after_acceptance(aid, results, made), 'generation': self.rubric_generation(aid, gid)}

    def _accept_adaptation(self, aid, gid, row, context, model, drafted, current, chosen, request_id, copy_comments):
        """The instructor's decision on adapted rubrics. Each kept question keeps its reference rubric's structure (`adapted_rules`): the
        same line IDs, rules and scoring, with the wording and points the instructor accepted for this version. A question whose reference
        rubric changed since it was adapted is refused for that question alone: adapt it again. With `copy_comments`, the instructor's
        comments on the reference lines are copied to the same lines of the new question (their own explicit choice, never implied)."""
        source_label = context['source']['label']
        results, seen, made = [], set(), {}
        for entry in chosen:
            draft = drafted.get(entry.get('draft')) if isinstance(entry, dict) else None
            if not draft or draft['number'] in seen:
                raise ValueError('Each accepted question must name one adapted problem once.')
            seen.add(draft['number'])
            try:
                origin, reference = draft['source'], current.get(draft['source']['question_id'])
                if not reference or reference['rubric_id'] != origin['rubric_id']:
                    raise Conflict(f"{source_label}'s rubric for {origin['title']} changed after it was adapted. Adapt it again to use the current rubric.")
                mode = reference['rubric']['rules']['mode']
                if entry.get('mode', mode) != mode:
                    raise ValueError(f"An adapted rubric keeps {source_label}'s scoring.")
                printed = locations.printed_number(str(entry.get('printed_number', '')))
                if not printed:
                    raise ValueError('Give the printed question number.')
                maximum = number(entry.get('max_points'), 'Question points', 0, 1000)
                if not maximum:
                    raise ValueError('A question must be worth some points.')
                statement = text(entry.get('statement', ''), 'Problem statement', 20000).strip()
                named = text(entry.get('title', ''), 'Question title', 200).strip() or draft.get('title') or f'Question {printed}'
                solution = text(entry.get('solution', ''), 'Worked solution', 20000).strip()
                inherited = [w for w in reference['rubric']['rules']['warnings'] if w != CREATED_BY and not w.startswith('Adapted by a model from ')]
                warnings = [ADAPTED_BY.format(source=source_label), *inherited]
                accepted = items(entry.get('items'), 'Rubric lines', max(MAX_LINES, len(draft['items']) + MAX_LINES))
                rules = adapted_rules(reference['rubric']['rules'], accepted, maximum, reference['content']['max_points'], warnings,
                                      worths=rubrics.part_worths(draft.get('parts')))
                kept = [(l.get('source') or '', (l.get('description') or '').strip(), l.get('points')) for l in accepted if isinstance(l, dict) and (l.get('description') or '').strip()]
                edited = (printed, named, maximum, kept, statement, solution) != (
                    draft['number'], draft.get('title') or f"Question {draft['number']}", draft['points'], [(l['id'], l['description'], l['points']) for l in draft['items']],
                    draft['statement'], draft['solution'])
                lines = [{k: item[k] for k in ('id', 'description', 'points')} for item in rules['items']]
                source = json.dumps({'mode': mode, 'items': lines, 'adapted_from': {'version': source_label, 'question': origin['number'], 'rubric_id': origin['rubric_id'],
                                     'revision': reference['rubric']['revision']},
                                     'notes': f"Adapted by {model['name']} ({model['model']}) from {source_label}'s {origin['title']} (rubric revision {reference['rubric']['revision']}) "
                                              f"for problem {draft['number']} of {context['template_label']}, and accepted by the instructor{' after editing' if edited else ''}."},
                                    indent=2, ensure_ascii=False)
                exam_version = row['exam_version']
                created = self.create_draft_question(aid, filename=f"adapted-question-{printed}.json", content=source.encode(), source=source,
                    rules=rules, max_points=maximum, printed=printed, exam_version=exam_version, key=f"q{printed}" + (f"-{exam_version}" if exam_version else ''),
                    document_id=row['document_id'], request_id=f"{request_id}:{draft['number']}", title=named,
                    compiler={'name': ADAPT_COMPILER, 'rule': context['rule'], 'mode': mode, 'generation_id': gid, 'problem': draft['number'], 'template_id': row['template_id'],
                              'model': model['name'], 'requested_model': model['model'], 'edited': edited,
                              'adapted_from': {'question_id': origin['question_id'], 'rubric_id': origin['rubric_id'], 'exam_version': context['source']['exam_version'],
                                               'printed_number': origin['number']}},
                    event='rubric_adaptation_accepted', statement=statement, solution=solution,
                    detail={'generation_id': gid, 'edited': edited, 'adapted_from': origin['question_id'], 'adapted_rubric': origin['rubric_id']})
                if copy_comments:
                    created['comments_copied'] = self._copy_line_comments(aid, origin['question_id'], created['question_id'])
                made[created['question_id']] = origin['question_id'] if copy_comments else None
                results.append({'draft': draft['number'], **created})
            except (ValueError, Conflict) as failure:
                results.append({'draft': draft['number'], 'status': 'failed', 'error': getattr(failure, 'description', None) or str(failure)})
        if made:  # What this acceptance gave each created question's lines: the reference lines' comments it copied, or none.
            with self.rosters.lock(), connect(self.path) as db:
                noted = self.line_comments(db, aid)
            made = {qid: dict(noted.get(copied_from, {})) if copied_from else {} for qid, copied_from in made.items()}
        return {'results': results, **self._after_acceptance(aid, results, made), 'generation': self.rubric_generation(aid, gid)}

    def _save_drafted_clarifications(self, aid, created, notes):
        """An accepted draft's clarifications, saved as the created question's line comments (2026-09-28). The draft keeps the wording
        Gradescope shows concise and puts the cases graders stopped at in a clarification per line; the instructor reads and edits it in
        the draft review, and the comment is the existing channel that carries it to the rubric review and into the clarifications the
        instructor confirms, never into the rubric. Only on the rubric this acceptance created, still current, and only on lines with no
        comment history, so a repeated acceptance never brings back a comment the instructor removed."""
        if created.get('status') not in ('imported', 'already_imported') or not notes:
            return 0
        qid, rid = created['question_id'], created['rubric_id']
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            question = self.question(db, aid, qid)
            if not question['rubric'] or question['rubric']['id'] != rid:
                return 0
            lines = {item['id'] for item in question['rubric']['rules']['items']}
            touched = {r[0] for r in db.execute('SELECT DISTINCT item_id FROM frq_rubric_line_comments WHERE assignment_id=? AND question_id=?', (aid, qid))}
            saved = {iid: note for iid, note in sorted(notes.items()) if iid in lines and iid not in touched}
            now = timestamp()
            for iid, note in saved.items():
                db.execute('INSERT INTO frq_rubric_line_comments VALUES(?,?,?,?,?,?,?,?)', (uid(), aid, qid, iid, rid, note, 'workspace', now))
            if saved:
                self.event(db, aid, 'rubric_draft_clarifications_saved', {'question_id': qid, 'rubric_id': rid, 'items': sorted(saved)})
            return len(saved)

    def _copy_line_comments(self, aid, from_qid, to_qid):
        """The instructor's current comments on one question's lines, copied to the lines of the same ID of another question of the plan.
        Append-only like every comment; a line that already has a comment history (even a cleared one) is left alone, so a repeated
        acceptance never brings back a comment the instructor removed."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            question = self.question(db, aid, to_qid)
            if not question['rubric']:
                return 0
            lines = {item['id'] for item in question['rubric']['rules']['items']}
            touched = {r[0] for r in db.execute('SELECT DISTINCT item_id FROM frq_rubric_line_comments WHERE assignment_id=? AND question_id=?', (aid, to_qid))}
            copied = {iid: note for iid, note in self.line_comments(db, aid, from_qid).get(from_qid, {}).items() if iid in lines and iid not in touched}
            for iid, note in copied.items():
                db.execute('INSERT INTO frq_rubric_line_comments VALUES(?,?,?,?,?,?,?,?)', (uid(), aid, to_qid, iid, question['rubric_id'], note, 'workspace', timestamp()))
            if copied:
                self.event(db, aid, 'rubric_line_comments_copied', {'question_id': to_qid, 'rubric_id': question['rubric_id'], 'from_question_id': from_qid, 'items': sorted(copied)})
            return len(copied)

    # Accepting a draft readies it -----------------------------------------------------------------------------------------------
    def _after_acceptance(self, aid, results, made):
        """What an acceptance does by itself for the questions it created (ACCEPTING A DRAFT READIES IT, at `READIED`), once their
        drafted clarifications or copied comments are saved: a confirmation names the comments it covers. `made` maps each question
        this acceptance created, or found already created, to the comments the acceptance gave its lines.

        1. Confirmed for the rubric review as `draft-acceptance`, through the page's own `confirm_rubric_review`: the instructor read
           and edited each rubric in the draft review. A request sent again (`already_imported`) confirms only a question that was
           never confirmed and still holds exactly the rubric and comments it was given, so a retry of an interrupted acceptance
           finishes it and no later one confirms what the instructor changed since. Never a rubric this acceptance REPLACED.
        2. The rubric check asked for if the plan is ready for it (`request_rubric_review_when_ready`), which right after an
           acceptance it rarely is.
        No key is read here: a created question has no confirmed answer area yet, and its key is read with the picture of that area
        once it is confirmed (`_read_keys_of_confirmed_areas`). Nothing here may fail the acceptance: whatever is refused is left
        for the page's own buttons. Returns what the answer adds (nothing when no question was created): `readied`, the questions
        confirmed; `keys_to_read`, those of this acceptance whose key is read once their answer area is confirmed; and `review`,
        '' when the check was asked for, else the sentence saying what it waits for."""
        mine = [r for r in results if r.get('status') in ('imported', 'already_imported') and r.get('question_id') in made]
        if not mine:
            return {}
        readied = []
        try:
            with self.rosters.lock(), connect(self.path) as db:
                status = self.rubric_quality_status(db, aid)
                before = {r[0] for r in db.execute('SELECT DISTINCT question_id FROM frq_rubric_review_ready WHERE assignment_id=?', (aid,))}
                asked = {}
                for r in mine:
                    qid, state = r['question_id'], status['readiness'].get(r['question_id'])
                    if not state or state['ready'] or state['rubric_id'] != r['rubric_id']:
                        continue
                    if r['status'] == 'already_imported':
                        lines = {item['id'] for item in self.question(db, aid, qid)['rubric']['rules']['items']}
                        if qid in before or state['comments'] != {iid: note for iid, note in made[qid].items() if iid in lines}:
                            continue
                    asked[qid] = {'rubric_id': state['rubric_id'], 'comments_sha256': state['comments_sha256']}
            for start in range(0, len(asked), 100):  # One confirmation names at most 100 questions.
                chunk = dict(list(asked.items())[start:start + 100])
                self.confirm_rubric_review(aid, {'questions': chunk}, actor=READIED)
                readied += list(chunk)
        except (ValueError, Conflict, NotFound):
            pass  # Changed in between: Confirm all for rubric review is still there for what was not confirmed.
        review, to_read = self._review_follows(aid)
        return {'readied': readied, 'keys_to_read': [r['question_id'] for r in mine if r['question_id'] in to_read], 'review': review}

    @staticmethod
    def _readied_plan(db, aid):
        """Whether any rubric of the plan was ever confirmed for review by accepting its draft. One indexed read: every plan read, key
        save and answer-area confirmation asks it, and a plan no acceptance readied is not read any further."""
        return bool(db.execute('SELECT 1 FROM frq_rubric_review_ready WHERE assignment_id=? AND actor=? LIMIT 1', (aid, READIED)).fetchone())

    @staticmethod
    def readied_by_acceptance(db, aid, status):
        """The questions whose CURRENT confirmation for the rubric review (`status`: the plan's `rubric_quality_status`) is the
        acceptance of their draft: their latest confirmation has that actor and names today's rubric revision and comments."""
        current = {qid: (state['rubric_id'], state['comments_sha256']) for qid, state in status['readiness'].items()}
        return {row['question_id'] for row in db.execute(
            '''SELECT c.question_id,c.rubric_id,c.comments_sha256 FROM frq_rubric_review_ready c WHERE c.assignment_id=? AND c.actor=?
               AND c.rowid=(SELECT MAX(n.rowid) FROM frq_rubric_review_ready n WHERE n.assignment_id=c.assignment_id AND n.question_id=c.question_id)''',
            (aid, READIED)) if current.get(row['question_id']) == (row['rubric_id'], row['comments_sha256'])}

    def _setup_facts(self, db, aid, question_ids):
        """{question: its `location_check` and `answer` exactly as the plan read (`assignment`) gives them}, read here where the plan
        read is not at hand."""
        readings, memo, found = self.answer_key_readings(db, aid), {}, {}
        for qid in question_ids:
            question = self.question(db, aid, qid)
            rules = question['rubric']['rules'] if question['rubric'] else None
            found[qid] = {'question_id': qid, 'location_check': self.location_status(db, aid, question, memo=memo),
                          'answer': {'key': question['content'].get('answer_key', []), 'proposed_key': answers.key_from_rubric(rules) if rules else [],
                                     **({'read_key': readings[qid]} if qid in readings else {})}}
        return found

    def rubric_review_wait(self, db, aid, status, questions=None):
        """READ ONLY (catalog reads; nothing is written or queued): where the rubric check of a plan with accepted drafts stands, as
        (state, sentence, the questions whose key is read once their answer area is confirmed). `status` is the plan's
        `rubric_quality_status`; `questions` are the plan read's own questions, each with its `location_check` and `answer`, so the
        plan read (`assignment`) asks nothing twice; elsewhere the asked questions are read here.

        A check asks the questions whose confirmed finalization does not stand. The states:
        'yours'      none of them holds a CURRENT confirmation made by accepting its draft: a plan of uploaded rubrics, a quiz's or
                     a makeup's approval and rubrics the instructor confirmed again themselves keep their own Run rubric check or
                     check request;
        'finalized'  the finalization is confirmed for exactly these rubrics (also one carried to a corrected line, which no check
                     of these rubrics exists for);
        'checking'   a check is waiting or running;
        'checked'    one exists for exactly these rubrics: finished, failed or cancelled, it stays the instructor's to review or
                     run again;
        'waiting'    the sentence says for what: a version without rubrics; a rubric not confirmed for review; a rubric draft
                     waiting or running; an answer area not confirmed (only then does the check's snapshot of a problem,
                     `rubric_reference`, carry its printed crop); a key still to be read or being read; a key proposal (the
                     rubric's, or its latest reading's) not saved, so that the snapshot carries the key the instructor saved;
        'ready'      nothing is waited for: `request_rubric_review_when_ready` asks for the check."""
        if not self._readied_plan(db, aid):
            return 'yours', YOURS_TO_RUN, []
        if status['review']:
            return 'finalized', 'The rubric finalization of these rubrics is already confirmed.', []
        asked = [qid for qid in status['readiness'] if not ((stands := status['standing'].get(qid)) and not stands['open'])]
        readied = self.readied_by_acceptance(db, aid, status)
        if not readied.intersection(asked):
            return 'yours', YOURS_TO_RUN, []
        facts = {q['question_id']: q for q in questions} if questions is not None else self._setup_facts(db, aid, asked)
        located = {qid for qid in asked if facts[qid]['location_check']['status'] == 'confirmed'}
        keys = {qid: key_stands(facts[qid]['answer']) for qid in asked}
        # Its key is read once its answer area is confirmed: only for a question an acceptance readied, never read before.
        to_read = [qid for qid in asked if qid in readied and qid not in located and keys[qid] == 'unread']
        if db.execute("SELECT 1 FROM frq_rubric_quality_checks WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
            return 'checking', 'A rubric check is already waiting or running.', to_read
        if db.execute('SELECT 1 FROM frq_rubric_quality_checks WHERE assignment_id=? AND input_sha256=? LIMIT 1', (aid, status['input_sha256'])).fetchone():
            return 'checked', 'These rubrics already have a rubric check: review it or run it again in Key & Rubric Finalization.', to_read
        waits = []
        if status['missing']:
            waits.append('every version has its rubrics')
        if status['unconfirmed']:
            waits.append('every rubric is confirmed for review')
        working = [kind or '' for (kind,) in db.execute(
            "SELECT json_extract(input_json,'$.kind') FROM frq_rubric_generations WHERE assignment_id=? AND status IN ('pending','running')", (aid,))]
        if any(kind != KEY_KIND for kind in working):
            waits.append('the rubric draft in progress has finished')
        if len(located) < len(asked):
            waits.append('the answer areas are confirmed')
        if to_read:
            waits.append('the answer keys, each read once its answer area is confirmed, are saved')
        elif KEY_KIND in working:
            waits.append('the answer keys are read and saved')
        elif 'unsaved' in keys.values():
            waits.append('the answer keys are saved')
        if not waits:
            return 'ready', '', []
        return 'waiting', f"The rubric check starts by itself once {' and '.join(filter(None, (', '.join(waits[:-1]), waits[-1])))}.", to_read

    def _review_when_ready(self, aid):
        """`request_rubric_review_when_ready`, with the questions whose key is read once their answer area is confirmed."""
        with self.rosters.lock():
            with connect(self.path) as db:
                if not self._readied_plan(db, aid):
                    return YOURS_TO_RUN, []
                status = self.rubric_quality_status(db, aid)
                state, sentence, to_read = self.rubric_review_wait(db, aid, status)
            if state != 'ready':
                return sentence, to_read
            try:
                self.queue_rubric_quality(aid, {'request_id': f"{READY_REQUEST}:{aid}:{status['input_sha256'][:40]}", 'input_sha256': status['input_sha256']})
            except (ValueError, Conflict) as failure:
                return getattr(failure, 'description', None) or str(failure), []
        return '', []

    def request_rubric_review_when_ready(self, aid):
        """Ask for the rubric check by itself once a plan with accepted drafts is ready for it (ACCEPTING A DRAFT READIES IT, at
        `READIED`). Returns '' when it asked, else one sentence saying what the check waits for or why it is not asked for by
        itself (`rubric_review_wait`, which decides and only reads); while it waits it writes nothing. The request ID names the plan
        and the fingerprint of its rubrics, so one state asks once. `queue_rubric_quality` checks its own conditions again (a
        reviewer model, the size of the set); what it refuses is returned as the sentence."""
        return self._review_when_ready(aid)[0]

    def _review_follows(self, aid):
        """`_review_when_ready` for the acts it follows: it never fails them (`rubric_review_follows`)."""
        try:
            return self._review_when_ready(aid)
        except Exception:
            LOG.warning('The rubric check of grading plan %s could not be asked for by itself', aid, exc_info=True)
            return YOURS_TO_RUN, []

    def rubric_review_follows(self, aid):
        """`request_rubric_review_when_ready` after an act that may have readied the plan: an acceptance, an answer area confirmed, an
        answer key saved or read, a rubric confirmed for review. Whatever goes wrong here never fails that act, and Run rubric
        check is still the instructor's."""
        return self._review_follows(aid)[0]

    def _read_keys_of_confirmed_areas(self, aid, question_ids):
        """THE KEY IS READ WITHOUT BEING ASKED (ACCEPTING A DRAFT READIES IT, at `READIED`): for each of the questions whose answer
        area was just confirmed, the ORDINARY reading of its key from the blank assignment, exactly as the page's button asks for it
        (`_queue_key_readings`: the picture of the confirmed area, the problem's text, the stored worked solution, the same
        refusals), so the proposal the instructor saves is no worse than the button's. Only for a question whose CURRENT
        confirmation for review is the acceptance of its draft, with no saved key, no key its rubric proposes and no reading yet;
        every other question keeps the button. The request ID names the confirmation and the question, so one confirmation asks
        once. What is refused (no reviewer model, nothing to read the problem from) is left for the button. Returns the questions
        queued; the key read stays a proposal until the instructor saves it."""
        with self.rosters.lock():
            with connect(self.path) as db:
                if not self._readied_plan(db, aid):
                    return []
                readied = self.readied_by_acceptance(db, aid, self.rubric_quality_status(db, aid))
                facts = self._setup_facts(db, aid, [qid for qid in dict.fromkeys(question_ids) if qid in readied])
                wanted = [(qid, fact['location_check']['review_id']) for qid, fact in facts.items()
                          if fact['location_check']['status'] == 'confirmed' and key_stands(fact['answer']) == 'unread']
            queued = []
            for qid, confirmation in wanted:
                try:
                    queued += self._queue_key_readings(aid, f'{KEYS_REQUEST}:{confirmation}', [qid], actor='automatic')
                except (ValueError, Conflict, NotFound):
                    continue
            return queued

    def after_answer_areas(self, aid, question_ids):
        """What follows confirmed answer areas by itself (ACCEPTING A DRAFT READIES IT, at `READIED`), never failing the confirmation:
        first the key of each just-confirmed question is asked to be read, then the rubric check is asked for. While such a reading
        waits or runs the check waits for it, and the reading's own end asks again (`run_rubric_generation`), so the last confirmed
        area never starts a check ahead of the key it just asked for."""
        try:
            self._read_keys_of_confirmed_areas(aid, question_ids)
        except Exception:
            LOG.warning('The answer keys of grading plan %s could not be asked to be read by themselves', aid, exc_info=True)
        self.rubric_review_follows(aid)

    # Every version at once ------------------------------------------------------------------------------------------------------
    def queue_rubric_versions(self, aid, data):
        """Draft the rubrics of EVERY version at once (requirement, 2026-09-25): one generation for the plan, read from each version's
        chosen blank template (`templates`: [{key, template_id}] for every template slot), in one scoring mode."""
        request_id = text(data.get('request_id'), 'Request ID', 128, False)
        mode = scoring_mode(data.get('mode', 'positive'))
        templates = items(data.get('templates'), 'Version templates', 26)
        choices = rubric_choices.clean(data.get('rubric_choices'))
        if any(not isinstance(t, dict) or not isinstance(t.get('key'), str) or not isinstance(t.get('template_id'), str) for t in templates):
            raise ValueError('Choose valid version templates.')
        chosen = {t['key']: t['template_id'] for t in templates}
        with self.rosters.lock(), connect(self.path) as db:
            setup = self.template_setup(db, aid)
            if not setup['slots']:
                raise ValueError('Add the assignment PDF in Create first.')
            versions, names = [], set()
            for slot in setup['slots']:
                template = next((t for t in slot['options'] if t['id'] == chosen.get(slot['key'])), None)
                if not template:
                    raise ValueError(f"Choose or upload a blank template for {slot['label']} first.")
                name = slot['label'] if slot['label'] not in names else f"{slot['label']} ({len(names) + 1})"
                names.add(name)
                versions.append({'name': name, 'key': slot['key'], 'document_id': slot['document_id'], 'exam_version': slot['exam_version'], 'label': slot['label'],
                                 'template_id': template['id'], 'manifest_sha256': template['manifest_sha256'], 'template_label': template['label'], 'pages': template['page_count']})
            if any(v['pages'] > MAX_PAGES for v in versions) or sum(v['pages'] for v in versions) > ALL_PAGES:
                raise ValueError(f'Templates of more than {MAX_PAGES} pages each, or {ALL_PAGES} together, are too long to draft rubrics from in one go.')
            endpoint, settings = self._drafting_model()
            # The mode and every version's template belong to this term-owned generation, not to the shared templates or assignment.
            context = {'rule': VERSIONS_RULE, 'kind': 'versions', 'mode': mode, 'versions': versions}
            if choices:  # RUBRIC CHOICES (2026-10-24): named only where some were chosen, so other requests keep their fingerprints
                context['rubric_choices'] = choices
            checksum = digest([aid, context, endpoint])
            prior = db.execute('SELECT * FROM frq_rubric_generations WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another rubric draft.')
                return self.rubric_generation_summary(db, prior)
            first = versions[0]
            if 'rubric_choices' in data and rubric_choices.record(db, aid, choices, 'workspace'):
                self.event(db, aid, 'rubric_choices_saved', {'choices': choices})
            summary = self._record_generation(db, aid, request_id, checksum, {'document_id': first['document_id'], 'exam_version': ALL, 'label': 'every version'},
                                              {'id': first['template_id']}, context, endpoint, settings, 'rubric_generation_queued',
                                              {'mode': mode, 'versions': [v['name'] for v in versions]})
        self.inbox.wake.set()
        return summary

    def _page_text(self, template_id, pages):
        found = []
        for page in range(1, pages + 1):
            try:
                words = self.templates.read(template_id, 'text', page)[0].decode('utf-8', 'replace').strip()
            except (KeyError, NotFound):
                words = ''
            if words:
                found.append({'page': page, 'text': words[:PAGE_TEXT]})
        return found

    def _draft_versions(self, task, context, endpoint, settings):
        """Survey each version's template (the ordinary survey, side by side), group the problems across versions in one request
        (skipped for a single version), then write each problem for all its versions in one request. Every answer is a retained call of
        this generation, so a later turn after a wait or a restart sends only what is still missing."""
        mode = scoring_mode(context['mode'])
        versions = context['versions']
        names = [v['name'] for v in versions]
        try:
            pictures = {}
            for v in versions:
                if self.templates.get(v['template_id'])['page_count'] != v['pages']:
                    raise ValueError(f"The template of {v['name']} changed after this draft was requested. Draft again.")
                pictures[v['name']] = [self.templates.read(v['template_id'], 'image', page)[0] for page in range(1, v['pages'] + 1)]
                if sum(map(len, pictures[v['name']])) > IMAGES:
                    raise ValueError(f"The page pictures of {v['name']}'s template exceed 20 MiB and cannot be sent in one request.")
            surveyor = {**endpoint, 'max_tokens': max(endpoint['max_tokens'], SURVEY_TOKENS)}
            definition, assignment = self._course_of(task)

            def survey(indexed):
                index, v = indexed
                return self._ask(task, surveyor, settings, SURVEY_EACH + str(index), 'rubric_survey',
                                 {'assignment': v['label'], 'pages': v['pages'], 'page_text': self._page_text(v['template_id'], v['pages'])},
                                 pictures[v['name']], lambda output: survey_problems(output, v['pages']),
                                 rubric_tools(self, endpoint, definition, {v['name']: pictures[v['name']]}, survey=True))
            with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(versions)))) as pool:
                surveys = list(pool.map(survey, enumerate(versions)))
            notes, surveyed, receipts = [], {}, {}
            for v, ((problems, found), receipt) in zip(versions, surveys):
                surveyed[v['name']], receipts[v['name']] = problems, receipt
                notes += [f"{v['name']}: {note}" if len(versions) > 1 else note for note in found]
            grouping = None
            if len(versions) == 1:
                families = [{'id': f'P{index}', 'versions': [{'version': names[0], **problem}]} for index, problem in enumerate(surveyed[names[0]], 1)]
            else:
                payload = {'versions': [{'version': name, 'problems': [{k: problem[k] for k in ('number', 'label', 'points', 'parts', 'statement')}
                                                                        for problem in surveyed[name]]} for name in names]}
                (families, grouped), grouping = self._ask(task, endpoint, settings, GROUP, 'rubric_group_versions', payload, [],
                                                          lambda output: version_groups(output, surveyed))
                notes += grouped

            writer_tools = rubric_tools(self, endpoint, definition, pictures, assignment_id=assignment)
            standing = self._memory_rules(definition, task)
            chosen = rubric_choices.stated(context.get('rubric_choices'))

            def one(family):
                shown, images = [], []
                for member in family['versions']:
                    for page in member['pages'][:3]:
                        image = pictures[member['version']][page - 1]
                        if len(images) < WRITE_IMAGES and sum(map(len, images)) + len(image) <= IMAGES:
                            shown.append({'picture': len(images), 'version': member['version'], 'page': page})
                            images.append(image)
                payload = {'mode': mode, 'pictures': shown, 'problem': {'versions': [
                    {k: member[k] for k in ('version', 'number', 'label', 'points', 'points_text', 'parts', 'statement')} for member in family['versions']]},
                    **({'standing_rules': standing} if standing else {}), **({'assignment_choices': chosen} if chosen else {})}
                try:
                    drafted, receipt = self._ask(task, endpoint, settings, WRITE_ALL + family['id'], 'rubric_write_versions', payload, images,
                                                 lambda output: family_rubric(output, family, mode, names), writer_tools)
                    return family, drafted, receipt, None, None
                except RetryLater as waiting:
                    return family, None, None, waiting.seconds, None
                except (ProviderError, ValueError, TypeError, KeyError) as failure:
                    return family, None, None, None, str(failure)
            with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(families)))) as pool:
                outcomes = list(pool.map(one, families))
            waits = [seconds for *_, seconds, _ in outcomes if seconds is not None]
            if waits:
                return 'pending', None, None, '', later(min(waits))
            problems = [drafted for _, drafted, _, _, failure in outcomes if not failure]
            failed = [{'id': family['id'], 'label': ' · '.join(f"{m['version']} {m['title']}" for m in family['versions']), 'error': failure}
                      for family, *_, failure in outcomes if failure]
            count = len(versions)
            result = {'rule': context['rule'], 'kind': 'versions', 'mode': mode, 'versions': [{k: v[k] for k in ('name', 'key', 'label')} for v in versions],
                      'problems': problems, 'failed': failed, 'notes': notes,
                      'summary': f"{len(problems)} of {len(families)} problems drafted for {count} version{'' if count == 1 else 's'} at once."
                                 + (f" {len(failed)} could not be drafted; draft again or upload their Gradescope CSVs." if failed else '')}
            metadata = {'surveys': receipts, 'grouping': grouping, 'problems': {family['id']: receipt for family, _, receipt, _, _ in outcomes if receipt}}
            if not problems:
                return 'failed', None, metadata, 'No problem could be drafted. ' + ' '.join(f"{f['label']}: {f['error']}" for f in failed[:3]), ''
            return 'complete', result, metadata, '', ''
        except RetryLater as waiting:
            return 'pending', None, None, '', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound) as failure:
            return 'failed', None, None, f'The rubrics could not be drafted: {failure}', ''

    def _accept_versions(self, aid, gid, row, chosen, request_id):
        """The instructor's decision on a draft of every version: per problem, the shared lines as edited (points shared, wording per
        version) and, per version, whether to create it, its number, title, statement and solution. Every created version of a problem
        takes the same line IDs (R1, R2 ... by line) and points. Each version is created independently: one refused never hides another."""
        context, model = json.loads(row['input_json']), json.loads(row['profile_json'])['endpoint']
        mode = scoring_mode(context['mode'])
        negative = mode == 'negative'
        drafted = {p['id']: p for p in json.loads(row['result_json'])['problems']}
        slots = {v['name']: (index, v) for index, v in enumerate(context['versions'])}
        results, seen, made = [], set(), {}
        for entry in chosen:
            draft = drafted.get(entry.get('draft')) if isinstance(entry, dict) else None
            if not draft or draft['id'] in seen:
                raise ValueError('Each accepted problem must name one drafted problem once.')
            seen.add(draft['id'])
            members = {m['version']: m for m in draft['versions']}
            try:
                if entry.get('mode', mode) != mode:
                    raise ValueError('The scoring mode must match the saved draft. Generate a new draft to change scoring.')
                maximum = number(entry.get('max_points'), 'Question points', 0, 1000)
                if not maximum:
                    raise ValueError('A question must be worth some points.')
                shared = []
                for index, line in enumerate(items(entry.get('items'), 'Rubric lines', MAX_LINES), 1):
                    if not isinstance(line, dict) or not isinstance(line.get('descriptions'), dict):
                        raise ValueError('Invalid rubric line.')
                    shared.append({'id': f'R{index}', 'descriptions': line['descriptions'], **({'link': line['link']} if line.get('link') in LINK_KINDS else {}),
                                   'clarifications': line['clarifications'] if isinstance(line.get('clarifications'), dict) else {},
                                   'points': number(line.get('points'), 'Line points', -maximum if negative else 0, 0 if negative else maximum)})
                versions = items(entry.get('versions'), 'Versions', len(members))
            except (ValueError, Conflict) as failure:
                results.append({'draft': draft['id'], 'status': 'failed', 'error': getattr(failure, 'description', None) or str(failure)})
                continue
            for picked in versions:
                name = picked.get('version') if isinstance(picked, dict) else None
                if not isinstance(name, str) or name not in members or name not in slots:
                    raise ValueError('Each accepted version must be one this problem was drafted for.')
                replace = picked.get('replace') is True
                if picked.get('keep') is not True and not replace:
                    continue
                member, (position, slot) = members[name], slots[name]
                try:
                    if replace and picked.get('keep') is True:
                        raise ValueError('Create a new question or replace the existing rubric, not both.')
                    printed = locations.printed_number(str(picked.get('printed_number', '')))
                    if not printed:
                        raise ValueError('Give the printed question number.')
                    lines = [{'id': line['id'], 'description': text(line['descriptions'].get(name) or '', 'Rubric line', 1000).strip(), 'points': line['points']} for line in shared]
                    lines = [line for line in lines if line['description']]  # A line cleared for this version is left out; the others keep their IDs.
                    if not lines:
                        raise ValueError('Keep at least one rubric line.')
                    kept = {line['id'] for line in lines}
                    notes = {line['id']: note for line in shared if line['id'] in kept
                             and (note := text(line['clarifications'].get(name) or '', 'Clarification', CLARIFICATION).strip())}
                    statement = text(picked.get('statement', ''), 'Problem statement', 20000).strip()
                    named = text(picked.get('title', ''), 'Question title', 200).strip() or member.get('title') or f'Question {printed}'
                    solution = text(picked.get('solution', ''), 'Worked solution', 20000).strip()
                    warnings = [CREATED_TOGETHER]
                    rows = [dict(line) for line in lines]
                    rubrics.wording_rules(rows, warnings)
                    worths = rubrics.part_worths(member.get('parts'), draft.get('part_points'))
                    rules = rubrics.validate(rubrics.capped_parts({'mode': mode, 'items': rows, 'warnings': warnings}, worths, maximum), maximum)
                    edited = (printed, named, maximum, [(l['id'], l['description'], l['points']) for l in lines], statement, solution) != (
                        member['number'], member.get('title') or f"Question {member['number']}", draft['points'],
                        [(f'R{i}', l['descriptions'][name], l['points']) for i, l in enumerate(draft['items'], 1)], member['statement'], member['solution'])
                    source = json.dumps({'mode': mode, 'items': lines, 'notes': f"Drafted by {model['name']} ({model['model']}) for every version at once, problem {draft['id']} "
                                         f"({name}, printed {member['number']}) from {slot['template_label']}, and accepted by the instructor{' after editing' if edited else ''}"
                                         f"{' in place of the existing question’s rubric' if replace else ''}."},
                                        indent=2, ensure_ascii=False)
                    letter = slot['exam_version']
                    provenance = {'name': VERSIONS_COMPILER, 'rule': context['rule'], 'mode': mode, 'generation_id': gid, 'problem': f"{draft['id']}:{name}",
                                  'family': draft['id'], 'version': name, 'template_id': slot['template_id'], 'model': model['name'], 'requested_model': model['model'], 'edited': edited,
                                  **({'line_kinds': kinds} if (kinds := line_kinds(shared)) else {})}
                    detail = {'generation_id': gid, 'mode': mode, 'edited': edited, 'family': draft['id'], 'version': name}
                    if replace:
                        results.append({'draft': draft['id'], 'version': name, **self._replace_with_draft(aid, document_id=slot['document_id'], exam_version=letter,
                            printed=printed, rules=rules, source=source, maximum=maximum, seen=picked.get('replaces'), provenance=provenance,
                            request_id=f"{request_id}:{draft['id']}:{position}:replace", detail=detail, notes=notes)})
                        continue
                    created = self.create_draft_question(aid, filename=f"drafted-question-{printed}{'-' + letter if letter else ''}.json",
                        content=source.encode(), source=source, rules=rules, max_points=maximum, printed=printed, exam_version=letter,
                        key=f"q{printed}" + (f"-{letter}" if letter else ''), document_id=slot['document_id'], request_id=f"{request_id}:{draft['id']}:{position}", title=named,
                        compiler=provenance, event='rubric_draft_accepted', statement=statement, solution=solution, detail=detail)
                    created['clarifications_saved'] = self._save_drafted_clarifications(aid, created, notes)
                    made[created['question_id']] = notes
                    results.append({'draft': draft['id'], 'version': name, **created})
                except (ValueError, Conflict) as failure:
                    results.append({'draft': draft['id'], 'version': name, 'status': 'failed', 'error': getattr(failure, 'description', None) or str(failure)})
        # A rubric this acceptance REPLACED is never among `made`: its confirmation for review is asked again, as after any rubric change.
        return {'results': results, **self._after_acceptance(aid, results, made), 'generation': self.rubric_generation(aid, gid)}

    def _replace_with_draft(self, aid, *, document_id, exam_version, printed, rules, source, maximum, seen, provenance, request_id, detail, notes=None):
        """An accepted draft given to a question that ALREADY exists, in place of its rubric (requirement, 2026-09-27). Only when the
        instructor ticked that version's own box (never all at once), and only the rubric changes: a NEW immutable revision of the
        existing question, as the line editor and Import revision make one. The question, its statement, solution and answer key stay.

        `seen` is the rubric revision the instructor saw when opening the review: if the question's rubric changed since, nothing is
        replaced. As after any rubric change, the question's earlier grades are stale and wait to be graded again, its finalization
        and its confirmation for review are asked for again, and where its finalization stood its automatic acceptance is suspended
        (this question only). Its line comments are cleared (append-only, kept in the history): they were written about lines of the
        old rubric, and the new lines reuse their IDs. The draft's own clarifications (`notes`) then become the new lines' comments. A confirmed answer area is carried by an append-only receipt, as the line editor
        carries it: the draft must be worth the question's points, so the template, pages, areas and maximum are the same. The draft's
        source is retained through Inbox storage like a created question's, with an upload receipt keyed by the request, so a repeated
        request replaces nothing twice. An identical rubric makes no revision."""
        filename = f"drafted-rubric-{printed}{'-' + exam_version if exam_version else ''}.json"
        content = source.encode()
        checksum = digest({'replace': True, 'request': request_id, 'source_sha256': digest(content)})
        place = f"{'Version ' + exam_version if exam_version else 'this document'}"
        with self.rosters.lock():
            with connect(self.path) as db:
                receipt = db.execute('SELECT * FROM frq_rubric_uploads WHERE assignment_id=? AND request_id=?', (aid, request_id)).fetchone()
                if receipt:
                    if receipt['input_sha256'] != checksum:
                        raise Conflict('This request already replaced a rubric with different lines. Open the review again.')
                    return {'status': 'already_replaced', 'question_id': receipt['question_id'], 'rubric_id': receipt['rubric_id'], 'printed_number': printed}
                question = None
                for row in db.execute('SELECT p.question_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id '
                                      'WHERE p.assignment_id=? AND q.assignment_version_id=? AND q.exam_version=?', (aid, document_id, exam_version)):
                    candidate = self.question(db, aid, row[0])
                    if self.location_context(db, aid, candidate)['printed_number'] == printed:
                        question = candidate
                        break
                if not question:
                    raise ValueError(f'No question {printed} exists for {place}, so there is no rubric to replace.')
                qid, before = question['question_id'], question['rubric']
                if not before or before['id'] != seen:
                    raise Conflict(f"The rubric of question {printed} for {place} changed since you opened this review. Open it again to replace the current one.")
                if float(question['content']['max_points']) != float(maximum):
                    raise ValueError(f"Question {printed} for {place} is worth {question['content']['max_points']:g} points; set this draft to the same points to replace its rubric.")
                # A part's cap (GRADING "PART CAPS") is part of the shape, so a draft that only adds caps replaces the rubric.
                shape = lambda value: (value.get('mode'), [(i['id'], i['description'], float(i['points'])) for i in value['items']],
                                       sorted((g['id'], float(g['floor'])) for g in value.get('groups') or [] if rubrics.part_cap(g['id'])),
                                       [(i['id'], i['group']) for i in value['items'] if rubrics.part_cap(i.get('group'))])
                if shape(before['rules']) == shape(rules):
                    return {'status': 'unchanged', 'question_id': qid, 'rubric_id': before['id'], 'printed_number': printed}
            retained = self.upload_source(aid, filename, content, 'rubric-replace:' + digest({'assignment': aid, 'request': request_id}))
            artifact = retained['source_artifact_id']
            with connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                question = self.question(db, aid, qid)
                if not question['rubric'] or question['rubric']['id'] != before['id']:
                    raise Conflict(f"The rubric of question {printed} for {place} changed while replacing it. Open the review again.")
                if self._stood(db, aid, qid, before['id']):
                    self.suspend_acceptance(db, aid, 'A drafted rubric replaced one the rubric finalization confirmed.', question_ids=[qid])  # only this question's
                rid, now = uid(), timestamp()
                compiler = {'name': REPLACE_COMPILER, 'replaces': before['id'], 'based_on': provenance}
                self.insert_rubric_revision(db, qid, rid, source, artifact, rules, maximum, compiler, now)
                db.execute('UPDATE frq_assignment_questions SET rubric_id=?,version=version+1 WHERE assignment_id=? AND question_id=?', (rid, aid, qid))
                located = self.carry_location(db, aid, qid, question['question_revision_id'], before['id'],
                    'A drafted rubric of the same points replaced the rubric; the template, pages, areas and the question’s maximum are the same.', to_rubric=rid, now=now)
                cleared = sorted(self.line_comments(db, aid, qid).get(qid, {}))
                for iid in cleared:
                    db.execute('INSERT INTO frq_rubric_line_comments VALUES(?,?,?,?,?,?,?,?)', (uid(), aid, qid, iid, rid, '', 'workspace', now))
                ids = {item['id'] for item in rules['items']}
                drafted = {iid: note for iid, note in sorted((notes or {}).items()) if iid in ids}
                for iid, note in drafted.items():
                    db.execute('INSERT INTO frq_rubric_line_comments VALUES(?,?,?,?,?,?,?,?)', (uid(), aid, qid, iid, rid, note, 'workspace', now))
                db.execute('INSERT INTO frq_rubric_uploads VALUES(?,?,?,?,?,?,?)', (aid, request_id, checksum, qid, rid, artifact, now))
                self.touch(db, aid)
                self.event(db, aid, 'rubric_draft_replaced', {'question_id': qid, 'rubric_id': rid, 'replaces': before['id'], 'source_artifact_id': artifact,
                    'printed_number': printed, **({'exam_version': exam_version} if exam_version else {}), 'location_carried_forward': bool(located),
                    'comments_cleared': cleared, 'clarifications_saved': sorted(drafted), **detail})
        self.inbox.wake.set()
        return {'status': 'replaced', 'question_id': qid, 'rubric_id': rid, 'replaces': before['id'], 'printed_number': printed,
                'exam_version': exam_version, 'max_points': maximum, 'clarifications_saved': len(drafted)}
