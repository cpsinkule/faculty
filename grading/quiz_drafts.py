"""Quizzes drafted by a model from the instructor's material, revised on request, and approved into print and grading.

Asked, they chose (same day): the Rubric
drafting and review model writes quizzes too; the earlier quizzes' LaTeX look is rebuilt from their PDFs; the versions are printed
UNNAMED (no student; the name is read from the scanned paper; and since 2026-10-01 nothing printed shows the version); and approving a quiz also
confirms its templates, its answer areas (known exactly from the LaTeX layout) and that its rubrics are ready for the rubric review.

A draft is COURSE-WIDE authoring (`quiz_drafts`, owned by the course definition, with the term whose sections it is for): the
instructor's request and material, kept in the definition's verified storage. Each model run is an immutable REVISION: the writer
returns every version of every problem at once as structured data (`quiz_latex.clean`), Faculty writes and compiles each version's
LaTeX in the sandbox (`quiz_latex.render`, `compiler.compile_sources`), sends compile and layout faults back to the writer to fix, shows
the printed pages to a check that reports what to fix (one round), then drafts each problem's rubric for all its versions at once with
the department's rubric rules (`quiz_rubric`, validated by `rubric_generation.family_rubric`), then reads each problem's answer key
on each version ONCE, with the worked solution in view (`answer_key_read`, requirement, 2026-10-11), and last runs the rubric check Key & Rubric Finalization would run once the quiz is approved, on what approving
would create (requirement, 2026-10-11). Asking for changes is a new revision
based on the one shown, which carries each unchanged problem's rubric with its keys and its rubric check, and asks the page check
only about the problems not printed exactly as that revision's check saw them (2026-10-11, `printed_keys`). Answering the rubric
check's decisions is a revision too, one that runs only the reviewer's Resolve and changes nothing of the quiz
(`answer_quiz_rubric_check`). Nothing is created or printed by a revision.

APPROVING a revision (`quiz_draft_approvals`, the instructor's decision for the draft's term) is carried out by the worker step by
step, every step reusing what an earlier attempt made: the course-wide assignment and one printed version per quiz version (labels
A, B ...), the term's grading plan with every version's questions and rubrics (the drafted clarifications as line comments), the
templates imported from those very PDFs and confirmed, each question's answer area from where its problem was printed, confirmed,
each rubric confirmed for the rubric review (the mandatory Key & Rubric Finalization still follows and waits for the instructor), and
one unnamed print job per version for the term, with no caption, attached to the term's exam record. An approval sent from the page
that showed the keys (`save_keys`) saves each question's key with it: that approval is the instructor's save of the key they saw.
Where the created questions are proved to be what the revision's rubric check read, that check is recorded as the plan's completed
check in place of a new request (`_carry_drafted_check`), and an approval whose one box was ticked (`finalization`) also records the
ordinary confirmation of it (`_finalize_with_approval`); anything less queues the plan's own check and waits, as before.
"""
import copy
import difflib
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import PurePosixPath

from werkzeug.exceptions import Conflict, NotFound

from course_storage import StorageError, timestamp
from rosters import connect
from seating import term_label
from document_identity import accessible_quiz, quiz_latex
from document_identity.compiler import CompileError

from . import answers, config, locations, participation, rubric_choices, rubrics, quiz_blueprints
from .cloud import RetryLater, later
from .drafting_tools import bank_tool, chosen_bank, chosen_notes, covered_sections, notes_tool, quiz_notes, rubric_tools
from .models import digest, encoded, identifier, items, number, text, uid
from .providers.base import Cancelled, ProviderError
from .rubric_generation import KEY_RULE, family_rubric, key_request, leftover_warning, line_kinds, read_key, scoring_mode
from .rubric_quality import (BUILTIN, CROPS, PARALLEL, VERSIONS_SCHEME, CheckHost, answered_concerns, builtin_exclusions, concern_list,
                             normalize_questions, resolve_links, resolves_input, review_budget)

LOG = logging.getLogger(__name__)

RULE = 'quiz-draft-v1'
COMPILER = 'model-drafted-quiz-v1'
GENERATOR = 'faculty-quiz-draft'
REBUILD_RULE = 'quiz-rebuild-v1'
# The plan events an approval, its automatic follow-ups and a rebuild write. Any other kind is the instructor's own work on the questions a
# rebuild takes off the plan, so the rebuild refuses (QUIZZES "REBUILDING AN APPROVED QUIZ").
REBUILD_AUTOMATIC = frozenset({'assignment_created', 'quiz_question_created', 'rubric_draft_clarifications_saved', 'template_selected',
                               'assignment_templates_confirmed', 'template_locations_confirmed', 'rubric_review_confirmed', 'rubric_quality_queued',
                               'rubric_quality_ready', 'rubric_quality_failed', 'answer_key_reading_queued', 'rubric_generation_ready',
                               'rubric_generation_failed', 'rubric_quality_cancelled', 'rubric_generation_cancelled', 'quiz_rebuild_questions_withdrawn'})
# What the ordinary confirmation writes (`rubric_quality._record_confirmation`). Never automatic by kind: only the events of the one
# confirmation an approval itself recorded on the instructor's tick are the approval's own (`_quiz_rebuild_refusal`).
APPROVAL_CONFIRMATION = ('rubric_quality_confirmed', 'rubric_approved')
WRITE, FIX, CHECK, RUBRIC = 'quiz:write', 'quiz:fix:', 'quiz:check', 'quiz:rubric:P'
KEY = 'quiz:key:P'       # one answer-key reading per problem and version: quiz:key:P<number>:<letter>
KINDS = {'.pdf': 'pdf', '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.txt': 'text', '.md': 'text', '.tex': 'text'}
MAX_FILES = 12
MAX_FILE = 25 * 1024**2
MAX_MATERIAL = 60 * 1024**2
MAX_VERSIONS = 8
MAX_INSTRUCTIONS = 8000
MAX_FORMAT = 1000
MATERIAL_TEXT = 150000   # characters of material sent with the writer's request
PAGE_TEXT = 6000         # characters kept of one material page
SCANNED = 400            # a material page with less text than this is sent as a picture (a scan, handwriting, a figure)
PICTURES = 20            # pictures in the writer's request (material pages, example quiz pages)
PICTURE_BYTES = 14 * 1024**2
FIX_ROUNDS = 2           # compile or layout faults sent back to the writer before a revision gives up
WRITE_TOKENS = 32768
EXAMPLES = 2
MAX_NOTES_SECTIONS = 60    # lecture-notes sections chosen for one draft (the MATH 140 notes have 29)
DIRECTIONS = ('PRINT your name at the top and enter your UIN. Notes are NOT allowed, but TI-83 and TI-84 calculators (or other approved '
              'calculators) are allowed. Circle one answer for each multiple-choice problem. For free-response problems, show all work and '
              'box your final answer. Partial credit will be given for appropriate work shown, and answers without sufficient work will '
              'not receive full credit.')
CREATED_BY = ('Drafted by a model with the quiz itself, for every version at once, and approved by the instructor with the quiz; its lines '
              'match the other versions\' line for line. Check the lines, their points and which of them are alternatives in Key & Rubric '
              'Finalization before grading.')
LAYOUT_READER = 'faculty-quiz-layout-v1'
# The format a new draft starts from, the instructor's current quizzes (2026-10-01); they edit or clear it with each request.
FORMAT = 'One multiple-choice problem and three free-response problems.'


def letters(count):
    if type(count) is not int or not 1 <= count <= MAX_VERSIONS:
        raise ValueError(f'A quiz has 1 to {MAX_VERSIONS} versions.')
    return list('ABCDEFGH'[:count])


def safe_name(name):
    name = PurePosixPath(str(name or '')).name.strip()
    if not name or len(name) > 120 or name.startswith('.') or any(c in name for c in '\\\x00/') or PurePosixPath(name).suffix.lower() not in KINDS:
        raise ValueError('Material files are PDF, PNG, JPEG, plain text, Markdown or LaTeX, with ordinary names.')
    return re.sub(r'[^A-Za-z0-9._ -]', '_', name)


# Presentation only: space, line breaks with their room, math-mode delimiters, skips, struts and ties.
SPACING = re.compile(r"\\[vh]space\*?\s*\{[^{}]*\}|\\\\\s*\[[^\]]*\]|\\q?quad\b|\\(?:small|med|big)skip\b|\\rule\s*\{[^{}]*\}\s*\{[^{}]*\}"
                     r"|\\\[|\\\]|(?<!\\)\$|\\[,;: ]|~|\s+")


def content_only(value):
    """A fragment without its spacing: moving a blank or a line break changes nothing a rubric depends on."""
    return " ".join(SPACING.sub(" ", value).split())


def signature(problem, mode):
    """What a problem's rubric depends on, its scoring included: a revision carries the earlier rubric only while this is unchanged. Its
    words, parts, options and final answers count; spacing does not, nor its figure's drawing, whose data are the statement's and whose
    results are the final answers (a graph redrawn smaller for room kept every rubric of the first real revision from being carried)."""
    return digest({'mode': mode, 'kind': problem['kind'], 'points': problem['points'], 'parts': problem['parts'],
                   'content': {letter: {'statement': content_only(c['statement']), 'left': content_only(c['left']), 'scaffold': content_only(c['scaffold']),
                                        'parts': [{**part, 'text': content_only(part['text'])} for part in c['parts']],
                                        **{k: c[k] for k in ('choices', 'correct_choice', 'final_answers')}}
                               for letter, c in problem['content'].items()}})


# What a check of the printed pages was shown (requirement, 2026-10-11, among the small repeats they chose to remove: the quiz check skips problems unchanged
# since the last revision). Another rule here, or another quiz_check prompt, is another key: what was seen under the
# earlier one is asked again.
CHECK_SEEN = 'quiz-check-seen-v1'
CHECK_EXAMPLES = 4       # pages of the past quizzes sent to the check after the drafted pages


def printed_keys(request, quiz, built, examples):
    """({problem number: its `printed_key`}, the whole quiz's key): what the check of the printed pages is shown of each problem and
    judges it against. `signature` is not enough for the check: it leaves out spacing and figures, which the check judges (the layout,
    the room to write, whether a problem fits). A problem's key holds its fragments exactly as written on every version, with their
    solution, final answers and correct choice; its number, kind, tag, points, parts, page and space; the heading printed above it and
    the problems that share its page; where each version's compile put it (its box, the room left to write, its area down to the next
    problem); and the frame every problem is printed in and judged against (what the instructor asked for, the title, sections line,
    directions and points, each version's page count, Faculty's class and style, the example pages, the check's own prompt). Two
    revisions with the same key show the check the same thing for that problem; the whole quiz's key is the frame with every problem's
    key in order. `built`: the versions' compiles (`_quiz_build`); `examples`: the example pictures the check is sent."""
    from .providers.local_vlm import prompt
    names = quiz['versions']
    frame = digest({'rule': CHECK_SEEN, 'prompt': prompt('quiz_check')[1]['prompt_sha256'], 'request': request,
                    **{k: quiz[k] for k in ('title_line', 'sections_line', 'directions', 'total', 'versions')}, 'problems': len(quiz['problems']),
                    'printed': {letter: {'pages': built[letter]['pages'], 'style': digest(built[letter]['retained'][quiz_latex.STYLE]),
                                         'class': digest(built[letter]['retained']['facultyassignment.cls'])} for letter in names},
                    'examples': [digest(picture) for picture in examples]})
    placed = {letter: built[letter]['layout']['problems'] for letter in names}
    room = {letter: quiz_latex.rooms(placed[letter], quiz) for letter in names}
    area = {letter: quiz_latex.areas(placed[letter]) for letter in names}
    keys, previous = {}, None
    for problem in quiz['problems']:
        number_ = problem['number']
        # The section heading above it, as `quiz_latex.render` prints one: over a page's first problem and where the kind changes.
        heading = ''
        if previous is None or previous['page'] != problem['page'] or previous['kind'] != problem['kind']:
            heading = quiz_latex.HEADINGS[problem['kind']] + (' (continued)' if previous is not None and previous['kind'] == problem['kind'] else '')
        keys[number_] = digest({'frame': frame, **{k: problem[k] for k in ('number', 'kind', 'tag', 'points', 'parts', 'page', 'space')}, 'heading': heading,
                                'page_holds': [p['number'] for p in quiz['problems'] if p['page'] == problem['page']],
                                'versions': {letter: {'content': problem['content'][letter], 'box': placed[letter].get(number_),
                                                      'room': room[letter].get(number_), 'area': area[letter].get(number_)} for letter in names}})
        previous = problem
    return keys, digest({'frame': frame, 'problems': [keys[problem['number']] for problem in quiz['problems']]})


def check_record(check, fixed, shown, final):
    """What a revision keeps of its check: its findings (those carried from an earlier check among them), whether the writer's fix
    held, the problems it did not ask about again (`unchanged`, named only when there are any), and `seen`: the keys a check actually
    SAW (`printed_keys`), with the findings about them that still stand. `shown` is the quiz as checked, `final` the quiz the revision
    ends with: the two differ after a fix, which is never checked again, so a problem the fix changed (or moved) has no seen key and
    its finding is not kept as standing, and what concerned the whole quiz (problem 0) stands only while the whole quiz is as checked.
    A later revision asks only about what is not in `seen` (`_quiz_checked`)."""
    (keys, whole), (now, now_whole) = shown, final
    same = {str(number_): key for number_, key in now.items() if keys.get(number_) == key}
    return {'issues': check['issues'], 'notes': check['notes'], 'fixed': fixed, **({'unchanged': check['unchanged']} if check['unchanged'] else {}),
            'seen': {'rule': CHECK_SEEN, 'problems': same, 'whole': whole if now_whole == whole else '',
                     'issues': [i for i in check['issues'] if (str(i['problem']) in same if i['problem'] else now_whole == whole)]}}


def asked_changes(db, revision):
    """Every change the instructor requested on the way to this revision, oldest first (QUIZZES "THE CHECK KEEPS WHAT WAS ASKED"; a
    revision with none, or one that only sent rubric answers, adds nothing)."""
    asked = []
    while revision:
        feedback = json.loads(revision['input_json']).get('feedback')
        if feedback:
            asked.insert(0, feedback)
        revision = db.execute('SELECT input_json, based_on FROM quiz_draft_revisions WHERE id=?', (revision['based_on'],)).fetchone() if revision['based_on'] else None
    return asked


def fix_request(request, quiz, faults, asked):
    """What a fix round is sent: what it must fix, and what it must keep (`asked_changes`)."""
    return {'request': request, 'previous': written(quiz), 'faults': faults, **({'asked': asked} if asked else {})}


def after_fix(extra, fixed):
    """What a revision says it changed: the writer's account, then each fix's, since a fix may change what that account says."""
    return '\n\n'.join(filter(None, (extra.get('changes'), fixed.get('changes'))))


def compile_report(quiz, built, faults):
    """What the writer's compile_quiz tool answers: per version its pages, where each problem landed, the room left to write in each
    free-response problem, the layout's own notes, then every fault Faculty would send back."""
    lines = []
    for letter in quiz['versions']:
        build = built.get(letter)
        if not build:
            lines.append(f'Version {letter}: not compiled (see its faults below).')
            continue
        lines.append(f"Version {letter}: {build['pages']} page{'s' if build['pages'] != 1 else ''}.")
        placed, room = build['layout']['problems'], quiz_latex.rooms(build['layout']['problems'], quiz)
        for problem in quiz['problems']:
            here = placed.get(problem['number'])
            if here:
                lines.append(f"  Problem {problem['number']} ({problem['kind'].replace('_', ' ')}, planned for page {problem['page']}): page {here['page']}, "
                             f"from {here['top']:.0%} to {here['bottom']:.0%} down the page"
                             + (f"; {room[problem['number']] / quiz_latex.MM / 10:.1f} cm left to write in" if problem['number'] in room else '') + '.')
        lines += [f'  Note: {note}' for note in build['layout']['notes']]
    if faults:
        lines += ['', 'Faults to fix before you answer:', *(f'- {fault}' for fault in faults)]
    else:
        lines += ['', 'No faults: every version compiles and fits its planned pages. Look at the pages (look_at_pages) to check figures, '
                      'spacing and room to write.']
    return '\n'.join(lines)


def written(quiz):
    """A clean quiz back in the writer's own shape, for a revision or a fix to start from."""
    return {'sections_line': quiz['sections_line'], 'directions': quiz['directions'], 'problems': [
        {'kind': p['kind'], 'tag': p['tag'], 'points': p['points'], 'page': p['page'], 'space': p['space'], 'parts': p['parts'],
         'versions': [{'version': letter, **{k: c[k] for k in ('statement', 'left', 'choices', 'correct_choice', 'figure', 'figure_side',
                                                               'scaffold', 'solution', 'final_answers')},
                       'parts': [{'label': part['label'], 'text': part['text']} for part in c['parts']]} for letter, c in p['content'].items()]}
        for p in quiz['problems']]}


BASES = ('none', 'previous', 'compiled')


def resolved(answer, previous, compiled, letters):
    """The quiz an answer (or a compile_quiz call) stands for, in the writer's own shape: the whole quiz it gives (`base` "none"), or
    the quiz it starts from, the request's `previous` or the writer's last successful compile in this request (`compiled`), with its
    `edits` applied in order. Each edit names a problem and replaces one version's whole content and/or sets the problem's page, space,
    tag or rubric feedback (requirement, 2026-10-01: retyping every version made each writing step take minutes). `quiz_latex.clean`
    still checks the result."""
    if not isinstance(answer, dict):
        raise ValueError('The answer must be an object.')
    base = answer.get('base') or 'none'   # answers recorded before 2026-10-01 have no base: the whole quiz
    if base not in BASES:
        raise ValueError('base must be "none", "previous" or "compiled".')
    edits = answer.get('edits') or []
    if not isinstance(edits, list):
        raise ValueError('edits must be a list.')
    if base == 'none':
        if edits:
            raise ValueError('With base "none" give the whole quiz and no edits.')
        return {key: answer.get(key) for key in ('sections_line', 'directions', 'problems')}
    start = previous if base == 'previous' else compiled
    if start is None:
        raise ValueError('This request has no previous quiz: give the whole quiz with base "none".' if base == 'previous' else
                         'Nothing was compiled in this request: give the whole quiz with base "none".')
    if answer.get('problems'):
        raise ValueError(f'With base "{base}" give only edits: problems must be [].')
    quiz = copy.deepcopy(start)
    for key in ('sections_line', 'directions'):
        if isinstance(answer.get(key), str) and answer[key].strip():
            quiz[key] = answer[key]
    for number_, edit in enumerate(edits, 1):
        where = f'Edit {number_}'
        if not isinstance(edit, dict):
            raise ValueError(f'{where} must be an object.')
        index = edit.get('problem')
        if type(index) is not int or not 1 <= index <= len(quiz['problems']):
            raise ValueError(f"{where} must name a problem from 1 to {len(quiz['problems'])}.")
        problem, content, letter = quiz['problems'][index - 1], edit.get('content'), edit.get('version') or ''
        if content is not None:
            if letter not in letters or not isinstance(content, dict):
                raise ValueError(f"{where} gives content: name its version, one of {', '.join(letters)}, and give the content as an object.")
            spot = next((i for i, v in enumerate(problem.get('versions') or []) if isinstance(v, dict) and v.get('version') == letter), None)
            if spot is None:
                raise ValueError(f'{where}: problem {index} has no Version {letter} to replace.')
            problem['versions'][spot] = {**content, 'version': letter}
        elif letter:
            raise ValueError(f'{where} names Version {letter} but gives no content: give that version\'s whole content, or "" as its version.')
        for key in ('page', 'space', 'tag', 'rubric_feedback'):
            if edit.get(key) is not None:
                problem[key] = edit[key]
    return quiz


def compiled_from(metadata, previous, letters):
    """The quiz the writer's last successful compile_quiz compiled in one request, rebuilt from that call's receipt by replaying its
    compiles in order, so an answer with `base` "compiled" reads the same when a resumed revision replays the call. A compile counts
    when the tool answered (`ok`): the tool kept its quiz as the base only then. None when nothing was compiled."""
    compiled = None
    for call in ((metadata or {}).get('tools') or {}).get('calls') or []:
        if call.get('tool') != 'compile_quiz' or not call.get('ok'):
            continue
        arguments = call.get('arguments')
        if not isinstance(arguments, dict) or 'base' not in arguments:
            raise ValueError('Faculty did not keep your compiles whole: give the whole quiz with base "none".')
        compiled = resolved(arguments, previous, compiled, letters)
    return compiled


# The finding a makeup revision stored before 2026-11-05 (GRADING "LEFTOVER VALUES").
STORED_LEFTOVER = re.compile(r"word for word .+'s, though (?:it names|they name) values: check (?:it|them) for this version\.$")


def statement_text(content):
    """The problem as graders read it: the statement, what is printed under it, its parts and its options, in LaTeX."""
    lines = [content['statement']]
    if content['left']:
        lines.append(content['left'])
    lines += [f"({part['label']}) {part['text']}" for part in content['parts']]
    lines += [f"({quiz_latex.LETTERS[i].lower()}) {choice}" for i, choice in enumerate(content['choices'])]
    if content['scaffold']:
        lines.append(content['scaffold'])
    return '\n'.join(lines)


def version_rules(rubric, letter, mode, points, parts=()):
    """One version's lines of a problem's drafted family rubric (`family_rubric`: shared points, a wording per version) and the rules
    its question is created with. The approval (`_set_up_quiz_plan`) and the answer-key reading made while the quiz is drafted
    (`_quiz_keys`) both build them here, so the key is read against exactly the lines the question gets. A quiz prints every part's
    points (`parts`), and a deducting rubric's parts are capped at them (GRADING "PART CAPS"). Returns (lines, rules)."""
    lines = [{'id': f'R{i}', 'description': line['descriptions'][letter], 'points': line['points']} for i, line in enumerate(rubric['items'], 1)
             if line['descriptions'].get(letter)]
    warnings = [CREATED_BY]
    rows = [dict(line) for line in lines]
    rubrics.wording_rules(rows, warnings)
    return lines, rubrics.validate(rubrics.capped_parts({'mode': mode, 'items': rows, 'warnings': warnings}, rubrics.part_worths(parts), points), points)


def key_warnings(quiz, entries, names):
    """One warning for each problem and version whose answer key could not be read while the quiz was drafted (`_quiz_keys`): approving
    saves no key for it, and Key & Rubric Finalization still reads or takes one. Worded as the layout's notes are ("Version A: Problem
    2 ..."), so the draft's page marks it under its problem and says it once when it holds on every version."""
    found = []
    for problem in quiz['problems']:
        entry = next((e for e in entries if e['problem'] == problem['number']), None) or {}
        for letter in names:
            reading = (entry.get('keys') or {}).get(letter)
            if reading and not reading['key']:
                why = f"the model could not read one ({reading['unreadable']})" if reading['unreadable'] else f"reading it failed ({reading.get('error', '')})"
                found.append(f"Version {letter}: Problem {problem['number']} has no answer key: {why}. Approving saves none for it.")
    return found


# RUBRIC QUESTIONS AT DRAFT REVIEW (requirement, 2026-10-11, chosen from a measured list of what the rubric finalization repeats). Until
# then a drafted quiz was approved, THEN the rubric check ran on the questions the approval created, THEN in a second sitting the
# instructor answered its concerns, ran Resolve and ticked the one confirmation without which nothing is graded. Measured: Quiz 4
# needed 10 check runs; fresh checks of the same unchanged rubrics raised different concerns each time; every proposed clarification
# (700 in 8 confirmations) was confirmed as proposed; no Resolve round opened a concern on an untouched line. So the SAME check (its
# prompts, rules, schemes and links: `rubric_quality.run_rubric_check`) runs as a revision's last step on what approving WOULD create
# (`draft_check_context`), its concerns are answered in the draft's review (`answer_quiz_rubric_check`: a revision that runs only
# Resolve), and an approval records the check for the plan and, on ONE explicit tick, the ordinary confirmation
# (`_carry_drafted_check`, `_finalize_with_approval`). Wherever the reviewer cannot be PROVED to have read what a check of the created
# questions would send (`check_digests`), the approval queues the check exactly as it always did.
DRAFT_CHECK = 'quiz-draft-check-v1'
CHECK_STEPS = 'rubric_quality:'   # the check's own step names, here under the revision that owns its requests
CHECK_FAILED = 'The rubrics could not be checked while the quiz was drafted'


def virtual(number_, letter):
    """The question a problem will be on one version, named before any question exists: a draft's check names its questions so, and
    an approval renames them to the questions it created (`renamed`)."""
    return f'draft:{number_}:{letter}'


def drafted_comments(rubric, letter):
    """One version's drafted clarifications of a family rubric, by the line IDs its question gets (`version_rules`): what an approval
    saves as that question's line comments, and what the rubric check reads as the instructor's comments."""
    return {f'R{i}': line['clarifications'].get(letter, '') for i, line in enumerate(rubric['items'], 1)
            if line['descriptions'].get(letter) and line['clarifications'].get(letter)}


def draft_check_context(revision_id, quiz, entries, mode, names, versions):
    """What the plan's rubric check would read of this quiz once approved (`rubric_quality_context`, `rubric_check_input`), built from
    a revision alone: one question per problem and version under its `virtual` ID, with the rules its question is created with
    (`version_rules`, as the catalog keeps them), its built-in exclusions, the drafted clarifications as its comments, and the
    reference (the problem as written, the worked solution, the key read for it, where it is printed: `versions[letter]['areas']`,
    which approval confirms); with versions, the scheme, each problem as one family and the drafted links as candidates, exactly as a
    new plan's first check names them. The reviewer's payload holds no catalog ID (`question_payload`, `problem_payload`), so what
    it is sent from here is what a check queued after approval would send."""
    from . import rubric_links
    held, questions, kinds = {entry['problem']: entry for entry in entries}, [], {}
    for problem in quiz['problems']:
        entry = held[problem['number']]
        rubric = entry['rubric']
        for letter in names:
            content, qid = problem['content'][letter], virtual(problem['number'], letter)
            rules = json.loads(encoded(version_rules(rubric, letter, mode, problem['points'], problem.get('parts'))[1]))
            known = {item['id'] for item in rules['items']}
            comments = {iid: note for iid, note in drafted_comments(rubric, letter).items() if note.strip() and iid in known}
            member = next(v for v in rubric['versions'] if v['version'] == letter)
            key = answers.clean_key(((entry.get('keys') or {}).get(letter) or {}).get('key'))
            areas = locations.validate_regions(versions[letter]['areas'][str(problem['number'])], versions[letter]['page_count'])
            kinds[qid] = line_kinds(rubric['items'])
            questions.append({'question_id': qid, 'rubric_id': 'rubric:' + qid, 'exam_version': letter if len(names) > 1 else '', 'document_id': f'draft:{letter}',
                              'title': f"Question {problem['number']}", 'printed_number': str(problem['number']), 'max_points': problem['points'], 'rubric': rules,
                              'builtin': builtin_exclusions(rules, problem['points']), **({'comments': dict(sorted(comments.items()))} if comments else {}),
                              'reference': {'problem': statement_text(content), 'expected_answer': '', 'worked_solution': content['solution'] or member.get('solution', ''),
                                            'answer_key': [{'label': part['label'], 'accept': part['accept']} for part in key],
                                            'printed': [{'page': area['page'], 'region': area['region']} for area in areas][:4]},
                              'origins': [['family', revision_id, f"P{problem['number']}"]]})
    context = {'rules': BUILTIN, 'documents': [{'id': f'draft:{letter}', 'label': letter} for letter in names], 'versions': names if len(names) > 1 else [],
               'questions': questions}
    if len(names) > 1:
        families = [[virtual(problem['number'], letter) for letter in names] for problem in quiz['problems']]
        seeds = rubric_links.effective_sets([{**q, 'line_kinds': kinds[q['question_id']]} for q in questions], families, [], {})
        links = rubric_links.known_links(seeds, {}, questions)
        context.update(scheme=VERSIONS_SCHEME, **({'known_links': links} if links else {}))
    return json.loads(encoded(context))  # numbers and keys as the catalog would give them back


def check_digests(question, pdf):
    """What proves that a question of the plan is the one a draft's check read, without the catalog's identities: `content`, its part
    of what a confirmation covers (`rubric_quality.question_input` less the question, rubric and document IDs: version, title,
    printed number, points, rules, built-in exclusions, comments); `reference`, what the reviewer read beside it (problem, solution,
    answer key); `printed`, where it is printed and on which PDF (`pdf`: that version's SHA-256)."""
    reference = question.get('reference') or {}
    return {'content': digest({k: v for k, v in question.items() if k not in ('question_id', 'rubric_id', 'document_id', 'reference', 'origins')}),
            'reference': digest({k: reference.get(k) for k in ('problem', 'expected_answer', 'worked_solution', 'answer_key')}),
            'printed': digest({'pdf': pdf, 'areas': [{'page': area['page'], 'region': area['region']} for area in reference.get('printed') or []]})}


def decisions(rows):
    """How many decisions a list of findings is, as the decisions view shows them: one finding raised on several versions of a problem
    is one (its `shared` key), one policy question raised on several problems is one (its `topic`, ONE QUESTION ONCE, 2026-10-29),
    and a finding Faculty fixed (its `fix`, guidance or key: BOOKKEEPING IS FIXED, NOT ASKED) is none."""
    return len({('topic', row['topic']) if row.get('topic') else row.get('shared') or (n,) for n, row in enumerate(rows) if not row.get('fix')})


def check_counts(check):
    """A revision's rubric check in numbers, for its summary, the draft's page and the approval: the decisions still open, the
    questions whose lines still add up to more than they are worth (each keeps the finalization open), the clarifications and
    exclusions proposed, and the decisions already answered. {'status': 'failed'} for a check that could not be made."""
    if check.get('status') != 'complete':
        return {'status': 'failed'}
    result = check['result']
    return {'status': 'complete', 'decisions': decisions(concern_list(result)), 'overflow': len(result.get('unresolved') or []),
            'clarifications': sum(len(q['clarifications']) for q in result['questions']), 'exclusions': sum(len(q['pairs']) for q in result['questions']),
            'answered': decisions((result.get('resolution') or {}).get('resolved') or [])}


def check_sha(check):
    """The check a page shows, as one fingerprint: an answer to its decisions and an approval that confirms it name this, so each is
    about exactly the check the instructor read."""
    return digest({'questions': check['questions'], 'result': check['result']})


def check_part(result, ids):
    """What a check's result says of some of its questions (`ids`: whole problems): their entries, the findings and links that name
    only them, and what a resolution resolved or changed of them."""
    resolution = result.get('resolution') or {}
    named = lambda row: set(row.get('question_ids') or [row.get('question_id')])
    return {'rule': resolution.get('rule', ''), 'questions': [q for q in result.get('questions') or [] if q['question_id'] in ids],
            'suspected_errors': [issue for issue in result.get('suspected_errors') or [] if issue['question_id'] in ids],
            'consistency_issues': [issue for issue in result.get('consistency_issues') or [] if set(issue['question_ids']) <= ids],
            'line_links': [link for link in result.get('line_links') or [] if all(line['question_id'] in ids for line in link['lines'])],
            'resolved': [row for row in resolution.get('resolved') or [] if named(row) <= ids],
            'before': {qid: was for qid, was in (resolution.get('before') or {}).items() if qid in ids}}


def check_joined(fresh, kept, order, said):
    """A check's result with the problems it did not ask about again as an earlier revision's check left them (`kept`: `check_part`;
    their entries and links came along as the check's settled questions), in the quiz's order (`order`: {question: position}), and
    `said` as its summary. A resolution those problems had been through stays on the record beside them."""
    result = {k: v for k, v in fresh.items() if k != 'settled'}
    result['suspected_errors'] = sorted(kept['suspected_errors'] + list(fresh.get('suspected_errors') or []), key=lambda issue: order[issue['question_id']])
    if 'consistency_issues' in fresh or kept['consistency_issues']:
        result['consistency_issues'] = sorted(kept['consistency_issues'] + list(fresh.get('consistency_issues') or []), key=lambda issue: order[issue['question_ids'][0]])
    if kept['resolved'] or kept['before']:
        result['resolution'] = {'rule': kept['rule'], 'check_id': '', 'commented': [], 'asked': [], 'before': kept['before'], 'resolved': kept['resolved']}
    return {**result, 'summary': said}


def answered_comments(rubrics, concerns, resolved):
    """A draft's rubrics after an answers revision (requirement, 2026-10-29: Quiz 5's drafted R7 comment still said a wrong Part B
    value with no work was not charged after the instructor answered that it was, and the check asked twice for them to edit it).
    A drafted clarification is Faculty's own until the quiz is approved, so where the instructor answered a decision about a line and
    the reviewer then changed that line's clarification, the drafted one it had (what the check reads as its comment,
    `drafted_comments`) becomes the reviewer's new clarification; a line drafted without one gains none. Returns (rubrics,
    [{problem, version, item_id}] rewritten); untouched lines stay."""
    before = (resolved.get('resolution') or {}).get('before') or {}
    after = {q['question_id']: q.get('clarifications') or {} for q in resolved.get('questions') or []}
    named = set()
    for concern in concerns:
        if not concern.get('response'):
            continue
        if concern.get('kind') == 'consistency':
            named |= {(qid, iid) for qid, ids in (concern.get('lines') or {}).items() for iid in ids}
        else:
            named |= {(concern['question_id'], iid) for iid in concern.get('item_ids') or []}
    rubrics, rewritten = json.loads(json.dumps(rubrics)), []
    held = {entry['problem']: entry for entry in rubrics}
    for qid, iid in sorted(named):
        parts = qid.split(':')
        if len(parts) != 3 or parts[0] != 'draft' or not parts[1].isdigit() or not iid.startswith('R') or not iid[1:].isdigit():
            continue
        entry, letter, n = held.get(int(parts[1])), parts[2], int(iid[1:])
        new, old = after.get(qid, {}).get(iid, ''), ((before.get(qid) or {}).get('clarifications') or {}).get(iid, '')
        if not entry or not 0 < n <= len(entry['rubric']['items']) or not new.strip() or new == old:
            continue
        line = entry['rubric']['items'][n - 1]
        if not line['descriptions'].get(letter) or not line['clarifications'].get(letter, '').strip() or line['clarifications'][letter] == new:
            continue
        line['clarifications'] = {**line['clarifications'], letter: new}
        rewritten.append({'problem': int(parts[1]), 'version': letter, 'item_id': iid})
    return rubrics, rewritten


def renamed(value, names):
    """A check's result with the draft's `virtual` question and rubric IDs replaced by the plan's own (`names`), wherever one stands
    alone as a value or as a key. The reviewer never saw an ID, so nothing it wrote holds one."""
    if isinstance(value, dict):
        return {names.get(key, key): renamed(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [renamed(item, names) for item in value]
    return names.get(value, value) if isinstance(value, str) else value



COPY_LIKENESS = .8    # a drafted problem this alike in words to a bank question it was sent, with the same numbers, reprints it


def _plain(value):
    value = re.sub(r"\\[a-zA-Z]+", " ", str(value or "")).casefold()
    return " ".join(re.sub(r"[^a-z0-9.]+", " ", value).split())


def bank_copies(quiz, bank):
    """QUIZZES "NOTES SECTIONS FOR THE WRITER" › NEVER A COPY: each drafted problem's version that reprints a bank question the writer
    was sent: the same numbers, in the same order, and words at least COPY_LIKENESS alike. Changed numbers are a changed problem."""
    if not bank:
        return []
    sent = []
    for section in bank["sections"]:
        for q in section["questions"]:
            words = _plain(" ".join([q["statement"], *(p["text"] for p in q.get("parts", [])), *q.get("choices", [])]))
            sent.append((q["source"] or f"from section {section['number']}", words, re.findall(r"\d+(?:\.\d+)?", words)))
    found = []
    for problem in quiz['problems']:
        for letter, content in problem['content'].items():
            words = _plain(statement_text(content))
            numbers = re.findall(r"\d+(?:\.\d+)?", words)
            for source, theirs, their_numbers in sent:
                if numbers == their_numbers and difflib.SequenceMatcher(None, words, theirs, autojunk=False).ratio() >= COPY_LIKENESS:
                    found.append(f"Problem {problem['number']}, Version {letter} reprints the question bank's question ({source}) with its "
                                 "numbers: change it (new numbers, and its setting or wording) so that it is not a copy.")
                    break
    return found


def notes_sections(db, definition_id):
    """The sections of the lecture notes the course page shows (COURSE-NOTES), in their order, as the new-draft window offers them:
    [{number, title, chapter, chapter_title}]. Catalog only."""
    from course_notes.service import current_sections
    return [{'number': number_, **{k: part[k] for k in ('title', 'chapter', 'chapter_title')}}
            for number_, part in current_sections(db, definition_id).items()]

class DraftCheckHost(CheckHost):
    """A quiz revision's own rubric check (`rubric_quality.CheckHost`): its requests are the revision's (`cloud_call` sends them only
    while that revision is running), each printed problem is cropped from the revision's own page pictures at the draft's areas
    (the pages a template of the approved PDF holds: the same converter renders both), and on a subscription connection the
    reviewer has the rubric writers' context with a closer look at those pages, as the rubric step does."""

    def __init__(self, grading, definition_id, pages):
        super().__init__(grading)
        self.definition_id, self.pages = definition_id, pages   # {letter: [PNG of each page]}

    def owner(self, task, step, attempt):
        return {'quiz_revision_id': task['id'], 'step': step, 'task_attempt': attempt}

    def images(self, question):
        from . import extraction
        letter = question['question_id'].rsplit(':', 1)[-1]
        try:
            with CROPS:
                return [extraction.extract(self.pages[letter][area['page'] - 1], area['region'], None, [])['images']['cloud']
                        for area in (question.get('reference') or {}).get('printed', [])]
        except Exception:  # as the plan's check: a page or a sandbox that fails only leaves the picture out
            return []

    def tools(self, task, endpoint, questions):
        return rubric_tools(self.grading, endpoint, self.definition_id, self.pages, reviewer=True)


def compile_faults(letter, error, tex):
    """What pdfLaTeX refused, in words a writer can act on: the error lines and the main.tex line they point at."""
    log = error.log.decode('utf-8', 'replace') if isinstance(error.log, bytes) else ''
    lines = log.splitlines()
    found = []
    for index, line in enumerate(lines):
        if line.startswith('!'):
            context = ' '.join(l.strip() for l in lines[index:index + 4] if l.strip())[:600]
            if 'tcrm' in context or 'mktexpk' in context:
                context += ' (a text-mode symbol such as °, ×, − or a curly quote needs a font the compiler does not have: write it in math mode)'
            at = re.search(r'l\.(\d+)', ' '.join(lines[index:index + 12]))
            where = ''
            if at:
                source = tex.splitlines()
                number_ = int(at.group(1))
                if 1 <= number_ <= len(source):
                    where = f" At main.tex line {number_}: {source[number_ - 1][:400]}"
                    problem = [l for l in source[:number_] if l.startswith('\\quizproblem{')]
                    if problem:
                        where += f" (problem {re.match(r'.quizproblem.(\d+)', problem[-1]).group(1)})"
            found.append(f"Version {letter}: LaTeX error {context}.{where}")
            if len(found) >= 3:
                break
    return found or [f"Version {letter}: LaTeX could not compile this version ({error})."]


class QuizDraftsMixin:
    # Reading ------------------------------------------------------------------------------------------------------------------
    def _quiz_draft(self, db, draft_id):
        row = db.execute('SELECT * FROM quiz_drafts WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise NotFound('Quiz draft not found.')
        return dict(row)

    def _quiz_revision_summary(self, db, row, *, details=False):
        state = 'cancelled' if row['cancelled'] else row['status']
        context = json.loads(row['input_json'])
        profile = json.loads(row['profile_json'])
        found = {k: row[k] for k in ('id', 'number', 'based_on', 'error', 'created_at', 'updated_at')}
        found.update(status=state, model=profile['endpoint']['name'], feedback=context.get('feedback', ''), mode=context['mode'])
        # A revision that only sends the instructor's answers to the rubric check's decisions (2026-10-11, `answer_quiz_rubric_check`):
        # named only on such a revision, with the answers, so a failed one is sent again as it was.
        if context.get('rubric_answers'):
            found['rubric_answers'] = context['rubric_answers']['responses']
        if state in ('pending', 'running'):
            steps = {r[0] for r in db.execute("SELECT DISTINCT step FROM frq_cloud_calls WHERE quiz_revision_id=? AND state='completed'", (row['id'],))}
            # What has been sent, answered or not: a stage is named from its first request, not after its first answer (2026-11-04).
            begun = {r[0] for r in db.execute("SELECT DISTINCT step FROM frq_cloud_calls WHERE quiz_revision_id=?", (row['id'],))}
            written_ = db.execute("SELECT receipt_json FROM frq_cloud_calls WHERE quiz_revision_id=? AND step=? AND state='completed' ORDER BY rowid DESC LIMIT 1",
                                  (row['id'], WRITE)).fetchone()
            problems = json.loads(written_[0] or '{}').get('result', {}).get('output', {}).get('problems') if written_ else None
            # A revision that changed nothing printed sends no check (`_quiz_checked`, 2026-10-11): its check is over once a later step answered.
            found['progress'] = {'written': WRITE in steps, 'fixes': sum(s.startswith(FIX) for s in steps),
                                 'checked': CHECK in steps or any(s.startswith((FIX + 'check', RUBRIC, KEY)) for s in steps),
                                 'rubrics': sum(s.startswith(RUBRIC) for s in steps), 'problems': len(problems) if isinstance(problems, list) else None,
                                 # Answer keys read so far (2026-10-11): named only once the first is, so earlier steps read as before.
                                 **({'keys': read} if (read := sum(s.startswith(KEY) for s in begun)) else {}),
                                 # The check's findings are with the writer (2026-11-04): it had read as drafting the rubrics.
                                 **({'fixing': True} if any(s.startswith(FIX + 'check') for s in begun - steps) else {}),
                                 # The rubric check's answered requests, its last step (2026-10-11), and a revision that only sends
                                 # answers to it: named the same way.
                                 **({'rubric_check': asked} if (asked := sum(s.startswith(CHECK_STEPS) for s in begun)) else {}),
                                 **({'answers': True} if context.get('rubric_answers') else {})}
        if row['status'] == 'complete' and row['result_json']:
            result = json.loads(row['result_json'])
            found['summary'] = {'problems': len(result['quiz']['problems']), 'points': result['quiz']['total'], 'versions': result['quiz']['versions'],
                                'rubrics': sum(1 for r in result['rubrics'] if r.get('rubric')), 'warnings': len(result['warnings'])}
            # How many answer keys the revision read, one per problem and version (2026-10-11, `_quiz_keys`): what an approval that saves
            # keys saves. Named only on a revision that read keys, so every earlier revision reads as before.
            if any('keys' in r for r in result['rubrics']):
                found['summary']['keys'] = sum(1 for r in result['rubrics'] for reading in (r.get('keys') or {}).values() if reading['key'])
            # What the rubric check left for the instructor (2026-10-11, `check_counts`): named only on a revision that ran one.
            check = result.get('rubric_check')
            if check:
                found['summary']['rubric_check'] = check_counts(check)
            if details:
                found['result'] = self._quiz_page_result(result) if check else result
                if result.get('makeup'):
                    found['result'] = self._makeup_leftovers_now(db, row['draft_id'], found['result'])
        return found

    @staticmethod
    def _makeup_leftovers_now(db, draft_id, result):
        """A makeup revision as its window reads it (GRADING "LEFTOVER VALUES"): a revision drafted before 2026-11-05 stored a finding on
        every unchanged line with a digit or any mathematics, which is read again from what it stores. Read only; nothing is stored."""
        if not any(STORED_LEFTOVER.search(str(w)) for w in result.get('warnings', [])):
            return result
        references = {r['question_id']: r['problem'] for r in json.loads(db.execute('SELECT input_json FROM quiz_drafts WHERE id=?', (draft_id,)).fetchone()[0])['makeup']['references']}
        letter, problems = result['quiz']['versions'][0], {p['number']: p for p in result['quiz']['problems']}
        warnings, rubrics_ = list(result['warnings']), []
        for entry in result['rubrics']:
            rubric = entry.get('rubric') or {}
            stored = [w for w in rubric.get('warnings', []) if STORED_LEFTOVER.search(w)]
            reference = references.get(entry['source']['question_id'])
            if not stored or not reference:  # Nothing to read again, or the original is no longer in the draft's copy: as stored.
                rubrics_.append(entry)
                continue
            problem = problems[entry['problem']]
            content = problem['content'][letter]
            now = leftover_warning({'statement': f"{statement_text(content)}\n{content['solution'] or ''}"}, reference, rubric['items'],
                                   {line['id']: line['description'] for line in rubric['reference_items']}, rubric.get('solution', ''),
                                   rubric.get('final_answers', []), entry['source']['label'])
            rubrics_.append({**entry, 'rubric': {**rubric, 'warnings': [w for w in rubric['warnings'] if w not in stored] + ([now] if now else [])}})
            said = f"Problem {entry['problem']} rubric: "
            warnings = [w for w in warnings if w not in {said + old for old in stored}] + ([said + now] if now else [])
        return {**result, 'warnings': warnings, 'rubrics': rubrics_}

    @staticmethod
    def _quiz_page_result(result):
        """A revision's result as its page reads it: the rubric check without the reviewer's connection and settings (its `model` is
        named), and with the questions its concerns are about as the concerns view draws them (`draft_check_context`: each line's
        ID, wording and points on its version, the built-in exclusions and the drafted comments). Read only; nothing is stored."""
        check = result['rubric_check']
        shown = {k: v for k, v in check.items() if k != 'profile'}
        if check.get('profile'):
            shown['model'] = check['profile']['endpoint']['name']
        if check.get('status') == 'complete':
            try:
                context = draft_check_context(result['revision_id'], result['quiz'], result['rubrics'], result['mode'], result['quiz']['versions'], result['versions'])
                shown['view'] = [{k: q[k] for k in ('question_id', 'rubric_id', 'exam_version', 'title', 'printed_number', 'max_points', 'rubric', 'builtin', 'comments') if k in q}
                                 for q in context['questions']]
            except (ValueError, TypeError, KeyError):  # a revision another rule drafted: its page shows the check's counts without the view
                pass
        return {**result, 'rubric_check': shown}

    def _quiz_approval_summary(self, row, db):
        found = {k: row[k] for k in ('id', 'revision_id', 'term_id', 'status', 'error', 'created_at', 'updated_at')}
        found['input'] = json.loads(row['input_json'])
        found['progress'] = json.loads(row['progress_json'] or '{}')
        if row['result_json']:
            # What the quiz is printed and graded as now: a rebuild's versions, questions and print jobs replace the approval's.
            found['result'] = self._quiz_current(db, row)
        rebuilds = [{k: r[k] for k in ('id', 'status', 'error', 'created_at', 'updated_at')} for r in db.execute(
            'SELECT id,status,error,created_at,updated_at FROM quiz_draft_rebuilds WHERE approval_id=? ORDER BY created_at,rowid', (row['id'],))]
        if rebuilds:
            found['rebuilds'] = rebuilds
        return found

    def _quiz_draft_summary(self, db, row, *, details=False):
        context = json.loads(row['input_json'])
        found = {'id': row['id'], 'definition_id': row['definition_id'], 'created_at': row['created_at'],
                 **{k: context[k] for k in ('title', 'course', 'term', 'versions', 'total', 'mode', 'instructions', 'title_line')},
                 'format': context.get('format', ''),
                 'material': [{k: m[k] for k in ('name', 'kind', 'size')} for m in context['material']],
                 'examples': [{k: e[k] for k in ('template_id', 'label', 'assignment_title')} for e in context['examples']]}
        if context.get('blueprint'):
            found['blueprint'] = context['blueprint']
        if context.get('notes_sections'):
            found['notes_sections'] = context['notes_sections']
        if context.get('participation'):  # GRADING "GRADED FOR PARTICIPATION"; named only when ticked
            found['participation'] = True
        revisions = [dict(r) for r in db.execute('SELECT * FROM quiz_draft_revisions WHERE draft_id=? ORDER BY number', (row['id'],))]
        latest = next((r for r in reversed(revisions) if r['status'] == 'complete' and not r['cancelled']), None)
        found['revisions'] = [self._quiz_revision_summary(db, r, details=details and latest is not None and r['id'] == latest['id']) for r in revisions]
        found['latest_complete'] = latest['id'] if latest else None
        found['live'] = next((r['id'] for r in revisions if r['status'] in ('pending', 'running')), None)
        approvals = [self._quiz_approval_summary(r, db) for r in db.execute('SELECT * FROM quiz_draft_approvals WHERE draft_id=? ORDER BY created_at,rowid', (row['id'],))]
        found['approvals'] = approvals
        found['approved'] = next((a for a in reversed(approvals) if a['status'] != 'failed'), None)
        found['state'] = ('approved' if found['approved'] and found['approved']['status'] == 'complete' else 'approving' if found['approved']
                          else 'drafting' if found['live'] else 'ready' if latest else 'failed')
        if found['state'] == 'approved' and any(r['status'] in ('pending', 'running') for r in found['approved'].get('rebuilds', [])):
            found['state'] = 'rebuilding'
        if context.get('makeup'):  # named only on a makeup draft, so every quiz draft reads as before
            found['makeup'] = {'plan_id': context['makeup']['plan_id'], 'event_id': context['makeup']['event_id'], 'letter': context['makeup']['letter'],
                               'source': context['makeup']['source']['label'], 'problems': len(context['makeup']['references'])}
        scrapped = db.execute('SELECT actor,created_at FROM quiz_draft_scraps WHERE draft_id=?', (row['id'],)).fetchone()
        if scrapped:  # named only on a scrapped draft, so every other draft reads as before
            found.update(state='scrapped', scrapped=dict(scrapped))
        return found

    @staticmethod
    def _quiz_not_scrapped(db, draft_id):
        if db.execute('SELECT 1 FROM quiz_draft_scraps WHERE draft_id=?', (draft_id,)).fetchone():
            raise Conflict('This quiz draft was scrapped. Draft a new quiz instead.')

    def quiz_drafts(self, definition_id=None):
        with self.rosters.lock(), connect(self.path) as db:
            # A scrapped draft is listed no more (`scrap_quiz_draft`); its own page still reads it.
            # A makeup draft (GRADING "MAKEUP VERSIONS") is listed with its assignment, not with the quizzes drafted from material.
            rows = db.execute("SELECT * FROM quiz_drafts WHERE id NOT IN (SELECT draft_id FROM quiz_draft_scraps) AND json_extract(input_json,'$.makeup') IS NULL"
                              + (' AND definition_id=?' if definition_id else '')
                              + ' ORDER BY created_at DESC,rowid DESC', (definition_id,) if definition_id else ()).fetchall()
            return [self._quiz_draft_summary(db, row) for row in rows]

    def quiz_draft(self, draft_id):
        with self.rosters.lock(), connect(self.path) as db:
            return self._quiz_draft_summary(db, self._quiz_draft(db, draft_id), details=True)

    def quiz_revision(self, draft_id, revision_id):
        with self.rosters.lock(), connect(self.path) as db:
            self._quiz_draft(db, draft_id)
            row = db.execute('SELECT * FROM quiz_draft_revisions WHERE id=? AND draft_id=?', (revision_id, draft_id)).fetchone()
            if not row:
                raise NotFound('Quiz draft revision not found.')
            return self._quiz_revision_summary(db, row, details=True)

    def quiz_file(self, draft_id, revision_id, letter, kind, page=None):
        """One compiled file of a revision's version: its PDF, LaTeX source, style, build log or a page picture. Read and verified
        through the course definition's storage; never a path from the request."""
        with self.rosters.lock():
            with connect(self.path) as db:
                draft = self._quiz_draft(db, draft_id)
                row = db.execute("SELECT result_json FROM quiz_draft_revisions WHERE id=? AND draft_id=? AND status='complete'", (revision_id, draft_id)).fetchone()
            if not row:
                raise NotFound('This revision has no compiled quiz.')
            version = json.loads(row[0])['versions'].get(letter)
            if not version:
                raise NotFound('That version is not part of this quiz.')
            if kind == 'page':
                if type(page) is not int or not 1 <= page <= len(version['pages']):
                    raise NotFound('That page does not exist.')
                item, media = version['pages'][page - 1]['image'], 'image/png'
            elif kind in ('pdf', 'tex', 'log', 'sty'):
                item = version['files'][{'pdf': 'main.pdf', 'tex': 'main.tex', 'log': 'build.log', 'sty': quiz_latex.STYLE}[kind]]
                media = {'pdf': 'application/pdf', 'tex': 'text/x-tex; charset=utf-8', 'log': 'text/plain; charset=utf-8', 'sty': 'text/x-tex; charset=utf-8'}[kind]
            else:
                raise NotFound('That file does not exist.')
            from document_identity.storage import read_verified
            with self.exams.documents.files.files('definition', draft['definition_id']) as root:
                return read_verified(root, item['path'], item['sha256']), media

    # Request ------------------------------------------------------------------------------------------------------------------
    def quiz_draft_options(self, definition_id=None):
        """What the new-draft window offers: current terms of each course with their sections, and past assignments' blank templates to
        imitate (quizzes first, newest first). Catalog only."""
        with self.rosters.lock(), connect(self.path) as db:
            terms = [dict(r) for r in db.execute("""SELECT t.*,d.code definition_code FROM course_terms t JOIN course_definitions d ON d.id=t.definition_id
                WHERE t.is_current=1 AND t.deleted_at IS NULL""" + (' AND t.definition_id=?' if definition_id else '') + ' ORDER BY d.code,t.year DESC',
                (definition_id,) if definition_id else ())]
            courses = []
            for term in terms:
                sections = [dict(r) for r in db.execute('''SELECT s.id,s.number,(SELECT COUNT(*) FROM roster_current m WHERE m.section_id=s.id) students
                    FROM course_sections s WHERE s.term_id=? ORDER BY s.number''', (term['id'],))]
                templates = [dict(r) for r in db.execute('''SELECT t.id template_id,t.label,t.page_count,a.title assignment_title,t.created_at
                    FROM assignment_templates t JOIN assignments a ON a.id=t.assignment_id WHERE a.definition_id=? ORDER BY t.created_at DESC''', (term['definition_id'],))]
                templates.sort(key=lambda t: not re.search(r'quiz', t['assignment_title'], re.I))  # quizzes first, each group newest first
                quizzes = [t for t in templates if re.search(r'quiz', t['assignment_title'], re.I)]
                # The newest quiz's first version (Version A) is the example to imitate, unless the instructor ticks others.
                suggested = [min((t for t in quizzes if t['assignment_title'] == quizzes[0]['assignment_title']), key=lambda t: t['label'])['template_id']] if quizzes else []
                courses.append({'definition_id': term['definition_id'], 'code': term['definition_code'], 'term_id': term['id'], 'term': term_label(term),
                                'sections': sections, 'templates': [{k: t[k] for k in ('template_id', 'label', 'page_count', 'assignment_title')} for t in templates[:40]],
                                'suggested_examples': suggested,
                                # RUBRIC CHOICES (2026-10-24): the form starts from the course's latest answers.
                                'rubric_choices': rubric_choices.of_course(db, term['definition_id']),
                                # NOTES SECTIONS (2026-10-28): the sections of the notes the course page shows, to choose from.
                                'notes_sections': notes_sections(db, term['definition_id'])})
        try:
            model = self._drafting_model()[0]['name']
            problem = ''
        except ValueError as error:
            model, problem = None, str(error)
        return {'courses': courses, 'model': model, 'model_problem': problem, 'max_versions': MAX_VERSIONS, 'default_directions': DIRECTIONS,
                'default_format': FORMAT, 'rubric_choice_options': rubric_choices.options()}

    def create_quiz_draft(self, data, files):
        """A new draft and its first revision. `files` is [(filename, bytes)], the instructor's material, retained in the course's storage."""
        request_id = identifier(data.get('request_id'))
        term_id = identifier(data.get('term_id'))
        title = ' '.join(text(data.get('title'), 'Quiz title', 120, False).split())
        total = number(data.get('total'), 'Total points', 0.5, 100)
        count = data.get('versions')
        count = int(count) if isinstance(count, str) and count.isdigit() else count
        versions = letters(count)
        mode = scoring_mode(data.get('mode', 'positive'))
        instructions = text(data.get('instructions', ''), 'Instructions', MAX_INSTRUCTIONS).strip()
        quiz_format = FORMAT if data.get('format') is None else text(data['format'], 'Format', MAX_FORMAT).strip()
        # RUBRIC CHOICES (requirement, 2026-10-24): the instructor's quick answers for this quiz, sent as JSON text with the form's files.
        choices = data.get('rubric_choices')
        if isinstance(choices, str):
            try:
                choices = json.loads(choices) if choices.strip() else None
            except ValueError:
                raise ValueError('Rubric choices are given as {choice: option}.') from None
        choices = rubric_choices.clean(choices)
        # GRADED FOR PARTICIPATION (requirement, 2026-11-01): the form's box ("1" ticked, from the form's files; true as JSON). Its
        # approval records it as the new plan's setting (GRADING "GRADED FOR PARTICIPATION"); the model writes the quiz as ever.
        taking_part = data.get('participation') in (True, '1', 'true')
        # NOTES SECTIONS (requirement, 2026-10-28): the lecture-notes sections the writer is sent, by number ("4.1,4.2" from the form,
        # which always sends the field). Not given at all, they are the sections the instructions name, else the title (below).
        named_only = data.get('notes_sections') is None
        chosen_sections = data.get('notes_sections') or []
        if isinstance(chosen_sections, str):
            chosen_sections = [n.strip() for n in chosen_sections.split(',') if n.strip()]
        chosen_sections = items(chosen_sections, 'Lecture notes sections', MAX_NOTES_SECTIONS)
        examples = data.get('examples') or []
        if isinstance(examples, str):
            examples = [e for e in examples.split(',') if e]
        examples = [identifier(example) for example in items(examples, 'Example quizzes', EXAMPLES)]
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ValueError(f'Provide at most {MAX_FILES} files of material.')
        material, seen = [], set()
        for index, (name, content) in enumerate(files):
            clean = safe_name(name)
            if not isinstance(content, bytes) or not content or len(content) > MAX_FILE:
                raise ValueError(f'{clean} must hold something and at most {MAX_FILE // 1024**2} MB.')
            kind = KINDS[PurePosixPath(clean).suffix.lower()]
            if kind == 'pdf' and not content.startswith(b'%PDF'):
                raise ValueError(f'{clean} is not a PDF.')
            if kind == 'image' and not (content.startswith(b'\x89PNG\r\n\x1a\n') or content.startswith(b'\xff\xd8\xff')):
                raise ValueError(f'{clean} is not a PNG or JPEG picture.')
            if kind == 'text':
                try:
                    content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    raise ValueError(f'{clean} is not UTF-8 text.') from None
            checksum = digest(content)
            if checksum in seen:
                continue
            seen.add(checksum)
            material.append({'name': clean, 'kind': kind, 'size': len(content), 'sha256': checksum, 'content': content, 'index': index})
        if sum(m['size'] for m in material) > MAX_MATERIAL:
            raise ValueError(f'The material adds up to more than {MAX_MATERIAL // 1024**2} MB.')
        endpoint, settings = self._quiz_writing_model()
        with self.rosters.lock():
            with connect(self.path) as db:
                term = db.execute("SELECT t.*,d.code FROM course_terms t JOIN course_definitions d ON d.id=t.definition_id WHERE t.id=? AND t.deleted_at IS NULL",
                                  (term_id,)).fetchone()
                if not term:
                    raise ValueError('Choose the course and term this quiz is for.')
                if not term['is_current']:
                    raise ValueError('Quizzes are drafted for a current term.')
                prior = db.execute('SELECT * FROM quiz_drafts WHERE request_id=?', (request_id,)).fetchone()
                previous = json.loads(prior['input_json']) if prior else {}
                blueprint = (previous.get('blueprint') if prior and previous.get('blueprint_request') == data.get('blueprint')
                             else quiz_blueprints.pin(db, term['definition_id'], data.get('blueprint'), total))
                chosen = []
                for template_id in dict.fromkeys(examples):
                    found = db.execute('''SELECT t.id,t.label,t.page_count,t.manifest_sha256,a.title FROM assignment_templates t JOIN assignments a ON a.id=t.assignment_id
                        WHERE t.id=? AND a.definition_id=?''', (template_id, term['definition_id'])).fetchone()
                    if not found:
                        raise ValueError('Choose example quizzes of this course.')
                    chosen.append({'template_id': found['id'], 'label': found['label'], 'assignment_title': found['title'],
                                   'pages': min(found['page_count'], 4), 'manifest_sha256': found['manifest_sha256']})
                offered = notes_sections(db, term['definition_id'])
                if named_only:  # what the instructions name, else what the title names ("Chapter 4 Quiz")
                    chosen_sections = [part['number'] for part in covered_sections(offered, [instructions]) or covered_sections(offered, [title])]
                if any(not isinstance(n, str) or n not in {o['number'] for o in offered} for n in chosen_sections):
                    raise ValueError("Choose sections of this course's lecture notes.")
                chosen_sections = [o['number'] for o in offered if o['number'] in set(chosen_sections)]
                # Sections of the notes are material enough: their text and the bank's questions go to the writer (instructor,
                # 2026-10-28: "I shouldn't have to give additional information or files").
                if not material and not instructions and not data.get('blueprint') and not chosen_sections:
                    raise ValueError('Tick the lecture notes sections the quiz covers, or provide material or instructions for the model.')
                prior = db.execute('SELECT * FROM quiz_drafts WHERE request_id=?', (request_id,)).fetchone()
            context = {'rule': RULE, 'course': term['code'], 'term': term_label(term), 'term_id': term['id'], 'title': title,
                       'title_line': f"{term['code']} {term_label(term)} {title}", 'versions': versions, 'total': total, 'mode': mode,
                       'format': quiz_format, 'instructions': instructions, 'examples': chosen,
                       'material': [{k: m[k] for k in ('name', 'kind', 'size', 'sha256')} for m in material]}
            if blueprint:
                context['blueprint'] = blueprint
                context['blueprint_request'] = data['blueprint']
            if choices:  # named only where some were chosen, so a request without any keeps the fingerprint it always had
                context['rubric_choices'] = choices
            if chosen_sections:  # the same
                context['notes_sections'] = chosen_sections
            if taking_part:  # the same
                context['participation'] = True
            checksum = digest([term['definition_id'], context])
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another quiz draft.')
                return self.quiz_draft(prior['id'])
            draft_id = uid()
            from document_identity.storage import publish
            with self.exams.documents.files.files('definition', term['definition_id'], write=True) as root:
                for m, kept in zip(material, context['material']):
                    kept['path'] = f"quiz-drafts/{draft_id}/material/{m['index'] + 1}-{m['name']}"
                    publish(root / kept['path'], m['content'])
                with connect(self.path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    now = timestamp()
                    db.execute('INSERT INTO quiz_drafts VALUES(?,?,?,?,?,?)', (draft_id, term['definition_id'], request_id, checksum, encoded(context), now))
                    self._record_quiz_revision(db, draft_id, 1, None, f'{request_id}:1', {'rule': RULE, 'mode': mode, 'feedback': ''}, endpoint, settings)
                    self.exams.documents.event(db, 'quiz_draft_created', 'workspace', {'draft_id': draft_id, 'title': title, 'versions': versions,
                                                                                      'material': [m['name'] for m in material], 'model': endpoint['name']})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    def _quiz_writing_model(self):
        """The drafting model for a quiz or makeup writer, with room for a whole quiz; frozen into each revision's profile and digest."""
        endpoint, settings = self._drafting_model()
        return {**endpoint, 'max_tokens': max(endpoint['max_tokens'], WRITE_TOKENS)}, settings

    def _record_quiz_revision(self, db, draft_id, number_, based_on, request_id, context, endpoint, settings):
        rid, now = uid(), timestamp()
        db.execute("""INSERT INTO quiz_draft_revisions(id,draft_id,number,based_on,request_id,request_sha256,input_json,profile_json,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,'pending',?,?)""", (rid, draft_id, number_, based_on, request_id, digest([draft_id, based_on, context, endpoint]),
                                                     encoded(context), encoded({'endpoint': endpoint, 'settings': settings}), now, now))
        return rid

    def revise_quiz_draft(self, draft_id, data):
        """Ask the model again: the instructor's requested changes to the revision they looked at (`based_on`), or the same request once
        more after a failure. The scoring mode may change with it; every problem's rubric is then drafted again."""
        request_id = identifier(data.get('request_id'))
        feedback = text(data.get('feedback', ''), 'Requested changes', MAX_INSTRUCTIONS).strip()
        endpoint, settings = self._quiz_writing_model()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            draft = self._quiz_draft(db, draft_id)
            prior = db.execute('SELECT * FROM quiz_draft_revisions WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                previous_context = json.loads(prior['input_json'])
                requested_context = {'rule': RULE, 'mode': scoring_mode(data.get('mode') or previous_context['mode']), 'feedback': feedback}
                if prior['draft_id'] != draft_id or prior['based_on'] != (data.get('based_on') or None) or previous_context != requested_context:
                    raise Conflict('This request ID already belongs to another quiz revision request.')
                return self._quiz_draft_summary(db, draft, details=True)
            self._quiz_not_scrapped(db, draft_id)
            if db.execute("SELECT 1 FROM quiz_draft_approvals WHERE draft_id=? AND status!='failed'", (draft_id,)).fetchone():
                raise Conflict('This quiz is approved. Draft a new quiz to change it.')
            if db.execute("SELECT 1 FROM quiz_draft_revisions WHERE draft_id=? AND status IN ('pending','running')", (draft_id,)).fetchone():
                raise Conflict('The model is still working on this quiz. Wait for it, or stop it first.')
            based = data.get('based_on') or None
            if based:
                based = identifier(based)
                row = db.execute("SELECT * FROM quiz_draft_revisions WHERE id=? AND draft_id=? AND status='complete' AND cancelled=0", (based, draft_id)).fetchone()
                if not row:
                    raise ValueError('Ask for changes to a finished revision of this quiz.')
                mode = scoring_mode(data.get('mode') or json.loads(row['input_json'])['mode'])
                if not feedback and mode == json.loads(row['input_json'])['mode']:
                    raise ValueError('Say what should change.')
            else:
                mode = scoring_mode(data.get('mode') or json.loads(draft['input_json'])['mode'])
            number_ = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM quiz_draft_revisions WHERE draft_id=?', (draft_id,)).fetchone()[0]
            rid = self._record_quiz_revision(db, draft_id, number_, based, request_id, {'rule': RULE, 'mode': mode, 'feedback': feedback}, endpoint, settings)
            self.exams.documents.event(db, 'quiz_draft_revision_requested', 'workspace', {'draft_id': draft_id, 'revision_id': rid, 'number': number_,
                                                                                         'based_on': based, 'mode': mode, 'model': endpoint['name']})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    def answer_quiz_rubric_check(self, draft_id, data):
        """The instructor's answers to the decisions of the rubric check a draft shows (requirement, 2026-10-11): `responses` [{concern, response}], numbered as the check
        lists its concerns (`rubric_quality.concern_list`), about the check the page showed (`based_on`: its revision,
        `check_sha256`). It queues a NEW revision based on that one which changes nothing of the quiz: the quiz, its rubrics, its
        keys and its compiled files are that revision's, and only the check's own Resolve step runs (`_run_rubric_answers`), every
        concern of the problems named being sent, the answered ones with their answers. Like Resolve concerns in Key & Rubric
        Finalization it approves nothing. Refused for a check that is not the one shown, that could not be made, or that lists
        no concern; and wherever asking for changes is (a scrapped or approved draft, a revision still running)."""
        request_id = identifier(data.get('request_id'))
        based_on = identifier(data.get('based_on'))
        responses = {}
        for row in items(data.get('responses'), 'Answers to the decisions', 200):
            if not isinstance(row, dict) or type(row.get('concern')) is not int or row['concern'] < 0 or row['concern'] in responses:
                raise ValueError('Answer each decision once, by its number.')
            comment = text(row.get('response'), 'Your answer to a decision', 4000).strip()
            if comment:
                responses[row['concern']] = comment
        # BOOKKEEPING IS FIXED, NOT ASKED on the draft's page (2026-10-29): a fix the instructor chose "Ask me instead" for is a
        # decision of this answer, and its fix is not made (named only where there is one, so other answers keep their fingerprint).
        unfixed = sorted({n for n in items(data.get('unfixed', []), 'Fixes asked instead', 200) if type(n) is int and n >= 0})
        endpoint, settings = self._quiz_writing_model()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            draft = self._quiz_draft(db, draft_id)
            prior = db.execute('SELECT * FROM quiz_draft_revisions WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                prior_answers = json.loads(prior['input_json']).get('rubric_answers')
                asked = {'check_sha256': data.get('check_sha256'), 'responses': [{'concern': n, 'response': responses[n]} for n in sorted(responses)],
                         **({'unfixed': unfixed} if unfixed else {})}
                if prior['draft_id'] != draft_id or prior['based_on'] != based_on or prior_answers != asked:
                    raise Conflict('This request ID already belongs to another rubric answer request.')
                return self._quiz_draft_summary(db, draft, details=True)
            self._quiz_not_scrapped(db, draft_id)
            if db.execute("SELECT 1 FROM quiz_draft_approvals WHERE draft_id=? AND status!='failed'", (draft_id,)).fetchone():
                raise Conflict('This quiz is approved. Answer its rubric decisions in Key & Rubric Finalization.')
            if db.execute("SELECT 1 FROM quiz_draft_revisions WHERE draft_id=? AND status IN ('pending','running')", (draft_id,)).fetchone():
                raise Conflict('The model is still working on this quiz. Wait for it, or stop it first.')
            row = db.execute("SELECT * FROM quiz_draft_revisions WHERE id=? AND draft_id=? AND status='complete' AND cancelled=0", (based_on, draft_id)).fetchone()
            latest = db.execute("SELECT id FROM quiz_draft_revisions WHERE draft_id=? AND status='complete' AND cancelled=0 ORDER BY number DESC LIMIT 1", (draft_id,)).fetchone()
            if not row or latest['id'] != row['id']:
                raise Conflict('A newer revision of this quiz exists. Open it before answering its rubric decisions.')
            check = json.loads(row['result_json']).get('rubric_check') or {}
            if check.get('status') != 'complete' or check.get('sha256') != data.get('check_sha256'):
                raise Conflict('The rubric check shown is not the one this revision holds. Refresh before answering its decisions.')
            concerns = answered_concerns(check['result'], responses)
            if unfixed and max(unfixed) >= len(concerns):
                raise Conflict('A fix you asked to decide is not one this review lists. Refresh first.')
            number_ = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM quiz_draft_revisions WHERE draft_id=?', (draft_id,)).fetchone()[0]
            context = {'rule': RULE, 'mode': json.loads(row['input_json'])['mode'], 'feedback': '',
                       'rubric_answers': {'check_sha256': check['sha256'], 'responses': [{'concern': n, 'response': responses[n]} for n in sorted(responses)],
                                          **({'unfixed': unfixed} if unfixed else {})}}
            rid = self._record_quiz_revision(db, draft_id, number_, row['id'], request_id, context, endpoint, settings)
            self.exams.documents.event(db, 'quiz_draft_rubric_answers_requested', 'workspace', {
                'draft_id': draft_id, 'revision_id': rid, 'number': number_, 'based_on': row['id'], 'concerns': len(concerns), 'answered': len(responses),
                'model': endpoint['name']})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    def cancel_quiz_revision(self, draft_id, revision_id):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self._quiz_draft(db, draft_id)
            if db.execute("UPDATE quiz_draft_revisions SET status='failed',cancelled=1,error='Stopped by the instructor.',updated_at=? WHERE id=? AND draft_id=? AND status IN ('pending','running')",
                          (timestamp(), revision_id, draft_id)).rowcount:
                self.exams.documents.event(db, 'quiz_draft_revision_cancelled', 'workspace', {'draft_id': draft_id, 'revision_id': revision_id})
        return self.quiz_draft(draft_id)

    def scrap_quiz_draft(self, draft_id, data, actor='workspace'):
        """The instructor scraps a draft they do not want (requirement, 2026-10-01). Drafts are retained, so scrapping is a record beside the draft
        (`quiz_draft_scraps`): it is listed no more, its revisions and approvals are refused, and a revision still pending or running is
        stopped in the same transaction, so nothing further is sent (`cloud_call` sends only for a running revision). A quiz whose approval
        was carried out or is being carried out is an assignment with print jobs, never scrapped; a failed approval's leftovers stay.
        Its material, revisions, compiled files and receipts stay where they are. Scrapping again changes nothing."""
        if data.get('confirmation') != 'scrap':
            raise ValueError('Confirm scrapping the draft: it can no longer be revised or approved.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            draft = self._quiz_draft(db, draft_id)
            if not db.execute('SELECT 1 FROM quiz_draft_scraps WHERE draft_id=?', (draft_id,)).fetchone():
                approval = db.execute("SELECT status FROM quiz_draft_approvals WHERE draft_id=? AND status!='failed' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                      (draft_id,)).fetchone()
                if approval:
                    raise Conflict('This quiz is approved: it is an assignment with print jobs now. Only a draft that was never approved is scrapped.'
                                   if approval['status'] == 'complete' else "Faculty is carrying out this quiz's approval, so it can no longer be scrapped.")
                now = timestamp()
                stopped = [r[0] for r in db.execute("SELECT id FROM quiz_draft_revisions WHERE draft_id=? AND status IN ('pending','running') ORDER BY number", (draft_id,))]
                db.execute("UPDATE quiz_draft_revisions SET status='failed',cancelled=1,error='Stopped: the draft was scrapped.',updated_at=? WHERE draft_id=? AND status IN ('pending','running')",
                           (now, draft_id))
                db.execute('INSERT INTO quiz_draft_scraps(draft_id,actor,created_at) VALUES(?,?,?)', (draft_id, actor, now))
                self.exams.documents.event(db, 'quiz_draft_scrapped', actor, {'draft_id': draft_id, 'title': json.loads(draft['input_json'])['title'], 'stopped': stopped})
        return self.quiz_draft(draft_id)

    # Worker: one revision ------------------------------------------------------------------------------------------------------
    def _quiz_ask(self, task, endpoint, settings, step, prompt, payload, images, check, *, faults_key=None, tools=None):
        """One request, asked once more when its answer is refused, the second time with the reason (a quiz is refused for what a
        writer can fix). What was answered before is replayed, never sent again. `tools` (a subscription connection only): what the
        model may call while it answers (`providers/tools.py`). `check(output, metadata)` reads the answer, with the call's receipt."""
        refused = None
        for attempt in (0, 1):
            body = payload
            if attempt and faults_key:
                body = {**payload, faults_key: [*payload.get(faults_key, []), f'Your previous answer was refused: {refused}']}
            reply = self.cloud_call({'quiz_revision_id': task['id'], 'step': step, 'task_attempt': attempt}, 'rubric_reviewer', endpoint,
                                    {'prompt': prompt, 'payload': body, 'images': images, **({'reask': True} if attempt else {}),
                                     **({'tools': tools} if tools else {})}, settings)
            try:
                return check(reply['output'], reply['metadata']), {'metadata': reply['metadata'], 'raw': reply['raw'], 'output': reply['output']}
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    def _quiz_material(self, draft, context):
        """The instructor's material as the writer reads it: text per file and page, and pictures of what has little text (a scanned or
        handwritten page, a figure, a picture file). PDFs are read in the document sandbox once per draft; the pages kept are course
        material in the definition's storage."""
        from document_identity.storage import publish, read_verified
        from document_identity.template_conversion import convert_template
        texts, pictures = [], []
        with self.rosters.lock(), self.exams.documents.files.files('definition', draft['definition_id']) as root:
            originals = {m['sha256']: read_verified(root, m['path'], m['sha256']) for m in context['material']}
        for m in context['material']:
            content = originals[m['sha256']]
            if m['kind'] == 'text':
                texts.append({'file': m['name'], 'page': None, 'text': content.decode('utf-8-sig')[:MATERIAL_TEXT]})
            elif m['kind'] == 'image':
                if len(content) <= 5 * 1024**2:
                    pictures.append((m['name'], None, content))
            else:
                prepared = self._quiz_prepared(draft, m, content, convert_template, publish, read_verified)
                for page in prepared:
                    if page['text'].strip():
                        texts.append({'file': m['name'], 'page': page['number'], 'text': page['text'][:PAGE_TEXT]})
                    if len(page['text'].strip()) < SCANNED and page.get('picture'):
                        pictures.append((m['name'], page['number'], page['picture']))
        budget, kept = MATERIAL_TEXT, []
        for entry in texts:
            if budget <= 0:
                kept.append({**entry, 'text': '', 'omitted': True})
                continue
            kept.append({**entry, 'text': entry['text'][:budget]})
            budget -= len(entry['text'])
        return kept, pictures

    def _quiz_prepared(self, draft, material, content, convert_template, publish, read_verified):
        """A material PDF's pages: text and a small picture of each, prepared once and kept beside the material."""
        base = f"quiz-drafts/{draft['id']}/prepared/{material['sha256']}"
        with self.rosters.lock(), self.exams.documents.files.files('definition', draft['definition_id']) as root:
            manifest = root / base / 'pages.json'
            if manifest.is_file():
                listing = json.loads(manifest.read_text())
                return [{'number': p['number'], 'text': p['text'],
                         'picture': read_verified(root, p['picture']['path'], p['picture']['sha256']) if p.get('picture') else None} for p in listing['pages']]
        try:
            converted = convert_template({material['name']: content})
        except ValueError as error:
            raise ValueError(f"{material['name']} could not be read: {error}") from None
        pages, payloads = [], {}
        for page in converted['pages']:
            entry = {'number': page['number'], 'text': page['text'][:PAGE_TEXT]}
            if len(page['text'].strip()) < SCANNED:
                picture = self._quiz_picture(page['png'], 1100)
                path = f"{base}/page-{page['number']}.jpg"
                payloads[path] = picture
                entry['picture'] = {'path': path, 'sha256': digest(picture)}
                entry['_bytes'] = picture
            pages.append(entry)
        with self.rosters.lock(), self.exams.documents.files.files('definition', draft['definition_id'], write=True) as root:
            for path, content_ in payloads.items():
                publish(root / path, content_)
            publish(root / base / 'pages.json', encoded({'pages': [{k: v for k, v in p.items() if k != '_bytes'} for p in pages]}).encode())
        return [{'number': p['number'], 'text': p['text'], 'picture': p.get('_bytes')} for p in pages]

    @staticmethod
    def _quiz_picture(png, width):
        """A page picture made smaller for a model's request: JPEG, `width` pixels wide. The PNG is the document sandbox's own render."""
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            image = image.convert('RGB')
            if image.width > width:
                image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=82, optimize=True)
            return out.getvalue()

    def _quiz_examples(self, context):
        """Past quizzes to imitate: each chosen blank template's text and its first pages as pictures."""
        found, pictures = [], []
        for example in context['examples']:
            pages = []
            for page in range(1, example['pages'] + 1):
                try:
                    words = self.templates.read(example['template_id'], 'text', page)[0].decode('utf-8', 'replace').strip()
                    picture = self._quiz_picture(self.templates.read(example['template_id'], 'image', page)[0], 1100)
                except (KeyError, ValueError, NotFound, OSError):
                    continue
                pages.append({'page': page, 'text': words[:PAGE_TEXT]})
                pictures.append((f"{example['assignment_title']} ({example['label']})", page, picture))
            if pages:
                found.append({'quiz': example['assignment_title'], 'label': example['label'], 'pages': pages})
        return found, pictures

    def _quiz_build(self, quiz):
        """Compile every version in the sandbox and read where each problem landed. Returns ({letter: build}, faults)."""
        import os
        from pathlib import Path
        from pypdf import PdfReader
        from document_identity.compiler import compile_sources
        from document_identity import process
        toolchain = os.environ.get('FACULTY_TEXLIVE_ROOT')
        if not toolchain:
            local = Path(__file__).resolve().parents[1] / '.tools/texlive'
            toolchain = str(local) if (local / 'usr/bin/pdflatex').is_file() else None

        def one(letter):
            files = quiz_latex.sources(quiz, letter)
            tex = files['main.tex'].decode()
            try:
                pdf, retained, details = compile_sources(files, toolchain_root=toolchain)
            except CompileError as error:
                return letter, None, compile_faults(letter, error, tex)
            except ValueError as error:
                return letter, None, [f'Version {letter}: {error}']
            pages = len(PdfReader(io.BytesIO(pdf)).pages)
            placed = quiz_latex.layout(retained['build.log'], quiz, pages)
            faults = [f'Version {letter}: {fault}' for fault in placed['faults']]
            if not faults:
                try:
                    process.run('inspect', pdf)  # the stamp box stays empty on every page, as registering the version will check
                except ValueError as error:
                    faults.append(f'Version {letter}: {error} Something is printed where the identity stamp goes; keep every figure and table inside the text area.')
            return letter, {'pdf': pdf, 'retained': retained, 'details': details, 'pages': pages, 'layout': placed}, faults
        with ThreadPoolExecutor(max_workers=min(4, len(quiz['versions']))) as pool:
            outcomes = list(pool.map(one, quiz['versions']))
        return {letter: built for letter, built, _ in outcomes if built}, [fault for *_, faults in outcomes for fault in faults]

    def _quiz_pages(self, built):
        """Each version's pages rendered as grading templates are (300 DPI, in the document sandbox), with their text."""
        from document_identity.template_conversion import convert_template

        def one(item):
            letter, build = item
            converted = convert_template({f'version-{letter}.pdf': build['pdf']})
            return letter, converted['pages']
        with ThreadPoolExecutor(max_workers=min(4, len(built))) as pool:
            return dict(pool.map(one, built.items()))

    def _quiz_write(self, task, endpoint, settings, step, payload, images, draft_context, tools=None):
        """One writer request: the whole quiz, or edits to the request's previous quiz or to the writer's last compile (`resolved`).
        `tools` (a subscription connection only) makes the writer's tools for this request's previous quiz."""
        names, previous = draft_context['versions'], payload.get('previous')

        def check(output, metadata):
            compiled = compiled_from(metadata, previous, names) if isinstance(output, dict) and output.get('base') == 'compiled' else None
            quiz = resolved(output, previous, compiled, names)
            cleaned = quiz_latex.clean(quiz, names, title_line=draft_context['title_line'], total=draft_context['total'])
            quiz_blueprints.check(cleaned, draft_context.get('blueprint'))
            copied = bank_copies(cleaned, payload.get('bank_questions'))
            if copied:
                raise ValueError(' '.join(copied))
            return (cleaned,
                    {'notes': text(output.get('notes', ''), 'Notes', 4000).strip(), 'changes': text(output.get('changes', ''), 'Changes', 4000).strip(),
                     'rubric_feedback': [text(p.get('rubric_feedback', '') or '', 'Rubric feedback', 4000).strip() for p in quiz['problems']]})
        return self._quiz_ask(task, endpoint, settings, step, 'quiz_write', payload, images, check, faults_key='faults',
                              tools=tools(previous) if tools else None)

    def _quiz_writer_tools(self, draft, draft_context, endpoint):
        """What the writer may call on a Claude or ChatGPT subscription connection (`providers/tools.offered`), else None; otherwise a
        function making one request's tools from its previous quiz: compile the quiz it is about to answer with, exactly as Faculty will
        (`resolved`, `quiz_latex.clean`, then `_quiz_build` in the document sandbox), whole or as edits to its last compile, and look at
        the pages that compile printed; and read the course's lecture notes and question bank, as the rubric writers do (`drafting_tools`;
        requirement, 2026-10-01). Faculty still compiles the answer and sends back any fault that remains (`FIX_ROUNDS`)."""
        from .providers import tools
        if not tools.offered(endpoint):
            return None
        return lambda previous: self._quiz_writer_toolset(draft, draft_context, previous)

    def _quiz_writer_toolset(self, draft, draft_context, previous):
        from .providers import tools
        names, last, state = draft_context['versions'], {}, {'compiled': None}

        class WriterTools(tools.ToolSet):
            def session(self):
                # A refused answer asked again starts over: no compile of the first attempt is its base (`compiled_from`).
                last.clear()
                state['compiled'] = None
                return super().session()

        def compile_quiz(arguments):
            try:
                kept = resolved(arguments, previous, state['compiled'], names)
                quiz = quiz_latex.clean(kept, names, title_line=draft_context['title_line'], total=draft_context['total'])
                quiz_blueprints.check(quiz, draft_context.get('blueprint'))
            except (ValueError, TypeError, KeyError, AttributeError) as refused:
                raise tools.ToolError(f'Faculty would refuse this quiz before compiling it: {refused}') from None
            built, faults = self._quiz_build(quiz)
            report = compile_report(quiz, built, faults)
            last.clear()
            last.update(built)
            state['compiled'] = kept   # the base of the next edits, and of an answer with base "compiled", as `compiled_from` rebuilds it
            return report

        def look_at_pages(arguments):
            from document_identity import process
            letter, wanted = arguments.get('version'), arguments.get('pages') or [1]
            if letter not in names:
                raise tools.ToolError(f"Name one version: {', '.join(names)}.")
            if letter not in last:
                raise tools.ToolError(f'Version {letter} has no compiled pages: compile the quiz with compile_quiz first, and fix its faults.')
            count = last[letter]['pages']
            if not isinstance(wanted, list) or not 1 <= len(wanted) <= tools.IMAGES or any(type(n) is not int or not 1 <= n <= count for n in wanted):
                raise tools.ToolError(f'Choose 1 to {tools.IMAGES} of its pages, from 1 to {count}.')
            rendered = process.run('look', last[letter]['pdf'], {'pages': sorted(set(wanted))})
            shown = [page['page'] for page in rendered['pages']]
            return (f"Version {letter}, page{'s' if len(shown) > 1 else ''} {', '.join(map(str, shown))} of {count}, as your last compile_quiz printed "
                    'them (the empty boxes at the top right and bottom left are where the two identity stamps go).',
                    [self._quiz_picture(page['png'], 1275) for page in rendered['pages']])

        quiz_schema = {'type': 'object', 'properties': {
            'base': {'type': 'string', 'enum': list(BASES), 'description': (
                '"none": the whole quiz in sections_line, directions and problems; "previous": the previous quiz you were given, changed by '
                'edits; "compiled": the quiz your last successful compile_quiz compiled, changed by edits.')},
            'sections_line': {'type': 'string', 'description': 'With base "none", the sections line; else "" to keep it, or its new text.'},
            'directions': {'type': 'string', 'description': 'With base "none", the directions; else "" to keep them, or their new text.'},
            'problems': {'type': 'array', 'items': {'type': 'object'}, 'description': (
                'With base "none", every problem with every version, exactly as your final answer gives them; else [].')},
            'edits': {'type': 'array', 'items': {'type': 'object'}, 'description': (
                'With base "previous" or "compiled", the edits, exactly as your final answer gives them; else [].')}},
            'required': ['base']}
        look_schema = {'type': 'object', 'properties': {'version': {'type': 'string', 'enum': names},
                                                        'pages': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'maxItems': tools.IMAGES}},
                       'required': ['version', 'pages']}
        # A draft's own problems are never in the bank: they join it only when its approval completes, and an approved draft is never revised.
        return WriterTools('quiz-writer-v3', [
            tools.Tool('compile_quiz', 'Compile every version of a quiz as Faculty will compile your answer: it reports, per version, the pages '
                       'printed, where each problem landed and the room left to write, and every fault Faculty would send back (LaTeX errors, '
                       'a problem off its planned page, more pages than planned, something in the stamp box). It takes the fields of your '
                       'answer: base, sections_line, directions, problems and edits.', quiz_schema, compile_quiz),
            tools.Tool('look_at_pages', 'Pictures of pages of one version as your last compile_quiz printed it, to check figures, tables, '
                       'spacing and the room to write.', look_schema, look_at_pages),
            notes_tool(self, draft['definition_id']), bank_tool(self, draft['definition_id'], None)],
            budget=20, guide=("You may read the course's lecture notes for the sections the quiz covers (lecture_notes) and see how this "
                              "course's question bank has asked and solved similar problems (question_bank), to follow the methods, notation "
                              "and wording the course teaches; a bank question may serve as the model for a problem. The request's "
                              'instructions, format and material still decide what the quiz asks. '
                              'Before you answer, compile your quiz with compile_quiz and fix every fault it reports, then look at the printed '
                              'pages with look_at_pages to check the figures, tables, spacing and room to write against the past quizzes. '
                              'Compile again after a change, as edits with base "compiled" (only what changed, never the whole quiz again). '
                              'When your last compile is the quiz you want, answer with base "compiled" and no edits: Faculty takes that '
                              'very quiz and compiles it once more.'))

    def _quiz_fixed(self, task, endpoint, settings, label, quiz, extra, faults, draft, draft_context, request, tools=None, writer=None, asked=()):
        """Compile, and send faults back to the writer until none are left or the rounds run out. `writer`: the request that fixes them
        (a makeup's own, `MakeupDraftsMixin._makeup_write`); the quiz writer by default."""
        built, found = self._quiz_build(quiz)
        for round_ in range(1, FIX_ROUNDS + 1):
            if not found:
                return quiz, extra, built, []
            payload = fix_request(request, quiz, found, asked)
            (quiz, fixed), _ = (writer or self._quiz_write)(task, endpoint, settings, f'{FIX}{label}{round_}', payload, [], draft_context, tools)
            extra = {**extra, 'rubric_feedback': extra['rubric_feedback'] if len(extra['rubric_feedback']) == len(quiz['problems']) else fixed['rubric_feedback'],
                     'notes': fixed['notes'] or extra['notes'], 'changes': after_fix(extra, fixed)}
            built, found = self._quiz_build(quiz)
        return quiz, extra, built, found

    def run_quiz_revision(self, task):
        with self.rosters.lock(), connect(self.path) as db:
            requested = db.execute('SELECT input_json FROM quiz_drafts WHERE id=?', (task['draft_id'],)).fetchone()
        if requested and json.loads(requested[0]).get('makeup'):
            return self.run_makeup_revision(task)  # a makeup version (GRADING "MAKEUP VERSIONS", grading/makeup_drafts.py)
        context, profile = json.loads(task['input_json']), json.loads(task['profile_json'])
        if context.get('rubric_answers'):
            return self._run_rubric_answers(task, context, profile)  # only the rubric check's Resolve step: nothing of the quiz changes
        endpoint, settings = profile['endpoint'], profile['settings']
        mode = scoring_mode(context['mode'])
        status, result, metadata, error, after = 'complete', None, None, '', ''
        try:
            with self.rosters.lock(), connect(self.path) as db:
                draft = self._quiz_draft(db, task['draft_id'])
                previous = db.execute('SELECT * FROM quiz_draft_revisions WHERE id=?', (task['based_on'],)).fetchone() if task['based_on'] else None
                asked = asked_changes(db, task)
            draft_context = json.loads(draft['input_json'])
            names = draft_context['versions']
            request = {k: draft_context[k] for k in ('course', 'term', 'title', 'total', 'versions')}
            if draft_context.get('format'):
                request['format'] = draft_context['format']
            request['instructions'] = draft_context['instructions']
            if draft_context.get('blueprint'):
                request['blueprint'] = draft_context['blueprint']
                request['instructions'] += '\n' + quiz_blueprints.INSTRUCTION
            if draft_context['examples']:
                request['examples'] = [{'quiz': e['assignment_title'], 'pages': e['pages']} for e in draft_context['examples']]
            material, material_pictures = self._quiz_material(draft, draft_context)
            example, example_pictures = self._quiz_examples(draft_context)
            images, shown = [], []
            for source, page, picture in example_pictures + material_pictures:
                if len(images) >= PICTURES or sum(map(len, images)) + len(picture) > PICTURE_BYTES:
                    continue
                shown.append({'picture': len(images), 'file': source, 'page': page})
                images.append(picture)
            payload = {'request': request, 'material': material, 'example': example, 'pictures': shown}
            # NOTES SECTIONS (requirement, 2026-10-28): the sections chosen for the draft go to the writer on every connection.
            writer_notes, writer_notes_warning = chosen_notes(self, draft['definition_id'], draft_context.get('notes_sections'))
            if writer_notes:
                payload['course_notes'] = writer_notes
            writer_bank, writer_bank_warning = chosen_bank(self, draft['definition_id'], draft_context.get('notes_sections'))
            if writer_bank:
                payload['bank_questions'] = writer_bank
            earlier = json.loads(previous['result_json']) if previous else None
            if earlier:
                payload.update(previous=written(earlier['quiz']), feedback=context['feedback'])
            writer_tools = self._quiz_writer_tools(draft, draft_context, endpoint)
            (quiz, extra), write_receipt = self._quiz_write(task, endpoint, settings, WRITE, payload, images, draft_context, writer_tools)
            quiz, extra, built, faults = self._quiz_fixed(task, endpoint, settings, '', quiz, extra, [], draft, draft_context, request, writer_tools, asked=asked)
            if faults:
                raise ValueError('The quiz could not be laid out after ' + str(FIX_ROUNDS) + ' rounds of fixes: ' + ' '.join(faults[:4]))
            pages = self._quiz_pages(built)
            # One look at the printed pages: what it finds goes back to the writer once, then the instructor sees the rest. A problem the
            # revision this one is based on already showed a check exactly as it prints now is not asked about again (2026-10-11).
            check, shown = self._quiz_checked(task, endpoint, settings, request, quiz, built, pages, example_pictures, earlier, asked)
            fixed_after_check = False
            if check['issues']:
                payload_fix = fix_request(request, quiz, [f"Version {i['version']}, problem {i['problem']}: {i['issue']} Fix: {i['fix']}"
                                                          if i['problem'] else f"Version {i['version']}: {i['issue']} Fix: {i['fix']}" for i in check['issues']], asked)
                (candidate, fixed), _ = self._quiz_write(task, endpoint, settings, FIX + 'check', payload_fix, [], draft_context, writer_tools)
                candidate_extra = {**extra, 'notes': fixed['notes'] or extra['notes'], 'changes': after_fix(extra, fixed),
                                   'rubric_feedback': extra['rubric_feedback'] if len(extra['rubric_feedback']) == len(candidate['problems']) else fixed['rubric_feedback']}
                candidate, candidate_extra, candidate_built, candidate_faults = self._quiz_fixed(task, endpoint, settings, 'check:', candidate, candidate_extra, [],
                                                                                                 draft, draft_context, request, writer_tools, asked=asked)
                if not candidate_faults:
                    quiz, extra, built, fixed_after_check = candidate, candidate_extra, candidate_built, True
                    pages = self._quiz_pages(built)
            notes, notes_warning = quiz_notes(self, draft['definition_id'], [quiz['sections_line'], *(p['tag'] for p in quiz['problems'])],
                                              draft_context.get('notes_sections') or ())
            rubrics_ = self._quiz_rubrics(task, endpoint, settings, quiz, extra, pages, mode, earlier, names, draft, notes)
            waits = [r['wait'] for r in rubrics_ if r.get('wait')]
            if waits:
                raise RetryLater(min(waits), 'A rubric request is waiting for its host.')
            # Each problem's answer key, read once with the worked solution in view and against the lines its question will get
            # (requirement, 2026-10-11); a problem whose rubric was carried keeps the key read with it.
            rubrics_ = self._quiz_keys(task, endpoint, settings, quiz, pages, rubrics_, names, lambda entry, problem, letter: (
                f"Question {problem['number']}", version_rules(entry['rubric'], letter, mode, problem['points'], problem.get('parts'))[1],
                problem['content'][letter]['solution'] or next((v for v in entry['rubric']['versions'] if v['version'] == letter), {}).get('solution', '')))
            result = self._publish_quiz_files(draft, task, built, pages)
            # Last, the rubric check the plan would run after approval, on what approving would create (requirement, 2026-10-11); a problem whose rubric and key were carried, and that reads as before, keeps its result.
            checked, receipts = self._quiz_rubric_check(task, profile, draft, quiz, rubrics_, mode, names, result['versions'],
                                                        {letter: [page['png'] for page in pages[letter]] for letter in names}, previous)
            warnings = [w for w in (writer_notes_warning, writer_bank_warning, notes_warning) if w]
            for letter in names:
                warnings += [f'Version {letter}: {note}' for note in built[letter]['layout']['notes']]
            if check['issues'] and not fixed_after_check:
                warnings += [f"Check, Version {i['version']}{', problem ' + str(i['problem']) if i['problem'] else ''}: {i['issue']}" for i in check['issues']]
            for r in rubrics_:
                warnings += [f"Problem {r['problem']} rubric: {w}" for w in (r.get('rubric') or {}).get('warnings', [])]
                if r.get('error'):
                    warnings.append(f"Problem {r['problem']}: its rubric could not be drafted ({r['error']}). Ask for changes to draft it again.")
            warnings += key_warnings(quiz, rubrics_, names)
            if checked and checked['status'] != 'complete':  # a warning, never a failed revision: the quiz, its rubrics and keys stand
                warnings.append(f"{CHECK_FAILED} ({checked['error']}). Key & Rubric Finalization checks them once the quiz is approved.")
            result.update(rule=RULE, mode=mode, quiz=quiz, notes=extra['notes'], changes=extra['changes'] if earlier else '',
                          check=check_record(check, fixed_after_check, shown, self._quiz_printed(request, quiz, built, example_pictures) if fixed_after_check else shown),
                          rubrics=[{k: v for k, v in r.items() if k != 'wait'} for r in rubrics_], warnings=warnings,
                          rubric_notes=[s['number'] for s in notes['sections']] if notes else [],
                          **({'writer_notes': [s['number'] for s in writer_notes['sections']] if writer_notes else [],
                              'writer_bank': {s['number']: len(s['questions']) for s in writer_bank['sections']} if writer_bank else {}}
                             if draft_context.get('notes_sections') else {}),
                          toolchain={letter: built[letter]['details'] for letter in names}, **({'rubric_check': checked} if checked else {}))
            if draft_context.get('blueprint'):
                result['blueprint_coverage'] = quiz_blueprints.check(quiz, draft_context['blueprint'])
            if not any(r.get('rubric') for r in rubrics_):
                raise ValueError('No rubric could be drafted for this quiz. ' + ' '.join(r.get('error', '') for r in rubrics_[:2]))
            metadata = {'write': {'metadata': write_receipt['metadata']}, **({'rubric_check': receipts} if receipts else {})}
        except RetryLater as waiting:
            status, after = 'pending', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound) as failure:
            status, error = 'failed', f'The quiz could not be drafted: {failure}'
        self._publish_quiz_revision(task, status, result, metadata, error, after)

    def _publish_quiz_revision(self, task, status, result, metadata, error, after, **detail):
        """A running revision's outcome written once: complete or failed ends it with its event (`detail` rides with the event), pending
        waits for `after`. A revision cancelled or scrapped meanwhile is not running and keeps its status."""
        with self.rosters.lock(), connect(self.path) as db:
            published = db.execute("UPDATE quiz_draft_revisions SET status=?,result_json=?,provider_json=?,error=?,not_before=?,updated_at=? WHERE id=? AND status='running'",
                                   (status, encoded(result) if result and status == 'complete' else None, encoded(metadata) if metadata else None, error, after, timestamp(), task['id']))
            if published.rowcount and status in ('complete', 'failed'):
                self.exams.documents.event(db, 'quiz_draft_revision_' + ('ready' if status == 'complete' else 'failed'), 'worker',
                                           {'draft_id': task['draft_id'], 'revision_id': task['id'], **detail, **({'error': error} if error else {})})

    @staticmethod
    def _quiz_printed(request, quiz, built, example_pictures):
        """`printed_keys` of a compiled quiz, against the example pages its check is sent (the first `CHECK_EXAMPLES`)."""
        return printed_keys(request, quiz, built, [picture for *_, picture in example_pictures[:CHECK_EXAMPLES]])

    def _quiz_checked(self, task, endpoint, settings, request, quiz, built, pages, example_pictures, earlier, asked=()):
        """The one look at the printed pages (`quiz_check`), less what the revision this one is based on already showed a check
        (requirement, 2026-10-11, among the small repeats they chose to remove; what it judges is as it was).
        A problem whose `printed_key` is one that revision's check SAW (`check_record`) is not asked about again: the findings about it
        that still stood are carried as they were and join this check's, so they go back to the writer with them and are shown when the
        fix does not hold, as if read again. The request names those problems `unchanged` (since `quiz_check` v3) and leaves out the pages
        that hold only such problems; the quiz's data are sent whole, so what concerns the whole quiz (what was asked for, the points and
        problems, each page shown) is judged again whenever anything changed. Every page is sent when the earlier check left a finding
        about the whole quiz (problem 0): nothing says which page it was about, and this check must be able to find it again. Nothing
        printed changed since a check saw the whole quiz: no request at all, and everything it found stands. No earlier revision, one
        recorded before 2026-10-11 (no `seen`) or no key that matches: every problem and page, exactly as before. Returns the check
        ({issues, notes, unchanged}) and the keys of the quiz as checked."""
        names, examples = quiz['versions'], example_pictures[:CHECK_EXAMPLES]
        keys, whole = shown = self._quiz_printed(request, quiz, built, example_pictures)
        before = ((earlier or {}).get('check') or {}).get('seen') or {}
        known, stood = before.get('problems') or {}, before.get('issues') or []
        unchanged = [number_ for number_ in keys if known.get(str(number_)) == keys[number_]]
        if before.get('whole') == whole:
            return {'issues': stood, 'notes': earlier['check'].get('notes', ''), 'unchanged': unchanged}, shown
        every = len(unchanged) in (0, len(keys)) or any(not i['problem'] for i in stood)
        wanted = {problem['page'] for problem in quiz['problems'] if problem['number'] not in unchanged}
        check_images, check_shown = [], []
        for letter in names:
            for page in pages[letter]:
                if every or page['number'] in wanted:
                    check_shown.append({'picture': len(check_images), 'version': letter, 'page': page['number']})
                    check_images.append(self._quiz_picture(page['png'], 1275))
        # The past quizzes to imitate, after the drafted pages: room to write and layout are judged against the instructor's own.
        for source, page, picture in examples:
            check_shown.append({'picture': len(check_images), 'example': source, 'page': page})
            check_images.append(picture)
        check_payload = {'request': request, 'quiz': {'versions': names, 'total': quiz['total'], **written(quiz)}, 'pictures': check_shown,
                         **({'unchanged': unchanged} if unchanged else {}), **({'asked': asked} if asked else {})}
        checker = {**endpoint, 'max_tokens': max(endpoint['max_tokens'], 16384)}
        found, _ = self._quiz_ask(task, checker, settings, CHECK, 'quiz_check', check_payload, check_images, self._quiz_check_result(quiz))
        carried = [i for i in stood if i['problem'] in unchanged]
        return {'issues': carried + [i for i in found['issues'] if i not in carried], 'notes': found['notes'], 'unchanged': unchanged}, shown

    @staticmethod
    def _quiz_check_result(quiz):
        def check(value, _metadata=None):
            if not isinstance(value, dict):
                raise ValueError('The check returned an invalid object.')
            found = []
            for issue in items(value.get('issues'), 'Issues', 60):
                if not isinstance(issue, dict) or issue.get('version') not in (*quiz['versions'], 'all'):
                    raise ValueError('Each issue names one version, or all.')
                problem = issue.get('problem')
                if type(problem) is not int or not 0 <= problem <= len(quiz['problems']):
                    raise ValueError('Each issue names a problem of the quiz, or 0.')
                found.append({'version': issue['version'], 'problem': problem, 'kind': issue.get('kind') if issue.get('kind') in ('correctness', 'figure', 'layout', 'versions', 'wording') else 'wording',
                              'issue': text(issue.get('issue'), 'Issue', 2000, False).strip(), 'fix': text(issue.get('fix', ''), 'Fix', 2000).strip()})
            return {'issues': found, 'notes': text(value.get('notes', ''), 'Notes', 2000).strip()}
        return check

    def _quiz_rubrics(self, task, endpoint, settings, quiz, extra, pages, mode, earlier, names, draft, notes=None):
        """Each problem's rubric for all its versions at once, in the all-versions rubric writer's shape. A problem unchanged since the
        revision it is based on, with nothing asked about its grading and the same lecture notes, keeps that revision's rubric. On a
        subscription connection the writer may look closer at the printed pages and read the course's notes, question bank and approved
        rubrics (`drafting_tools`). Every writer reads the instructor's instructions for the model, given with the request
        (`instructor_instructions`), and the text of the notes sections the quiz covers (`course_notes`, `drafting_tools.quiz_notes`)."""
        notes_key = digest(notes) if notes else ''
        context_tools = rubric_tools(self, endpoint, draft['definition_id'], {letter: [page['png'] for page in pages[letter]] for letter in names})
        carried = {}
        if earlier:
            before = {r['signature']: r for r in earlier['rubrics'] if r.get('rubric') and r.get('signature')}
            carried = before
        writer = {**endpoint, 'max_tokens': max(endpoint['max_tokens'], 16384)}
        instructions = json.loads(draft['input_json']).get('instructions', '')
        standing = self._quiz_standing(draft, task)
        chosen = rubric_choices.stated(json.loads(draft['input_json']).get('rubric_choices'))

        def one(index_problem):
            index, problem = index_problem
            sig = signature(problem, mode)
            feedback = extra['rubric_feedback'][index] if index < len(extra['rubric_feedback']) else ''
            # A rubric drafted with other notes, or none (every revision before quiz_rubric v3), is drafted again with these.
            if sig in carried and not feedback and carried[sig].get('notes_sha256', '') == notes_key:
                return {**carried[sig], 'problem': problem['number'], 'carried_from': earlier.get('revision_id') or carried[sig].get('carried_from')}
            family = {'id': f"P{problem['number']}", 'versions': [
                {'version': letter, 'number': str(problem['number']), 'label': f"{problem['number']}.", 'title': f"Question {problem['number']}",
                 'points': problem['points'], 'points_text': quiz_latex.points_text(problem['points']), 'parts': problem['parts'],
                 'statement': statement_text(problem['content'][letter]), 'pages': [problem['page']]} for letter in names]}
            shown, images = [], []
            for letter in names:
                page = pages[letter][problem['page'] - 1]
                shown.append({'picture': len(images), 'version': letter, 'page': problem['page']})
                images.append(self._quiz_picture(page['png'], 1275))
            payload = {'mode': mode, 'pictures': shown, 'problem': {'versions': [
                {'version': letter, 'number': str(problem['number']), 'points': problem['points'], 'parts': problem['parts'],
                 'statement': family['versions'][i]['statement'],
                 'choices': [{'letter': quiz_latex.LETTERS[j].lower(), 'text': choice} for j, choice in enumerate(problem['content'][letter]['choices'])],
                 'correct_choice': problem['content'][letter]['correct_choice'], 'author_solution': problem['content'][letter]['solution'],
                 'author_final_answers': problem['content'][letter]['final_answers']} for i, letter in enumerate(names)]}}
            if instructions:
                payload['instructor_instructions'] = instructions
            if feedback:
                payload['instructor_feedback'] = feedback
            if notes:
                payload['course_notes'] = notes
            if standing:  # RUBRIC MEMORIES (2026-10-14): the course's kept rules and watches as they stood when the revision was asked for
                payload['standing_rules'] = standing
            if chosen:  # RUBRIC CHOICES (2026-10-24): the instructor's quick answers for this quiz
                payload['assignment_choices'] = chosen
            prior = next((r for r in (earlier or {}).get('rubrics', []) if r.get('problem') == problem['number'] and r.get('rubric')), None)
            if prior and (feedback or sig not in carried):
                payload['previous_rubric'] = {'points': prior['rubric']['points'], 'items': [
                    {'points': line['points'], 'descriptions': line['descriptions']} for line in prior['rubric']['items']]}
            try:
                drafted, _ = self._quiz_ask(task, writer, settings, f"{RUBRIC}{problem['number']}", 'quiz_rubric', payload, images,
                                            lambda output, _metadata: family_rubric(output, family, mode, names), tools=context_tools)
                return {'problem': problem['number'], 'signature': sig, 'rubric': drafted, 'notes_sha256': notes_key}
            except RetryLater as waiting:
                return {'problem': problem['number'], 'wait': waiting.seconds}
            except (ProviderError, ValueError, TypeError, KeyError) as failure:
                return {'problem': problem['number'], 'signature': sig, 'error': str(failure)}
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(quiz['problems'])))) as pool:
            return list(pool.map(one, enumerate(quiz['problems'])))

    def _quiz_standing(self, draft, revision):
        """The course's rubric memories as they stood when `revision` was asked for (GRADING "RUBRIC MEMORIES"): what its rubric writer
        and its rubric check read, the same on every retry of the revision. [] when the course has none."""
        from .rubric_memory import standing_rules
        with connect(self.path) as db:
            return standing_rules(db, draft['definition_id'], revision['created_at'])

    def _quiz_keys(self, task, endpoint, settings, quiz, pages, entries, names, known):
        """Each problem's answer key on each version, read ONCE while the quiz is drafted (requirement, 2026-10-11). Measured that day on the live catalog: the drafting model worked each answer out three times (writer, check, rubric
        writer), yet a question was created without a key, no key could be read from a deducting rubric (0 of 16 and 0 of 15 questions
        of the two drafted quizzes), and every key came from a reading asked for afterwards that was sent neither the worked solution
        nor the writer's answers and solved the problem a fourth time; the rubric check queued at approval ran before any key existed.
        The writer's own `final_answers` cannot be the key: they are composite prose the conservative matcher cannot compare (0 of 21
        questions comparable), where the reader's keys have the right form (a tableau question became 10 labelled parts).

        So the SAME reader contract as the plan's own reading (`rubric_generation.KEY_RULE`: prompt `answer_key_read`, its schema, the
        validator `read_key`, the request `key_request`) is asked here with what the draft already knows: the lines exactly as the
        question will get them and which is full credit (`known(entry, problem, letter)` gives the question's title, those rules and
        the worked solution), the problem as written (`statement_text`), the writer's final answers and correct choice, and the
        printed page that holds the problem. One request per problem and version, each owned by the revision under its own step
        (`KEY`), in parallel as the rubrics are; multiple-choice problems are read the same way.

        A reading is kept with its problem's rubric entry (`keys`: {letter: {rule, key, basis, unreadable}}, and `error` when it
        failed), so a rubric CARRIED from the revision this one is based on (`_quiz_rubrics`, the same `signature`) carries the key
        read with it and nothing is asked; a version carried without a key is read again. A reading that fails or finds the problem
        unreadable never fails the revision: that problem and version has no key (`key_warnings`). A reading waiting for its host
        makes the revision wait, as a rubric does. The draft's page shows each key; nothing is saved until an approval that says
        `save_keys`. A key is evidence only: it selects no rubric line. Returns the entries with their keys."""
        held = {entry['problem']: entry for entry in entries if entry.get('rubric')}
        wanted = [(problem, letter) for problem in quiz['problems'] for letter in names
                  if problem['number'] in held and not ((held[problem['number']].get('keys') or {}).get(letter) or {}).get('key')]

        def one(problem_letter):
            problem, letter = problem_letter
            content = problem['content'][letter]
            try:
                title, rules, solution = known(held[problem['number']], problem, letter)
                pictures = [self._quiz_picture(pages[letter][problem['page'] - 1]['png'], 1275)] if endpoint.get('modality') == 'vision' else []
                payload = key_request({'title': title, 'printed_number': str(problem['number']), 'max_points': problem['points']},
                                      [{'id': item['id'], 'points': item['points'], 'description': item['description']} for item in rules['items']],
                                      answers.rubric_roles(rules)['full'], statement_text(content), len(pictures), worked_solution=solution,
                                      author_answers=content['final_answers'], correct_choice=content['correct_choice'])
                found, _ = self._quiz_ask(task, endpoint, settings, f"{KEY}{problem['number']}:{letter}", 'answer_key_read', payload, pictures,
                                          lambda output, _metadata: read_key(output))
                return {'rule': KEY_RULE, **found}
            except RetryLater as waiting:
                return {'wait': waiting.seconds}
            except (ProviderError, ValueError, TypeError, KeyError) as failure:
                return {'rule': KEY_RULE, 'key': [], 'basis': [], 'unreadable': '', 'error': str(failure)}
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(wanted)))) as pool:
            read = list(zip(wanted, pool.map(one, wanted)))
        waits = [reading['wait'] for _, reading in read if 'wait' in reading]
        if waits:
            raise RetryLater(min(waits), 'An answer-key request is waiting for its host.')
        return [{**entry, 'keys': {**(entry.get('keys') or {}), **{letter: reading for (problem, letter), reading in read if problem['number'] == entry['problem']}}}
                if entry.get('rubric') else entry for entry in entries]

    # The rubric check while the quiz is drafted (requirement, 2026-10-11) -------------------------
    @staticmethod
    def _draft_reviewer(settings, context):
        """The reviewer's connection for a draft's check, with its reasoning choice and sized for one problem with every version, as
        the plan's own check sizes it (`rubric_reviewer_profile`), from the settings the revision was requested with: a revision
        that waits and comes back asks the same connection, so what it already bought is replayed."""
        versions = len(context['versions'])
        return {'endpoint': review_budget(config.rubric_reviewer(settings), context, versions if versions > 1 else 1), 'settings': settings}

    def _draft_check_run(self, task, reviewer, context, host):
        """One turn of the check for a revision (`rubric_quality.run_rubric_check`): (result, receipts), or (None, why it failed). A
        request that waits for its host makes the revision wait, as a rubric's or a key's does."""
        status, result, metadata, error, after = self.run_rubric_check(task, reviewer, context, host)
        if status == 'pending':
            raise RetryLater(max(1, (datetime.fromisoformat(after) - datetime.now(timezone.utc)).total_seconds()), 'The rubric check is waiting for its host.')
        return (result, metadata) if status == 'complete' else (None, error)

    def _quiz_rubric_check(self, task, profile, draft, quiz, entries, mode, names, versions, pages, previous):
        """A revision's LAST step: the rubric check the plan would run once the quiz is approved, on what approving would create.
        Measured on the live catalog (2026-10-11): the check ran only after approval, so its concerns, real grading-policy decisions,
        waited for a second sitting, and each of Quiz 4's 10 check runs asked every rubric again. Here the same check
        (`rubric_quality.run_rubric_check`: the same prompts, rules, scheme, links and validation, every version of a problem
        side by side) reads `draft_check_context` through `DraftCheckHost`: each request the revision's own under the check's
        step names (`CHECK_STEPS`), the reviewer's connection sized as the plan's check sizes it (`_draft_reviewer`).

        A PROBLEM THAT READS AS BEFORE KEEPS ITS RESULT, like a settled question (`rubric_quality.settled_entries`): where its rubric
        was carried from the revision this one is based on (`_quiz_rubrics`; its keys come with it), and its questions' `content`
        and `reference` digests are what that revision's check recorded for it (`check_digests`: lines, points, comments, problem,
        solution and key), that check's entries, findings, links and answered decisions for it stand and nothing is asked. Judged
        per problem, never per request: other problems' comments travel in a problem's payload. Its pictures are not compared
        (a rubric is carried when only spacing or a figure's drawing changed, `signature`).

        Returns (what the revision keeps, its receipts). Kept in the result (`rubric_check`): `status`, the reviewer's `profile`,
        `questions` ({virtual ID: {problem, version, content, reference, printed}}), `problems` ({number: {questions, digest}} and
        `carried_from` where kept), each version's `pdf` SHA-256, the check's `result` under the virtual IDs, and `sha256`
        (`check_sha`). The receipts go to the revision's provider record, in a check's own shape. A check that FAILS is kept as
        {status: 'failed', error} and is a warning, never a failed revision (the quiz, its rubrics and keys stand, and the approval
        queues the plan's check as before); only a wait or a cancellation leaves this step. (None, None) while a problem has no
        rubric: nothing can be approved yet.

        THE SWITCH (requirement, 2026-10-11): off in the settings the revision was requested with, this step is not taken, (None,
        None), so the revision holds no check, nothing is asked of the reviewer, and the approval queues the plan's check as it did
        before 2026-10-11. Read from the revision's own settings, never the current ones: a revision that waits and comes back ends
        the way it was asked to. A check an earlier revision holds is untouched; a later revision asked for with the switch on
        checks every problem again (nothing to keep a result from)."""
        if any(not entry.get('rubric') for entry in entries) or not profile['settings'].get('draft_rubric_check', True):
            return None, None
        try:
            context = draft_check_context(task['id'], quiz, entries, mode, names, versions)
            standing = self._quiz_standing(draft, task)
            if standing:  # RUBRIC MEMORIES: as the plan's check sends them, no part of a question's digests
                context['standing_rules'] = standing
            chosen = rubric_choices.stated(json.loads(draft['input_json']).get('rubric_choices'))
            if chosen:  # RUBRIC CHOICES (2026-10-24): likewise
                context['assignment_choices'] = chosen
            reviewer = self._draft_reviewer(profile['settings'], context)
            letter_of = {virtual(problem['number'], letter): (problem['number'], letter) for problem in quiz['problems'] for letter in names}
            pdf = {letter: versions[letter]['files']['main.pdf']['sha256'] for letter in names}
            questions = {q['question_id']: {'problem': letter_of[q['question_id']][0], 'version': letter_of[q['question_id']][1],
                                            **check_digests(q, pdf[letter_of[q['question_id']][1]])} for q in context['questions']}
            problems = {str(problem['number']): {'questions': [virtual(problem['number'], letter) for letter in names]} for problem in quiz['problems']}
            for row in problems.values():
                row['digest'] = digest([[questions[qid]['content'], questions[qid]['reference']] for qid in row['questions']])
            earlier = json.loads(previous['result_json']) if previous else {}
            before = earlier.get('rubric_check') or {}
            same_reviewer = lambda: ({k: v for k, v in before['profile']['endpoint'].items() if k != 'max_tokens'}
                                     == {k: v for k, v in reviewer['endpoint'].items() if k != 'max_tokens'})
            kept = {}
            if before.get('rule') == DRAFT_CHECK and before.get('status') == 'complete' and same_reviewer():
                carried = {str(entry['problem']) for entry in entries if entry.get('carried_from')}
                kept = {number_: row for number_, row in problems.items() if number_ in carried and (was := (before.get('problems') or {}).get(number_))
                        and was['questions'] == row['questions'] and was['digest'] == row['digest']}
            ids = {qid for row in kept.values() for qid in row['questions']}
            part = check_part(before.get('result') or {}, ids)
            old = (json.loads(previous['provider_json'] or '{}').get('rubric_check') or {}) if kept else {}
            receipts = {name: {qid: value for qid, value in (old.get(name) or {}).items() if qid in ids} for name in ('questions', 'problem_images', 'problem_summaries')}
            if len(kept) == len(problems):  # nothing reads otherwise than that revision's check read it: no request at all
                result = before['result']
            else:
                run = {**context, 'settled': {'questions': {q['question_id']: q for q in part['questions']}, **({'links': part['line_links']} if part['line_links'] else {})}} if kept else context
                result, metadata = self._draft_check_run(task, reviewer, run, DraftCheckHost(self, draft['definition_id'], pages))
                if result is None:
                    return {'rule': DRAFT_CHECK, 'status': 'failed', 'error': metadata}, None
                receipts = {name: {**receipts[name], **(metadata.get(name) or {})} for name in receipts} | {k: v for k, v in metadata.items() if k not in receipts}
                if kept:
                    order = {q['question_id']: n for n, q in enumerate(context['questions'])}
                    asked, named = len(problems) - len(kept), [f'Problem {number_}' for number_ in kept]
                    found = {k: len(part[k]) + len(result.get(k) or []) for k in ('suspected_errors', 'consistency_issues')}
                    result = check_joined(result, part, order, (
                        f"{asked} problem{'s' if asked != 1 else ''} checked in this revision, each "
                        f"{'with all its versions side by side in one request' if len(names) > 1 else 'alone in its own request'}. "
                        f"{', '.join(named[:-1]) + ' and ' + named[-1] if len(named) > 1 else named[0]} read{'s' if len(named) == 1 else ''} as in the revision before and "
                        f"keep{'s' if len(named) == 1 else ''} {'its' if len(named) == 1 else 'their'} check. "
                        f"In all {sum(len(q['clarifications']) for q in result['questions'])} clarifications, {sum(len(q['pairs']) for q in result['questions'])} exclusive pairs, "
                        f"{found['suspected_errors']} suspected errors and {found['consistency_issues']} differences between versions are proposed."
                        + (f" {len(result['unresolved'])} question{'s' if len(result['unresolved']) > 1 else ''} still allow more points than they are worth: decide which of "
                           "their lines are alternatives before you confirm." if result.get('unresolved') else "")))
            for number_ in kept:
                problems[number_]['carried_from'] = (before['problems'][number_].get('carried_from') or earlier.get('revision_id'))
            record = {'rule': DRAFT_CHECK, 'status': 'complete', 'profile': reviewer, 'questions': questions, 'problems': problems, 'pdf': pdf, 'result': result}
            return {**record, 'sha256': check_sha(record)}, receipts
        except (RetryLater, Cancelled):
            raise
        except Exception as failure:  # noqa: BLE001 - whatever stopped it, the drafted quiz stands and the plan's own check follows
            import logging
            logging.getLogger(__name__).exception('the rubric check of quiz revision %s could not be made', task['id'])
            return {'rule': DRAFT_CHECK, 'status': 'failed', 'error': str(failure) or type(failure).__name__}, None

    def _quiz_stored_pages(self, draft, versions):
        """{letter: [PNG of each page]} of a finished revision, read and verified from the definition's storage."""
        from document_identity.storage import read_verified
        with self.rosters.lock(), self.exams.documents.files.files('definition', draft['definition_id']) as root:
            return {letter: [read_verified(root, page['image']['path'], page['image']['sha256']) for page in version['pages']] for letter, version in versions.items()}

    def _run_rubric_answers(self, task, context, profile):
        """A revision that only sends the instructor's answers to the rubric check's decisions (`answer_quiz_rubric_check`). The quiz,
        its rubrics, its keys, its page check and its compiled files are the revision's it is based on, referenced unchanged: no
        writer, no page check, no rubric and no key request, nothing compiled or published. Only the check's own Resolve step runs
        (`rubric_quality.run_rubric_resolution`, rule `RESOLVE`), through `DraftCheckHost`, from the very input the plan's Resolve
        concerns builds (`resolves_input`): every concern of the problems a concern names, the answered ones with their answers,
        starting from the guidance the check holds, with the links as its reviewer left them. Its result replaces the check's (the
        concerns it settled move to `resolution.resolved`), the digests and each version's PDF stay, and the fingerprint is the
        new check's. A resolution that fails or is refused fails this revision alone: the revision it answered stays the one
        shown, with its check as it was, and the page keeps the answers to send again."""
        asked = context['rubric_answers']
        status, result, metadata, error, after = 'complete', None, None, '', ''
        try:
            with self.rosters.lock(), connect(self.path) as db:
                draft = self._quiz_draft(db, task['draft_id'])
                previous = db.execute("SELECT * FROM quiz_draft_revisions WHERE id=? AND status='complete' AND cancelled=0", (task['based_on'],)).fetchone()
            earlier = json.loads(previous['result_json']) if previous else {}
            check = earlier.get('rubric_check') or {}
            if check.get('status') != 'complete' or check.get('sha256') != asked['check_sha256']:
                raise ValueError('the rubric check these answers were written for is no longer the one this revision holds.')
            from . import rubric_links
            names = earlier['quiz']['versions']
            base = draft_check_context(previous['id'], earlier['quiz'], earlier['rubrics'], earlier['mode'], names, earlier['versions'])
            standing = self._quiz_standing(draft, previous)
            if standing:  # RUBRIC MEMORIES: what the check these answers are about was sent
                base['standing_rules'] = standing
            chosen = rubric_choices.stated(json.loads(draft['input_json']).get('rubric_choices'))
            if chosen:  # RUBRIC CHOICES: likewise
                base['assignment_choices'] = chosen
            if any({k: v for k, v in check['questions'][q['question_id']].items() if k in ('content', 'reference', 'printed')}
                   != check_digests(q, check['pdf'][check['questions'][q['question_id']]['version']]) for q in base['questions']):
                raise ValueError('the rubrics no longer read as its rubric check read them. Ask for changes to check them again.')
            reviewer = self._draft_reviewer(profile['settings'], base)
            shown = check['result']
            concerns = answered_concerns(shown, {row['concern']: row['response'] for row in asked['responses']})
            concerns = [{k: v for k, v in concern.items() if k != 'fix'} if n in set(asked.get('unfixed') or []) else concern
                        for n, concern in enumerate(concerns)]
            window = normalize_questions([{k: q[k] for k in ('question_id', 'rubric_id', 'pairs', 'clarifications')} for q in shown['questions']], base)
            links = []
            if len(names) > 1:
                kinds = {virtual(entry['problem'], letter): line_kinds(entry['rubric']['items']) for entry in earlier['rubrics'] for letter in names}
                families = shown.get('families') or []
                sets = rubric_links.effective_sets([{**q, 'line_kinds': kinds[q['question_id']]} for q in base['questions']], families, shown.get('line_links') or [], {},
                                                   {qid for family in families for qid in family} if shown.get('line_links') is not None else ())
                links = resolve_links(sets, {}, base['questions'])
            run = {**base, 'resolves': resolves_input(shown, base['questions'], window, concerns, 'quiz-revision:' + previous['id'], links)}
            resolved, receipts = self._draft_check_run(task, reviewer, run, DraftCheckHost(self, draft['definition_id'], self._quiz_stored_pages(draft, earlier['versions'])))
            if resolved is None:
                raise ValueError(receipts)
            old = json.loads(previous['provider_json'] or '{}').get('rubric_check') or {}
            record = {**{k: v for k, v in check.items() if k != 'sha256'}, 'profile': reviewer, 'result': resolved}
            rubrics, rewritten = answered_comments(earlier['rubrics'], concerns, resolved)
            if rewritten:  # the questions read otherwise now: their digests are what a check of the new comments would record
                fresh = draft_check_context(previous['id'], earlier['quiz'], rubrics, earlier['mode'], names, earlier['versions'])
                digests = {q['question_id']: check_digests(q, check['pdf'][check['questions'][q['question_id']]['version']]) for q in fresh['questions']}
                record['questions'] = {qid: {**row, 'content': digests[qid]['content']} for qid, row in record['questions'].items()}
                record['problems'] = {number_: {**row, 'digest': digest([[record['questions'][qid]['content'], record['questions'][qid]['reference']] for qid in row['questions']])}
                                      for number_, row in record['problems'].items()}
            result = {**earlier, 'revision_id': task['id'], 'changes': '', 'rubrics': rubrics, 'rubric_check': {**record, 'sha256': check_sha(record)},
                      'rubric_answers': {'revision_id': previous['id'], 'concerns': len(concerns), 'answered': len(asked['responses']),
                                         **({'comments_rewritten': rewritten} if rewritten else {})}}
            metadata = {'rubric_check': {**old, **{name: {**(old.get(name) or {}), **(receipts.get(name) or {})} for name in ('questions', 'problem_images', 'problem_summaries')}}}
        except RetryLater as waiting:
            status, after = 'pending', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound, Conflict) as failure:
            status, error = 'failed', f"Your answers could not be resolved: {getattr(failure, 'description', None) or failure}"
        self._publish_quiz_revision(task, status, result, metadata, error, after)

    def _publish_quiz_files(self, draft, task, built, pages):
        """Every version's LaTeX, style, build log, PDF and page pictures, kept in the course definition's verified storage under the
        revision. Publishing is idempotent: a resumed revision writes the same bytes again, which changes nothing."""
        from document_identity.storage import publish
        base = f"quiz-drafts/{draft['id']}/{task['id']}"
        versions, payloads = {}, {}
        for letter, build in built.items():
            files = {}
            for name, content in (('main.tex', build['retained']['main.tex']), (quiz_latex.STYLE, build['retained'][quiz_latex.STYLE]),
                                  ('build.log', build['retained']['build.log']), ('main.pdf', build['pdf'])):
                path = f'{base}/{letter}/{name}'
                payloads[path] = content
                files[name] = {'path': path, 'sha256': digest(content), 'size': len(content)}
            shown = []
            for page in pages[letter]:
                path = f"{base}/{letter}/page-{page['number']}.png"
                payloads[path] = page['png']
                shown.append({'number': page['number'], 'width_px': page['width_px'], 'height_px': page['height_px'],
                              'image': {'path': path, 'sha256': digest(page['png']), 'size': len(page['png'])}})
            versions[letter] = {'files': files, 'pages': shown, 'page_count': build['pages'],
                                'class_sha256': digest(build['retained']['facultyassignment.cls']),
                                'layout': {'problems': build['layout']['problems'], 'notes': build['layout']['notes']},
                                'areas': {str(n): a for n, a in quiz_latex.areas(build['layout']['problems']).items()}}
        with self.rosters.lock(), self.exams.documents.files.files('definition', draft['definition_id'], write=True) as root:
            for path, content in payloads.items():
                publish(root / path, content)
        return {'revision_id': task['id'], 'versions': versions}

    # Approval ------------------------------------------------------------------------------------------------------------------
    def quiz_approval_options(self, draft_id):
        """What approving asks for: the assignment's key and title, each section of the draft's term with its students, and the copies of
        each version it would print by default for all of them. The quiz is printed as ONE stack for the term (requirement, 2026-10-01): the versions share the students of every
        section, one more copy of the first versions when uneven; the page offers the same split for the sections ticked."""
        with self.rosters.lock(), connect(self.path) as db:
            draft = self._quiz_draft(db, draft_id)
            self._quiz_not_scrapped(db, draft_id)
            context = json.loads(draft['input_json'])
            term = db.execute('SELECT * FROM course_terms WHERE id=?', (context['term_id'],)).fetchone()
            sections = [dict(r) for r in db.execute('''SELECT s.id,s.number,(SELECT COUNT(*) FROM roster_current m WHERE m.section_id=s.id) students
                FROM course_sections s WHERE s.term_id=? ORDER BY s.number''', (context['term_id'],))]
            key = re.sub(r'[^a-z0-9]+', '-', context['title'].lower()).strip('-')[:60] or 'quiz'
            existing = db.execute('SELECT id,title FROM assignments WHERE definition_id=? AND assignment_key=?', (draft['definition_id'], key)).fetchone()
            if existing and existing['title'] != context['title']:
                key = f'{key}-{digest(draft_id)[:6]}'
        names = context['versions']
        share, rest = divmod(sum(section['students'] for section in sections), len(names))
        return {'term': {'id': term['id'], 'label': term_label(term), 'is_current': bool(term['is_current'])}, 'assignment': {'key': key, 'title': context['title']},
                'versions': names, 'sections': sections, 'copies': {letter: share + (index < rest) for index, letter in enumerate(names)}}

    def approve_quiz_draft(self, draft_id, data):
        """The instructor's approval of one finished revision for the draft's term (`confirmation: "approve"`, checked here): what to call
        the assignment, the sections it is for, and the copies of each version printed for them as one stack (one print job per version
        owned by the term, naming no section: document_identity migration 013). The worker carries it out (`run_quiz_approval`)."""
        with self.rosters.lock(), connect(self.path) as db:
            if json.loads(self._quiz_draft(db, draft_id)['input_json']).get('makeup'):
                return self.approve_makeup_draft(draft_id, data)  # GRADING "MAKEUP VERSIONS"
        request_id = identifier(data.get('request_id'))
        if data.get('confirmation') != 'approve':
            raise ValueError('Confirm the approval: it creates the assignment, its grading setup and the print jobs.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            draft = self._quiz_draft(db, draft_id)
            self._quiz_not_scrapped(db, draft_id)
            revision_id = identifier(data.get('revision_id'))
            context = json.loads(draft['input_json'])
            prior = db.execute('SELECT * FROM quiz_draft_approvals WHERE request_id=?', (request_id,)).fetchone()
            revision = db.execute("SELECT * FROM quiz_draft_revisions WHERE id=? AND draft_id=? AND status='complete' AND cancelled=0",
                                  (revision_id, draft_id)).fetchone()
            if not revision:
                raise ValueError('Approve a finished revision of this quiz.')
            latest = db.execute("SELECT id FROM quiz_draft_revisions WHERE draft_id=? AND status='complete' AND cancelled=0 ORDER BY number DESC LIMIT 1", (draft_id,)).fetchone()
            if latest['id'] != revision['id']:
                raise Conflict('A newer revision of this quiz exists. Open it before approving.')
            result = json.loads(revision['result_json'])
            quiz_blueprints.check(result['quiz'], context.get('blueprint'))
            missing = [r['problem'] for r in result['rubrics'] if not r.get('rubric')]
            if missing:
                raise Conflict(f"Problem{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} {'have' if len(missing) > 1 else 'has'} no rubric yet. "
                               'Ask for changes so the model drafts it again.')
            key = text(data.get('key'), 'Assignment key', 160, False).strip()
            title = ' '.join(text(data.get('title'), 'Assignment title', 240, False).split())
            asked = data.get('sections')
            if not isinstance(asked, list) or not asked or len(asked) > 50:
                raise ValueError('Choose the sections this quiz is for.')
            names, sections = context['versions'], []
            for section_id in asked:
                section = db.execute('SELECT id,number FROM course_sections WHERE id=? AND term_id=?', (section_id, context['term_id'])).fetchone() \
                    if isinstance(section_id, str) else None
                if not section or any(s['section_id'] == section['id'] for s in sections):
                    raise ValueError(f"Choose sections of {context['term']}, each once.")
                sections.append({'section_id': section['id'], 'number': section['number']})
            sections.sort(key=lambda s: (s['number'], s['section_id']))
            copies = data.get('copies')
            if not isinstance(copies, dict) or set(copies) != set(names) or any(type(v) is not int or not 0 <= v <= 500 for v in copies.values()):
                raise ValueError('Give a number of copies from 0 to 500 for each version.')
            copies = {n: copies[n] for n in names}
            total = sum(copies.values())
            if not total:
                raise ValueError('Print at least one copy.')
            # 'Given on' (requirement, 2026-10-02), optional: the campus date the quiz is written, a wall-clock date kept as typed. The
            # worker gives the exam record its main sitting on that date (`_quiz_given_on`), which puts the quiz on Home's Coming up,
            # the Calendar and its Exams page. Absent, nothing is dated and the approval reads as it did before.
            given = data.get('given_on')
            if given not in (None, ''):
                try:
                    if not isinstance(given, str) or date.fromisoformat(given).isoformat() != given:
                        raise ValueError
                except ValueError:
                    raise ValueError('Give the date the quiz is given as YYYY-MM-DD, or leave it empty.') from None
            # The planned row of the term's class schedule this quiz stands for (SCHEDULING "CLASS SCHEDULE", pre-fill), optional: the
            # worker links it once the assignment exists (`_follow_planned`), so the row follows the quiz's own date. Absent, the
            # approval reads as it did before. Checked only for a new approval: a repeated request answers as it first did.
            planned = data.get('planned_item')
            if planned not in (None, '') and not isinstance(planned, str):
                raise ValueError('Name the planned row of the class schedule this quiz stands for, or leave it out.')
            # The answer keys the draft showed (requirement, 2026-10-11): the page that shows each problem's
            # key sends `save_keys`, and only then does carrying the approval out save them with the questions; that approval is the
            # instructor's save of the keys they saw. A tab opened before that page existed sends nothing and no key is saved, as
            # before (GRADING: a key stays unused until the instructor saves it). Absent, the approval reads and checksums as it did.
            save_keys = data.get('save_keys') is True
            # QUIZZES "SHARED STAMPS" (requirement, 2026-11-04): each student must use their own copy, so a stamp that comes back on two
            # papers is reported on the assignment. Absent, the approval reads and checksums as it did.
            own_copies = data.get('own_copies') is True
            # THE RUBRIC FINALIZATION WITH THE APPROVAL (requirement, 2026-10-11): the approval
            # window's one unticked box, offered only where the draft's rubric check is complete with no decision open and no
            # question's lines adding up to more than it is worth. Ticked, the request says `finalization: {confirm: true,
            # check_sha256}` and the worker records the ORDINARY confirmation of that check's guidance once it has recorded the
            # check for the plan (`_finalize_with_approval`). Accepted only for the check this revision holds and shows, with
            # nothing open: nothing weaker than this explicit tick confirms a finalization. Absent, the approval reads and
            # checksums as it did, and Key & Rubric Finalization waits for the instructor as before.
            finalization = data.get('finalization')
            if finalization is not None:
                if not isinstance(finalization, dict) or finalization.get('confirm') is not True or not isinstance(finalization.get('check_sha256'), str):
                    raise ValueError('Tick the rubric finalization for the rubric check this page shows, or leave it out.')
                held = result.get('rubric_check') or {}
                if held.get('status') != 'complete' or held.get('sha256') != finalization['check_sha256']:
                    raise Conflict('The rubric check shown is not the one this revision holds. Open the approval again.')
                left = check_counts(held)
                if left['decisions'] or left['overflow']:
                    raise Conflict(f"The rubric check still has {left['decisions'] + left['overflow']} decision{'s' if left['decisions'] + left['overflow'] != 1 else ''} "
                                   'open. Answer them on the draft, or approve without the rubric finalization and finish it in Key & Rubric Finalization.')
            approval = {'rule': RULE, 'revision_id': revision['id'], 'term_id': context['term_id'], 'key': key, 'title': title, 'sections': sections,
                        'copies': copies, **({'given_on': given} if given else {}), **({'planned_item': planned} if planned else {}),
                        **({'save_keys': True} if save_keys else {}), **({'own_copies': True} if own_copies else {}),
                        **({'finalization': {'confirm': True, 'check_sha256': finalization['check_sha256']}} if finalization else {})}
            checksum = digest([draft_id, approval])
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another approval.')
                return self._quiz_draft_summary(db, draft, details=True)
            if planned:
                from scheduling.class_schedule import plan as class_plan
                if not any(row['item'] == planned and not row.get('link') for row in class_plan(db, context['term_id'])['items']):
                    raise ValueError('That planned row is no longer open on this term\'s class schedule. Open the approval again.')
            if db.execute("SELECT 1 FROM quiz_draft_approvals WHERE draft_id=? AND term_id=? AND status!='failed'", (draft_id, context['term_id'])).fetchone():
                raise Conflict('This quiz is already approved.')
            if db.execute("SELECT 1 FROM quiz_draft_revisions WHERE draft_id=? AND status IN ('pending','running')", (draft_id,)).fetchone():
                raise Conflict('The model is still working on a newer revision. Wait for it or stop it before approving.')
            existing = db.execute('SELECT id,title FROM assignments WHERE definition_id=? AND assignment_key=?', (draft['definition_id'], key)).fetchone()
            if existing:
                if existing['title'] != title:
                    raise Conflict(f"The assignment key {key} already belongs to {existing['title']}. Choose another key.")
                taken = [r[0] for r in db.execute('SELECT label FROM assignment_versions WHERE assignment_id=?', (existing['id'],)) if r[0] in names]
                if taken and not db.execute("SELECT 1 FROM quiz_draft_approvals WHERE draft_id=? AND status='failed'", (draft_id,)).fetchone():
                    raise Conflict(f"{title} already has printed versions {', '.join(taken)}. Choose another key and title.")
            now = timestamp()
            aid = uid()
            db.execute("""INSERT INTO quiz_draft_approvals(id,draft_id,revision_id,term_id,request_id,request_sha256,input_json,status,actor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'pending','workspace',?,?)""", (aid, draft_id, revision['id'], context['term_id'], request_id, checksum, encoded(approval), now, now))
            self.exams.documents.event(db, 'quiz_draft_approved', 'workspace', {'draft_id': draft_id, 'approval_id': aid, 'revision_id': revision['id'],
                                                                               'key': key, 'title': title, 'copies': total})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    def quiz_section_stack(self, draft_id, approval_id, section_id=None):
        """The print file with the versions interleaved, from the jobs an approval made (`DocumentService.section_stack`): the whole
        stack of jobs printed for the term, or (`section_id`, an approval from before 2026-10-01) one section's."""
        with self.rosters.lock(), connect(self.path) as db:
            self._quiz_draft(db, draft_id)
            row = db.execute("SELECT * FROM quiz_draft_approvals WHERE id=? AND draft_id=? AND status='complete'", (approval_id, draft_id)).fetchone()
            if not row:
                raise NotFound('This approval has no print jobs yet.')
            result = self._quiz_current(db, row)  # a rebuilt quiz prints its rebuild's jobs
        batches = [b for b in result['batches'] if b.get('section_id') == section_id]
        if not batches:
            raise NotFound('This approval printed nothing for that section.')
        content = self.exams.documents.section_stack([b['batch_id'] for b in batches])
        title = re.sub(r'[^A-Za-z0-9]+', '-', json.loads(row['input_json'])['title']).strip('-') or 'quiz'
        return content, f"{title}-section-{batches[0]['section_number']}-all-versions.pdf" if section_id else f"{title}-all-versions.pdf"

    # Accessible copies for the DR center (QUIZZES "ACCESSIBLE COPY", requirement, 2026-10-27) -----------------------------------------
    def quiz_accessible_copies(self, term_id):
        """{assignment_id: {draft_id, approval_id, title, version, ready, reasons}} of the term's approved quizzes (a makeup's draft
        is left out): the first version a screen reader can read (`accessible_quiz.readiness`), else the first version and why it
        cannot. The DR quizzes tab joins it to its rows by assignment. Writes nothing."""
        with self.rosters.lock(), connect(self.path) as db:
            rows = db.execute("""SELECT a.id, a.draft_id, a.revision_id, a.input_json, a.result_json, r.result_json AS revision
                FROM quiz_draft_approvals a JOIN quiz_draft_revisions r ON r.id=a.revision_id
                WHERE a.term_id=? AND a.status='complete' ORDER BY a.created_at, a.rowid""", (term_id,)).fetchall()
        found = {}
        for row in rows:
            request_ = json.loads(row['input_json'])
            if request_.get('rule') != 'quiz-draft-v1':
                continue
            quiz = json.loads(row['revision'])['quiz']
            entry = None
            for letter in quiz['versions']:
                reasons = accessible_quiz.readiness(row['revision_id'], quiz, letter, quiz['title_line'])
                entry = entry or {'version': letter, 'reasons': reasons}
                if not reasons:
                    entry = {'version': letter, 'reasons': []}
                    break
            found[json.loads(row['result_json'])['assignment_id']] = {
                'draft_id': row['draft_id'], 'approval_id': row['id'], 'title': request_.get('title') or quiz['title_line'],
                'version': entry['version'], 'ready': not entry['reasons'], 'reasons': entry['reasons']}
        return found

    def quiz_accessible_copy(self, draft_id, approval_id, letter):
        """(.docx, file name) of one version of an approved quiz, made again from its approved revision (`accessible_quiz.build`);
        Conflict saying why when a screen reader could not read it. The name says no version (QUIZZES "NO VERSION ON THE PAGE")."""
        with self.rosters.lock(), connect(self.path) as db:
            self._quiz_draft(db, draft_id)
            row = db.execute("""SELECT a.input_json, r.result_json, a.term_id, a.result_json AS approved FROM quiz_draft_approvals a
                JOIN quiz_draft_revisions r ON r.id=a.revision_id WHERE a.id=? AND a.draft_id=? AND a.status='complete'""", (approval_id, draft_id)).fetchone()
        if not row:
            raise NotFound('This quiz is not approved yet.')
        quiz = json.loads(row['result_json'])['quiz']
        if letter not in quiz['versions']:
            raise NotFound('That version is not part of this quiz.')
        try:
            content, _ = accessible_quiz.build(quiz, letter, quiz['title_line'])
        except accessible_quiz.Unavailable as error:
            raise Conflict('No accessible copy: ' + '; '.join(error.reasons) + '.') from error
        # GRADING "DR COPIES" > STAMPS (requirement, 2026-11-10): the file handed out prints its page codes, the same for every
        # student. A file whose codes cannot be made or checked goes out as it always did, and the reason is logged: the DR
        # center's copy is never held up for them, and its papers are then found as papers without labels.
        try:
            content = self.dr_stamped_copy(json.loads(row['approved']).get('assignment_id'), row['term_id'], letter, approval_id) or content
        except (ValueError, Conflict, OSError, StorageError) as error:
            LOG.warning('The accessible copy of approval %s goes out without page codes: %s', approval_id, getattr(error, 'description', error))
        title = re.sub(r'[^A-Za-z0-9]+', '-', json.loads(row['input_json']).get('title') or 'quiz').strip('-') or 'quiz'
        return content, f"{title}-accessible.docx"

    # Rebuilding an approved quiz (QUIZZES "REBUILDING AN APPROVED QUIZ") -------------------------------------------------------
    def _quiz_current(self, db, approval):
        """What an approved quiz is printed and graded as now: its approval's result, or the latest complete rebuild's on top of it."""
        result = json.loads(approval['result_json'])
        latest = db.execute("SELECT result_json FROM quiz_draft_rebuilds WHERE approval_id=? AND status='complete' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                            (approval['id'],)).fetchone()
        return {**result, **json.loads(latest['result_json'])} if latest else result

    @staticmethod
    def _quiz_rebuild_refusal(db, current):
        """Why the quiz printed and graded as `current` cannot be rebuilt now, or ''. A rebuild replaces what the approval made, so nothing may
        have come back, been graded, sent, certified or changed by the instructor since."""
        aid, batch_ids = current['plan_id'], [b['batch_id'] for b in current['batches']]
        if batch_ids and db.execute(f"""SELECT 1 FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                WHERE c.print_batch_id IN ({','.join('?' * len(batch_ids))}) LIMIT 1""", batch_ids).fetchone():
            return 'Copies of this quiz already came back in a scan, so it is not rebuilt.'
        if db.execute('SELECT 1 FROM frq_responses WHERE assignment_id=? LIMIT 1', (aid,)).fetchone():
            return 'This quiz already has papers to grade, so it is not rebuilt.'
        for table, what in (('frq_certifications', 'certified'), ('frq_gradescope_creations', 'made on Gradescope'), ('frq_gradescope_targets', 'linked to Gradescope')):
            if db.execute(f'SELECT 1 FROM {table} WHERE assignment_id=? LIMIT 1', (aid,)).fetchone():
                return f'This quiz is already {what}, so it is not rebuilt.'
        # A key the worker saved by itself (GRADING "ANSWER KEYS SAVE THEMSELVES", actor `automatic`) is an automatic follow-up too.
        own = {r[0] for r in db.execute("SELECT DISTINCT kind FROM frq_events WHERE assignment_id=? AND NOT (kind='answer_key_saved' AND actor='automatic')",
                                        (aid,))} - REBUILD_AUTOMATIC
        # A finalization recorded WITH the approval (`_finalize_with_approval`) wrote `rubric_quality_confirmed` and one
        # `rubric_approved` per rubric: they are the approval's own, told by the confirmation they name (`finalization.review_id` of
        # the approval's result), or a quiz finalized on approval could never be rebuilt. The same kinds written for any other
        # confirmation are the instructor's work in Key & Rubric Finalization and still refuse the rebuild.
        with_approval = (current.get('finalization') or {}).get('review_id')
        if with_approval and not db.execute(f"""SELECT 1 FROM frq_events WHERE assignment_id=? AND kind IN ({','.join('?' * len(APPROVAL_CONFIRMATION))})
                AND COALESCE(json_extract(detail_json,'$.review_id'),'')!=? LIMIT 1""", (aid, *APPROVAL_CONFIRMATION, with_approval)).fetchone():
            own -= set(APPROVAL_CONFIRMATION)
        own = sorted(own)
        if own:
            return (f"This quiz's grading setup has changes of your own since it was approved ({', '.join(own[:4])}); a rebuild would leave them on "
                    'the questions it takes off the plan, so it is not rebuilt.')
        if db.execute("""SELECT 1 FROM frq_rubric_generations WHERE assignment_id=? AND status IN ('pending','running')
                UNION ALL SELECT 1 FROM frq_rubric_quality_checks WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1""", (aid, aid)).fetchone():
            return 'A rubric review or answer-key reading of this quiz is still running. Wait for it to finish, then rebuild.'
        return ''

    def rebuild_quiz_approval(self, draft_id, approval_id, data, actor='workspace'):
        """The instructor's request to print an approved quiz again in Faculty's current page layout (`confirmation: "rebuild"`, checked
        here): every version compiled again from the approved quiz, the grading setup moved onto the new printed versions, new print jobs
        in place of the superseded ones. The worker carries it out (`run_quiz_rebuild`)."""
        request_id = identifier(data.get('request_id'))
        if data.get('confirmation') != 'rebuild':
            raise ValueError('Confirm the rebuild: it replaces the print jobs and the grading setup of this quiz.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            draft = self._quiz_draft(db, draft_id)
            approval = db.execute("SELECT * FROM quiz_draft_approvals WHERE id=? AND draft_id=? AND status='complete'", (approval_id, draft_id)).fetchone()
            if not approval:
                raise NotFound('Only a quiz whose approval was carried out can be rebuilt.')
            if json.loads(draft['input_json']).get('makeup'):
                raise ValueError('A makeup version is not rebuilt: draft a new makeup instead.')
            rebuild = {'rule': REBUILD_RULE, 'approval_id': approval_id}
            checksum = digest([draft_id, rebuild])
            prior = db.execute('SELECT * FROM quiz_draft_rebuilds WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another rebuild.')
                return self._quiz_draft_summary(db, draft, details=True)
            if db.execute("SELECT 1 FROM quiz_draft_rebuilds WHERE approval_id=? AND status IN ('pending','running')", (approval_id,)).fetchone():
                raise Conflict('This quiz is already being rebuilt.')
            refusal = self._quiz_rebuild_refusal(db, self._quiz_current(db, approval))
            if refusal:
                raise Conflict(refusal)
            now, rid = timestamp(), uid()
            db.execute("""INSERT INTO quiz_draft_rebuilds(id,draft_id,approval_id,request_id,request_sha256,input_json,status,actor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'pending',?,?,?)""", (rid, draft_id, approval_id, request_id, checksum, encoded(rebuild), actor, now, now))
            self.exams.documents.event(db, 'quiz_draft_rebuild_requested', actor, {'draft_id': draft_id, 'approval_id': approval_id, 'rebuild_id': rid})
        self.inbox.wake.set()
        return self.quiz_draft(draft_id)

    def _rebuild_progress(self, rebuild_id, step, **detail):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute('SELECT progress_json,status FROM quiz_draft_rebuilds WHERE id=?', (rebuild_id,)).fetchone()
            if row['status'] != 'running':
                return
            progress = json.loads(row['progress_json'] or '{}')
            progress[step] = {**progress.get(step, {}), **detail, 'at': timestamp()}
            db.execute('UPDATE quiz_draft_rebuilds SET progress_json=?,updated_at=? WHERE id=?', (encoded(progress), timestamp(), rebuild_id))

    def run_quiz_rebuild(self, task):
        status, result, error = 'complete', None, ''
        try:
            result = self._carry_out_rebuild(task)
        except (ValueError, Conflict, NotFound, CompileError, KeyError) as failure:
            status, error = 'failed', getattr(failure, 'description', None) or str(failure)
        with self.rosters.lock(), connect(self.path) as db:
            published = db.execute("UPDATE quiz_draft_rebuilds SET status=?,result_json=?,error=?,updated_at=? WHERE id=? AND status='running'",
                                   (status, encoded(result) if result else None, error, timestamp(), task['id']))
            if published.rowcount:
                self.exams.documents.event(db, 'quiz_draft_rebuild_' + ('complete' if status == 'complete' else 'failed'), 'worker',
                                           {'draft_id': task['draft_id'], 'approval_id': task['approval_id'], 'rebuild_id': task['id'],
                                            **({'error': error} if error else {})})

    def _carry_out_rebuild(self, task):
        """Every step reuses what an earlier attempt made, so a rebuild stopped part way is requested again and carries on."""
        documents = self.exams.documents
        with self.rosters.lock(), connect(self.path) as db:
            draft = self._quiz_draft(db, task['draft_id'])
            approval = db.execute("SELECT * FROM quiz_draft_approvals WHERE id=? AND status='complete'", (task['approval_id'],)).fetchone()
            revision = db.execute('SELECT * FROM quiz_draft_revisions WHERE id=?', (approval['revision_id'],)).fetchone()
            progress = json.loads(db.execute('SELECT progress_json FROM quiz_draft_rebuilds WHERE id=?', (task['id'],)).fetchone()[0] or '{}')
            current = self._quiz_current(db, approval)
            refusal = '' if 'withdrawn' in progress else self._quiz_rebuild_refusal(db, current)
        if refusal:
            raise Conflict(refusal)
        context, drafted, ordered = json.loads(draft['input_json']), json.loads(revision['result_json']), json.loads(approval['input_json'])
        quiz, names = drafted['quiz'], context['versions']
        aid, event_id, assignment_id = current['plan_id'], current['event_id'], current['assignment_id']
        # 1. Every version compiled again from the approved quiz with Faculty's current page class and quiz style.
        built, faults = self._quiz_build(quiz)
        if faults:
            raise ValueError("The approved quiz does not fit Faculty's current page layout as it stands, so it cannot be rebuilt: " + ' '.join(faults[:3]))
        with connect(self.path) as db:
            printed = {row['id']: row['canonical_sha256'] for row in db.execute('SELECT id,canonical_sha256 FROM assignment_versions WHERE assignment_id=?', (assignment_id,))}
        if 'withdrawn' not in progress and all(printed.get(current['versions'][letter]) == digest(built[letter]['pdf']) for letter in names):
            raise Conflict('This quiz is already printed in the current page layout; there is nothing to rebuild.')
        # 2. A new printed version of the same assignment per quiz version, labelled with its letter and the printing ("A2"): labels are
        # unique, and the versions printed before stay as they were.
        versions = {}
        for letter in names:
            build = built[letter]
            details = {**build['details'], 'generator': GENERATOR, 'draft_id': draft['id'], 'revision_id': revision['id'], 'quiz_rule': quiz['rule'],
                       'rebuild_id': task['id']}
            sources = {'main.tex': build['retained']['main.tex'], quiz_latex.STYLE: build['retained'][quiz_latex.STYLE],
                       'facultyassignment.cls': build['retained']['facultyassignment.cls'], 'build.log': build['retained']['build.log']}
            for printing in range(2, 100):
                label = f'{letter}{printing}'
                with connect(self.path) as db:
                    found = db.execute('SELECT id,canonical_sha256 FROM assignment_versions WHERE assignment_id=? AND label=?', (assignment_id, label)).fetchone()
                if not found:
                    versions[letter] = documents.register_version(assignment_id, label, build['pdf'], sources, details)['id']
                    break
                if found['canonical_sha256'] == digest(build['pdf']) and found['id'] not in current['versions'].values():
                    versions[letter] = found['id']  # made by an earlier attempt
                    break
            else:
                raise ValueError(f'Version {letter} has been printed too many times to rebuild it again.')
        self._rebuild_progress(task['id'], 'versions', versions=versions)
        # 3. The questions on the versions printed before come off the plan; everything else about them stays.
        withdrawn = progress.get('withdrawn', {}).get('questions') or self._withdraw_quiz_questions(aid, task['id'], versions)
        self._rebuild_progress(task['id'], 'withdrawn', questions=withdrawn)
        # 4.-7. The new versions' questions, templates and answer areas where each problem is now printed, the rubric review, the print jobs.
        # The questions keep the answer keys the approval saved with the ones they replace (`save_keys`): the same revision's keys.
        areas = {letter: {str(n): a for n, a in quiz_latex.areas(built[letter]['layout']['problems']).items()} for letter in names}
        setup_ = {'rubrics': drafted['rubrics'], 'mode': drafted['mode'], 'versions': {letter: {'areas': areas[letter]} for letter in names}}
        # The rubric check made while the approved revision was drafted is carried to the new questions where they read as it read them
        # (the PDFs are new by definition, so they are not compared), NEVER its confirmation: a rebuilt quiz's rubrics wait for the
        # instructor's one tick in Key & Rubric Finalization, whatever the approval confirmed (2026-10-11).
        created, templates, confirmed, review, batches, carried = self._set_up_quiz_plan(
            lambda step, **detail: self._rebuild_progress(task['id'], step, **detail), draft, revision, quiz, setup_, names, versions, aid, event_id,
            question_prefix=f"quiz-rebuild:{task['id']}", review_request=f"quiz-rebuild-review:{task['id']}", batch_prefix=f"quiz:rebuild-{task['id']}",
            detail={'draft_id': draft['id'], 'revision_id': revision['id'], 'approval_id': approval['id'], 'rebuild_id': task['id']},
            copies=ordered['copies'], term_id=ordered['term_id'], section_ids=[s['section_id'] for s in ordered['sections']],
            save_keys=bool(ordered.get('save_keys')), checked=self._drafted_check(revision, drafted, printed=False))
        # 8. The print jobs made before are superseded (never printed back: the request checked); they stay as the record.
        superseded = []
        for batch in current['batches']:
            row = documents.get_batch(batch['batch_id'])
            if row['superseded_at'] or row['status'] == 'cancelled':
                continue
            documents.transition(batch['batch_id'], 'supersede' if row['status'] == 'ready' else 'cancel', actor='worker')
            superseded.append(batch['batch_id'])
        self._rebuild_progress(task['id'], 'superseded', batches=superseded)
        # 9. An answer-key reading the instructor asked for on the earlier questions is asked again for their new counterparts, except
        # those that now hold a key (the keys the approval saved came with them, step 4): a reading refuses a question with a key,
        # and one such question would refuse the whole request.
        with self.rosters.lock(), connect(self.path) as db:
            asked = {qid for (detail,) in db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='answer_key_reading_queued'", (aid,))
                     for qid in json.loads(detail).get('question_ids') or []}
            keyed = {qid for qid in created.values() if self.question(db, aid, qid)['content'].get('answer_key')}
        counterpart = {qid: created.get((int(key.split(':')[0]), key.split(':')[1])) for key, qid in current['questions'].items()}
        again = [new for earlier, new in counterpart.items() if earlier in asked and new and new not in keyed]
        keys = 'not asked'
        if again:
            try:
                self.queue_answer_key_reading(aid, {'request_id': f"quiz-rebuild-keys:{task['id']}", 'question_ids': again})
                keys = 'requested'
            except (ValueError, Conflict) as failure:
                keys = getattr(failure, 'description', None) or str(failure)  # the instructor asks for it on Key & Rubric Finalization
        self._rebuild_progress(task['id'], 'answer_keys', note=keys)
        self.inbox.wake.set()
        return {'assignment_id': assignment_id, 'event_id': event_id, 'plan_id': aid, 'versions': versions, 'replaced_versions': current['versions'],
                'templates': templates, 'questions': {f'{n}:{l}': q for (n, l), q in created.items()}, 'withdrawn': [q['question_id'] for q in withdrawn],
                'batches': batches, 'superseded': superseded, 'areas_confirmed': confirmed, 'rubric_review': 'requested' if not review else review,
                # Named on every rebuild (None when the plan's own check was queued), so it never reads as the approval's check.
                'rubric_check': carried.get('check_id'), 'answer_keys': keys}

    def _withdraw_quiz_questions(self, aid, rebuild_id, versions):
        """Take every question off the plan that is not on one of a rebuild's new printed versions: its pin goes (the plan's list of
        questions), while the question, its revisions, rubric, checks, comments and events all stay. Refuses a question with responses."""
        keep = set(versions.values())
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            pins = [dict(row) for row in db.execute("""SELECT p.question_id,p.question_revision_id,p.rubric_id,q.assignment_version_id,q.exam_version
                FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=? ORDER BY p.rowid""", (aid,))]
            gone = [pin for pin in pins if pin['assignment_version_id'] not in keep]
            if not gone:
                return []
            marks = ','.join('?' * len(gone))
            if db.execute(f'SELECT 1 FROM frq_responses WHERE assignment_id=? AND question_id IN ({marks}) LIMIT 1', (aid, *[p['question_id'] for p in gone])).fetchone():
                raise Conflict('A question of this quiz already has papers to grade, so it is not taken off the plan.')
            db.execute(f'DELETE FROM frq_assignment_questions WHERE assignment_id=? AND question_id IN ({marks})', (aid, *[p['question_id'] for p in gone]))
            # An answer-area check still waiting for one of them would otherwise wait for ever (the worker reads its question through the plan).
            db.execute(f"UPDATE frq_location_checks SET status='stale',updated_at=? WHERE assignment_id=? AND question_id IN ({marks}) AND status IN ('pending','running','failed')",
                       (timestamp(), aid, *[p['question_id'] for p in gone]))
            self.event(db, aid, 'quiz_rebuild_questions_withdrawn', {'rebuild_id': rebuild_id, 'questions': gone}, actor='worker')
            self.touch(db, aid)
        return gone

    def _approval_progress(self, approval_id, step, **detail):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute('SELECT progress_json,status FROM quiz_draft_approvals WHERE id=?', (approval_id,)).fetchone()
            if row['status'] != 'running':
                return
            progress = json.loads(row['progress_json'] or '{}')
            progress[step] = {**progress.get(step, {}), **detail, 'at': timestamp()}
            db.execute('UPDATE quiz_draft_approvals SET progress_json=?,updated_at=? WHERE id=?', (encoded(progress), timestamp(), approval_id))

    def run_quiz_approval(self, task):
        approval = json.loads(task['input_json'])
        status, result, error = 'complete', None, ''
        try:
            result = self._carry_out_approval(task, approval)
        except (ValueError, Conflict, NotFound, CompileError, KeyError) as failure:
            status, error = 'failed', getattr(failure, 'description', None) or str(failure)
        with self.rosters.lock(), connect(self.path) as db:
            published = db.execute("UPDATE quiz_draft_approvals SET status=?,result_json=?,error=?,updated_at=? WHERE id=? AND status='running'",
                                   (status, encoded(result) if result else None, error, timestamp(), task['id']))
            if published.rowcount:
                self.exams.documents.event(db, 'quiz_draft_approval_' + ('complete' if status == 'complete' else 'failed'), 'worker',
                                           {'draft_id': task['draft_id'], 'approval_id': task['id'], **({'error': error} if error else {}),
                                            **({'assignment_id': result['assignment_id']} if result else {})})

    def _carry_out_approval(self, task, approval):
        if approval.get('rule') == 'makeup-draft-v1':
            return self._carry_out_makeup(task, approval)  # GRADING "MAKEUP VERSIONS"
        from document_identity import compiler
        from document_identity.storage import read_verified
        documents = self.exams.documents
        with self.rosters.lock(), connect(self.path) as db:
            draft = self._quiz_draft(db, task['draft_id'])
            revision = db.execute('SELECT * FROM quiz_draft_revisions WHERE id=?', (approval['revision_id'],)).fetchone()
        context, drafted = json.loads(draft['input_json']), json.loads(revision['result_json'])
        quiz, names = drafted['quiz'], context['versions']
        # 1. The course-wide assignment and one printed version per quiz version, from the very PDFs the instructor approved.
        assignment = documents.create_assignment(draft['definition_id'], approval['key'], approval['title'])
        class_bytes = (compiler.TEMPLATES / 'facultyassignment.cls').read_bytes()
        versions = {}
        with self.rosters.lock(), documents.files.files('definition', draft['definition_id']) as root:
            stored = {letter: {name: read_verified(root, item['path'], item['sha256']) for name, item in drafted['versions'][letter]['files'].items()} for letter in names}
        for letter in names:
            files = stored[letter]
            if digest(class_bytes) != drafted['versions'][letter]['class_sha256']:
                raise ValueError('Faculty\'s page class changed since this revision was compiled. Ask for changes to compile the quiz again, then approve it.')
            with connect(self.path) as db:
                found = db.execute('SELECT * FROM assignment_versions WHERE assignment_id=? AND label=?', (assignment['id'], letter)).fetchone()
            if found:
                if found['canonical_sha256'] != digest(files['main.pdf']):
                    raise Conflict(f"{approval['title']} already has a different Version {letter}.")
                versions[letter] = found['id']
                continue
            details = {**drafted['toolchain'][letter], 'generator': GENERATOR, 'draft_id': draft['id'], 'revision_id': revision['id'], 'quiz_rule': quiz['rule']}
            sources = {'main.tex': files['main.tex'], quiz_latex.STYLE: files[quiz_latex.STYLE], 'facultyassignment.cls': class_bytes, 'build.log': files['build.log']}
            versions[letter] = documents.register_version(assignment['id'], letter, files['main.pdf'], sources, details)['id']
        self._approval_progress(task['id'], 'versions', assignment_id=assignment['id'], versions=versions)
        # 2. The term's exam record and grading plan.
        with self.rosters.lock(), connect(self.path) as db:
            event = db.execute('SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?', (approval['term_id'], assignment['id'])).fetchone()
        event_id = event['id'] if event else self.exams.create({'term_id': approval['term_id'], 'assignment_id': assignment['id'], 'title': approval['title'],
                                                                'section_ids': [s['section_id'] for s in approval['sections']]})['id']
        plan = self.create_assignment({'event_id': event_id})
        aid = plan['id']
        # 'Given on': the date the exam record holds afterwards, and whether an earlier date was kept instead of the typed one (the
        # draft's page says which, never the typed date alone).
        given = {}
        if approval.get('given_on'):
            saved = self._quiz_given_on(event_id, approval['title'], approval['given_on'])
            given = {'given_on': saved, **({'given_on_kept': True} if saved != approval['given_on'] else {})}
        if approval.get('planned_item'):
            given['planned_linked'] = self._follow_planned(approval, assignment['id'], task['id'])
        self._approval_progress(task['id'], 'plan', event_id=event_id, plan_id=aid, **given)
        # 3.-6. Questions, templates and answer areas, the rubric review, the print jobs (`_set_up_quiz_plan`). The rubric check made
        # while the revision was drafted is carried to the plan where its questions are the ones it read, on the very PDFs approved;
        # with the approval window's box ticked (`finalization`) its ordinary confirmation is recorded too.
        created, templates, confirmed, review, batches, carried = self._set_up_quiz_plan(
            lambda step, **detail: self._approval_progress(task['id'], step, **detail), draft, revision, quiz, drafted, names, versions, aid, event_id,
            question_prefix=f"quiz:{revision['id']}", review_request=f"quiz-review:{revision['id']}", batch_prefix=f"quiz:{revision['id']}",
            detail={'draft_id': draft['id'], 'revision_id': revision['id'], 'approval_id': task['id']},
            copies=approval['copies'], term_id=approval['term_id'], section_ids=[s['section_id'] for s in approval['sections']],
            save_keys=bool(approval.get('save_keys')), checked=self._drafted_check(revision, drafted, printed=True), finalize=approval.get('finalization'))
        self.inbox.wake.set()
        # `rubric_check` and `finalization` are named only where the draft's check was carried and confirmed, so every other approval
        # reads as before; `finalization.review_id` is the confirmation a rebuild knows as the approval's own.
        return {'assignment_id': assignment['id'], 'versions': versions, 'event_id': event_id, 'plan_id': aid, 'templates': templates,
                'questions': {f'{n}:{l}': q for (n, l), q in created.items()}, 'batches': batches, 'areas_confirmed': confirmed,
                'rubric_review': 'requested' if not review else review, **({'rubric_check': carried['check_id']} if carried.get('check_id') else {}),
                **({'finalization': {'review_id': carried['review_id'], 'check_id': carried['check_id']}} if carried.get('review_id') else {})}

    @staticmethod
    def _drafted_check(revision, drafted, *, printed):
        """What `_set_up_quiz_plan` needs of the rubric check a revision made while it was drafted, or None when it made none: the
        check, its receipts (kept with the revision's provider record), and whether each version's PDF must be the one checked."""
        check = drafted.get('rubric_check')
        return {'check': check, 'receipts': json.loads(revision['provider_json'] or '{}').get('rubric_check') or {}, 'printed': printed} if check else None

    def _follow_planned(self, approval, assignment_id, approval_id):
        """The class schedule's planned row this quiz stands for now follows the new assignment (SCHEDULING "CLASS SCHEDULE"): the
        schedule then shows the quiz's own date. Its request ID is the approval's, so a second attempt records nothing new. A link
        that cannot be recorded (the row was removed or linked meanwhile) never fails the approval: the row stays planned and the
        schedule page offers the link again. Returns whether the row follows the assignment."""
        try:
            from scheduling import class_schedule
            class_schedule.link(self, approval['term_id'], {'request_id': f'quiz-approval:{approval_id}', 'item': approval['planned_item'],
                                                           'assignment_id': assignment_id})
            return True
        except Exception:  # noqa: BLE001 - the quiz is approved either way; the row can be linked from the schedule page
            import logging
            logging.getLogger(__name__).exception('could not link planned row %s to the approved quiz', approval['planned_item'])
            return False

    def _quiz_given_on(self, event_id, title, day):
        """The approval's 'Given on' date (requirement, 2026-10-02) as the exam record's main sitting, saved by the Exams page's own
        sittings path (`ExamService.save_sitting`, what POST /api/exams/<event>/sittings calls): Home's Coming up, the Calendar (and
        its automatic Google Calendar sync of exams, CALENDAR), the Exams hub's phase and the bubble-sheet finder's calendar read it
        from there. A main sitting already dated (an earlier attempt, or the instructor's own on the Exams page or through Create New
        Assignment) is kept; an undated one gets the date. Returns the main date the record holds afterwards (the earliest dated
        main sitting, as the Exams hub reads it), which differs from `day` exactly when an earlier date was kept."""
        with self.rosters.lock():
            with connect(self.path) as db:
                version = db.execute('SELECT version FROM exam_events WHERE id=?', (event_id,)).fetchone()[0]
                mains = [dict(r) for r in db.execute("SELECT id,date,version FROM exam_sittings WHERE event_id=? AND kind='main' ORDER BY created_at,id",
                                                     (event_id,))]
            dated = sorted(m['date'] for m in mains if m['date'])
            if dated:
                return dated[0]
            if mains:
                self.exams.save_sitting(event_id, {'version': mains[0]['version'], 'date': day}, mains[0]['id'])
            else:
                self.exams.save_sitting(event_id, {'version': version, 'label': title, 'kind': 'main', 'date': day})
        return day

    def _attach_quiz_batch(self, event_id, batch_id, **placement):
        """Attach a retained print job once; a conflict is a replay only when the attachment exists."""
        event = self.exams.get(event_id)
        if any(batch['id'] == batch_id for batch in event.get('batches', [])):
            return
        try:
            self.exams.attach_batch(event_id, {'version': event['version'], 'batch_id': batch_id, **placement})
        except Conflict:
            # QUIZZES APPROVAL: an earlier attempt may have attached it, but another edit may also have won the version check.
            if not any(batch['id'] == batch_id for batch in self.exams.get(event_id).get('batches', [])):
                raise

    def _set_up_quiz_plan(self, progress, draft, revision, quiz, drafted, names, versions, aid, event_id, *, question_prefix, review_request,
                          batch_prefix, detail, copies, term_id, section_ids, save_keys=False, checked=None, finalize=None):
        """Steps 3-6 of carrying out an approval, and of rebuilding one (`_carry_out_rebuild`): every version's questions on its printed
        version (`versions`), their templates and answer areas (`drafted['versions'][letter]['areas']`) confirmed, the rubrics confirmed
        and the rubric review requested, then one print job per version for the term. Every step reuses what an earlier attempt made
        (the request IDs below). `save_keys` (requirement, 2026-10-11): the approval was sent from the page
        that showed each problem's answer key, so each question is created WITH the key read for it while the quiz was drafted
        (`_quiz_keys`), before the rubric review is requested, whose snapshot of each problem's reference then carries its key; a
        question an earlier attempt already created keeps what it was created with. Without it no key is saved, as before.
        `checked` ({check, receipts, printed}; requirement, 2026-10-11): the rubric check the
        revision made while it was drafted. Where every created question is proved to be what that check read, it is recorded as
        the plan's completed check in place of the request (`_carry_drafted_check`); `printed` also asks that each version's PDF be
        the one checked (an approval; a rebuild prints new PDFs and compares the rest). `finalize` (an approval's ticked box, its
        `check_sha256`; never a rebuild): the ordinary confirmation of that check, recorded only on the check carried here.
        Returns (created, templates, areas confirmed, review note, batches, {check_id, review_id} of what was carried and confirmed)."""
        # 3. Every version's questions with their rubrics, the drafted clarifications as line comments.
        entries = {r['problem']: r for r in drafted['rubrics']}
        created = {}
        mode = drafted['mode']
        for problem in quiz['problems']:
            rubric = entries[problem['number']]['rubric']
            for letter in names:
                content = problem['content'][letter]
                lines, rules = version_rules(rubric, letter, mode, problem['points'], problem.get('parts'))
                notes = drafted_comments(rubric, letter)
                answer_key =((entries[problem['number']].get('keys') or {}).get(letter) or {}).get('key') if save_keys else None
                member = next(v for v in rubric['versions'] if v['version'] == letter)
                exam_version = letter if len(names) > 1 else ''
                source = json.dumps({'mode': mode, 'items': lines, 'notes': f"Drafted by a model with quiz draft {draft['id']} revision {revision['number']}, "
                                     f"problem {problem['number']}, Version {letter}, and approved by the instructor with the quiz."}, indent=2, ensure_ascii=False)
                provenance = {'name': COMPILER, 'rule': RULE, 'draft_id': draft['id'], 'revision_id': revision['id'], 'generation_id': revision['id'],
                              'family': f"P{problem['number']}", 'problem': f"P{problem['number']}:{letter}", 'version': letter,
                              **({'line_kinds': kinds} if (kinds := line_kinds(rubric['items'])) else {})}
                question = self.create_draft_question(aid, filename=f"quiz-question-{problem['number']}{'-' + letter if exam_version else ''}.json",
                    content=source.encode(), source=source, rules=rules, max_points=problem['points'], printed=str(problem['number']), exam_version=exam_version,
                    key=f"q{problem['number']}" + (f'-{letter}' if exam_version else ''), document_id=versions[letter],
                    request_id=f"{question_prefix}:{problem['number']}:{letter}", title=f"Question {problem['number']}", compiler=provenance,
                    event='quiz_question_created', statement=statement_text(content), solution=content['solution'] or member.get('solution', ''),
                    detail=detail, answer_key=answer_key)
                self._save_drafted_clarifications(aid, question, notes)
                created[(problem['number'], letter)] = question['question_id']
        # How many of them hold an answer key now, said only for an approval that saves keys (as the catalog has it, so a question an
        # earlier attempt created without one is not counted).
        saved = {}
        if save_keys:
            with self.rosters.lock(), connect(self.path) as db:
                saved = {'keys': sum(1 for qid in created.values() if self.question(db, aid, qid)['content'].get('answer_key'))}
        progress('questions', count=len(created), **saved)
        # 4. Templates from the printed versions themselves, confirmed with the answer areas where each problem was printed: the
        # instructor's approval of the quiz is that confirmation (their choice, 2026-09-28). Under one hold of the lifecycle lock, so the
        # worker's own template reading never queues an OCR check for these questions in between.
        templates = {letter: self.templates.import_version(versions[letter])['id'] for letter in names}
        with self.rosters.lock():
            setup_ = self.assignment_templates(aid)
            slots = setup_['slots']
            selections = []
            for slot in slots:
                letter = next((l for l in names if versions[l] == slot['document_id']), None)
                if letter is None:
                    raise ValueError('A template slot of this grading plan is not one of the quiz versions.')
                selections.append({'key': slot['key'], 'template_id': templates[letter]})
            if not all(slot.get('confirmed') for slot in slots):
                fingerprint = setup_['input_sha256']
                self.confirm_assignment_templates(aid, {'acknowledge_warning': True, 'input_sha256': fingerprint, 'selections': selections})
            confirmed = self._confirm_quiz_areas(aid, quiz, drafted, created, templates, revision['id'])
        progress('areas', templates=templates, confirmed=confirmed)
        # 5. Every rubric confirmed for the rubric review, then the review requested: the finalization itself stays the instructor's.
        # RUBRIC CHOICES (2026-10-24): the quick answers given with the draft become the plan's, so its own check reads them too.
        choices = json.loads(draft['input_json']).get('rubric_choices')
        # GRADED FOR PARTICIPATION (2026-11-01): the box ticked with the draft becomes the plan's setting.
        taking_part = json.loads(draft['input_json']).get('participation') is True
        with self.rosters.lock(), connect(self.path) as db:
            if choices or taking_part:
                db.execute('BEGIN IMMEDIATE')
                if choices and rubric_choices.record(db, aid, choices, 'quiz-approval'):
                    self.event(db, aid, 'rubric_choices_saved', {'choices': rubric_choices.clean(choices), 'quiz_draft_id': draft['id']}, 'workspace')
                if taking_part and not participation.has_bubble_key(db, aid) and participation.record(db, aid, True, 'quiz-approval'):
                    self.event(db, aid, 'participation_set', {'enabled': True, 'quiz_draft_id': draft['id']}, 'workspace')
                db.commit()
            status_ = self.rubric_quality_status(db, aid)
        readiness = {qid: {'rubric_id': r['rubric_id'], 'comments_sha256': r['comments_sha256']} for qid, r in status_['readiness'].items() if not r['ready']}
        if readiness:
            self.confirm_rubric_review(aid, {'questions': readiness})
        # The check the revision made while it was drafted stands for the plan where its questions are proved to be the ones it read;
        # anything less queues the plan's own check, exactly as before (requirement, 2026-10-11).
        review, carried = '', {}
        check_id = self._carry_drafted_check(aid, checked, created, versions, review_request, {'quiz_draft_id': draft['id'], 'quiz_revision_id': revision['id']}) if checked else None
        if check_id:
            carried = {'check_id': check_id}
            # One explicit tick in the approval window is the instructor's confirmation; the ordinary confirmation records it.
            if finalize:
                confirmed_as, why = self._finalize_with_approval(aid, check_id, checked['check'], finalize)
                carried.update({'review_id': confirmed_as} if confirmed_as else {'not_confirmed': why})
        else:
            try:
                with self.rosters.lock(), connect(self.path) as db:
                    fingerprint = self.rubric_quality_status(db, aid)['input_sha256']
                self.queue_rubric_quality(aid, {'request_id': review_request, 'input_sha256': fingerprint})
            except (ValueError, Conflict) as failure:
                review = getattr(failure, 'description', None) or str(failure)  # the instructor requests it in Key & Rubric Finalization
            if finalize:
                carried = {'not_confirmed': 'The rubric check made while the quiz was drafted could not be carried to its grading plan, so the plan checks its rubrics again.'}
        progress('review', requested=not review, note=review, **({'carried': carried} if carried else {}))
        # 6. The print jobs: one per version for the term, naming no section (one stack for every section, any copy for any student:
        # requirement, 2026-10-01), unnamed and with no caption: nothing printed says which version a paper is (requirement, 2026-10-01), attached to the term's exam record.
        batches = []
        for letter in names:
            count = copies[letter]
            if not count:
                continue
            batch = self.exams.documents.create_batch(versions[letter], None, count, f"{batch_prefix}:{letter}", term_id=term_id, section_ids=section_ids)
            batches.append({'batch_id': batch['id'], 'version': letter, 'copies': count})
        for batch in batches:
            self._attach_quiz_batch(event_id, batch['batch_id'])
        progress('print', batches=len(batches))
        return created, templates, confirmed, review, batches, carried

    def _carry_drafted_check(self, aid, checked, created, versions, request_id, detail):
        """The rubric check a revision made while it was drafted, recorded as the plan's completed check
        (`rubric_quality.record_drafted_check`), or None: the caller then queues the plan's own check as it always did. Carried only
        where identity is PROVED: the check completed under this rule; the plan's check would ask every question and they are
        exactly the questions `created` (none settled, none besides); and for each of them the `content` digest (version, title,
        printed number, points, rules, built-in exclusions, comments: what a confirmation covers of it, less its IDs) and the
        `reference` digest (problem, solution, answer key) of the plan's own check input equal what the check recorded
        (`check_digests`). So an approval that saved no keys, or met a question an earlier attempt created otherwise, queues as
        before. With `checked['printed']` (an approval) each version's registered PDF and the confirmed answer areas must also be
        the ones checked; a rebuild prints new PDFs by definition and compares content and reference alone. The result goes in
        with the plan's own question and rubric IDs (`renamed`), the receipts under the same questions."""
        check = checked['check']
        if check.get('rule') != DRAFT_CHECK or check.get('status') != 'complete':
            return None
        with self.rosters.lock(), connect(self.path) as db:
            status = self.rubric_quality_status(db, aid, details=True)
            payload, settled = self.rubric_check_input(db, aid, status)
            printed = {row['id']: row['canonical_sha256'] for row in db.execute(
                f"SELECT id,canonical_sha256 FROM assignment_versions WHERE id IN ({','.join('?' * len(versions))})", list(versions.values()))}
        by_id = {q['question_id']: q for q in payload['questions']}
        names = {virtual(number_, letter): qid for (number_, letter), qid in created.items()}
        if settled or set(names.values()) != set(by_id) or set(names) != set(check['questions']):
            return None
        for name, qid in names.items():
            read = check['questions'][name]
            now = check_digests(by_id[qid], printed.get(versions[read['version']]))
            if now['content'] != read['content'] or now['reference'] != read['reference'] or (checked['printed'] and now['printed'] != read['printed']):
                return None
        names.update({'rubric:' + name: by_id[qid]['rubric_id'] for name, qid in list(names.items())})
        receipts = checked.get('receipts') or {}
        metadata = {**receipts, **{part: {names.get(name, name): value for name, value in (receipts.get(part) or {}).items()}
                                   for part in ('questions', 'problem_images', 'problem_summaries') if part in receipts}}
        return self.record_drafted_check(aid, request_id, status['input_sha256'], check['profile'], renamed(check['result'], names), metadata, detail)

    def _finalize_with_approval(self, aid, check_id, check, finalize):
        """The rubric finalization an approval's tick asked for (`approve_quiz_draft`, `finalization`), recorded by the ORDINARY
        confirmation (`confirm_rubric_quality`: the points rule, the standing of each question, the rubric approvals and what a
        batch may take are all its own and unchanged) of the check just carried, with exactly the guidance that check holds: its
        clarifications and exclusions as the draft showed them, nothing edited. Only for the check the tick named
        (`check_sha256`) and only with no decision open; an earlier attempt's confirmation of this check is the one. Returns
        (the confirmation's ID, '') or (None, why nothing was confirmed): the approval is carried out either way, and Key & Rubric
        Finalization then waits for the instructor as it always did."""
        left = check_counts(check)
        if check.get('sha256') != finalize.get('check_sha256') or left['decisions'] or left['overflow']:
            return None, 'The rubric check is not the one you confirmed with nothing left open.'
        with self.rosters.lock(), connect(self.path) as db:
            status = self.rubric_quality_status(db, aid, details=True)
            made = db.execute('SELECT id FROM frq_rubric_quality_reviews WHERE assignment_id=? AND check_id=? ORDER BY rowid DESC LIMIT 1', (aid, check_id)).fetchone()
        if made:
            return made['id'], ''
        current = status['check']
        if not current or current['id'] != check_id or current['status'] != 'complete' or not current['current']:
            return None, 'The rubrics changed after the check was carried to the grading plan.'
        try:
            done = self.confirm_rubric_quality(aid, {'confirm_review': True, 'version': status['version'], 'check_id': check_id, 'input_sha256': status['input_sha256'],
                'questions': [{k: q[k] for k in ('question_id', 'rubric_id', 'pairs', 'clarifications')} for q in current['result']['questions']]})
        except (ValueError, Conflict) as failure:
            return None, getattr(failure, 'description', None) or str(failure)
        return done['review']['id'], ''

    def _confirm_quiz_areas(self, aid, quiz, drafted, created, templates, revision_id):
        """Each question's answer area where its problem was printed (`quiz_latex.areas`), recorded as a completed check of the layout
        and confirmed, exactly as a confirmed OCR proposal would be. Caller holds the lifecycle lock. Returns how many were confirmed."""
        count = 0
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            for (problem_number, letter), qid in created.items():
                q = self.question(db, aid, qid)
                status = self.location_status(db, aid, q)
                if status['status'] == 'confirmed':
                    continue
                regions = drafted['versions'][letter]['areas'][str(problem_number)]
                problem = next(p for p in quiz['problems'] if p['number'] == problem_number)
                context = status['context']
                if not context['template'] or context['template']['id'] != templates[letter]:
                    raise ValueError(f'Question {problem_number} of Version {letter} is not on its own template; confirm its answer area in Answer areas.')
                if not status['id']:
                    check = {'regions': regions, 'evidence': [{'page': regions[0]['page'], 'source': 'latex-layout', 'revision_id': revision_id}],
                             'printed_points': problem['points'], 'rubric_maximum': context['rubric_maximum'], 'points_match': True, 'issues': [],
                             'needs_manual_review': False, 'confirmation_required': True, 'source': LAYOUT_READER}
                    now = timestamp()
                    db.execute("INSERT INTO frq_location_checks VALUES(?,?,?,?,?,?,'complete',0,?,'',?,?)",
                               (uid(), aid, qid, status['input_sha256'], encoded(context), encoded({'reader': LAYOUT_READER, 'revision_id': revision_id}),
                                encoded(check), now, now))
                    status = self.location_status(db, aid, q)
                if status['status'] != 'needs_confirmation':
                    raise ValueError(f"Question {problem_number} of Version {letter}: {status['label']}")
                self._confirm_location(db, aid, q, status, {'confirm_locations': True, 'template_id': templates[letter], 'printed_number': str(problem_number),
                                                            'regions': regions, 'notes': '', 'confirmed_together': True})
                count += 1
        return count
