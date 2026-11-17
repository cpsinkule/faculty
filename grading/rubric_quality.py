"""Rubric finalization: the term plan's mandatory, instructor-confirmed check of its rubrics, separate from scoring.

Built-in exclusions need no model and cannot be switched off. A model proposes further exclusive pairs, clarified wording and
suspected errors; the instructor edits and confirms them. Everything confirmed is an overlay BESIDE the immutable rubric: the
graders read it, while the rubric, its scores, the review screen and every export keep the original wording (department policy).
"""
import copy
import json
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect

from . import config, locations
from .assignments import version
from .cloud import RetryLater, later
from .drafting_tools import TemplatePages, rubric_tools
from .models import digest, encoded, items, text, uid
from .providers.base import ProviderError


# Requirement, 2026-09-20. A changed rule gets a new label: it is part of what a confirmation covers.
BUILTIN = 'rubric-builtin-v1'
# "for either parts a or b", "for Part a or Part b", "for any part": the line speaks for the WHOLE question. One named part does not.
_LETTERS = r' [a-z](?:(?:,| or| and| nor)+ (?:parts? )?[a-z])+'
_ALL_PARTS = rf'(?: (?:for|to|in) (?:(?:either|any|both|all) (?:of the )?parts?(?:{_LETTERS})?|parts?{_LETTERS}))?'
_NOTHING = rf'(?:no (?:answers?|response|attempt)(?: (?:given|shown|provided))?{_ALL_PARTS}|blank|(?:completely |totally |entirely )?(?:incorrect|wrong))'
# The WHOLE line must say it. "Part A: No Matrix Given or Completely Incorrect" is one part's zero and may sit beside another
# part's credit; measured on the instructor's sixteen rubrics, every such part line is left alone and every whole-question line is found.
NO_CREDIT = re.compile(rf'{_NOTHING}(?: ?(?:or|and|/) ?{_NOTHING})*')


def says_no_credit(description):
    """Whether a line's wording, as a whole, is the no-credit line (`NO_CREDIT`)."""
    return bool(NO_CREDIT.fullmatch(re.sub(r'\s+', ' ', description.lower()).strip(' .!:;')))


RULES = {'full_credit': 'the full-credit line', 'no_credit': 'the no-credit line'}


def builtin_exclusions(rubric, max_points):
    """Lines that exclude every other line of their question, decided by rule alone: a line worth the whole question (all of
    the credit, or in a deducting rubric all of the deduction), and a zero line that says only "no answer / completely incorrect"."""
    found = []
    for item in rubric['items']:
        if max_points > 0 and abs(item['points']) == max_points:
            found.append({'item_id': item['id'], 'rule': 'full_credit' if item['points'] > 0 else 'no_credit'})
        elif rubric['mode'] == 'positive' and item['points'] == 0 and says_no_credit(item['description']):
            found.append({'item_id': item['id'], 'rule': 'no_credit'})
    return found


def exclusive_pairs(entry, rubric):
    """Every pair in force for one question: built-in rules first, then the instructor's confirmed pairs."""
    ids, pairs = [i['id'] for i in rubric['items']], {}
    for rule in entry.get('builtin', []):
        for other in ids:
            if other != rule['item_id']:
                pairs.setdefault(tuple(sorted((rule['item_id'], other))), rule)
    for pair in entry['pairs']:
        pairs.setdefault(tuple(pair), None)
    return pairs


SEARCH_LIMIT = 200000  # Calls of the exact search. The dialog's mirror uses the same number, so both sides stop at the same place.
# The search's answers by the exact content searched (PERFORMANCE "PROCESS-WIDE CACHES"): a pure function of the rubric, the
# question's exclusions, its value and SEARCH_LIMIT, so an answer never goes stale. Every `final_decisions` read asks it for every
# question (`rubric_quality_status`); Exam 2's 24 questions took 45 ms each time, under the lifecycle lock, once per grade a
# Gradescope send checked (2026-10-27: 2,592 grades).
_COMBINATIONS, _COMBINATIONS_LOCK, COMBINATIONS_MAX = OrderedDict(), threading.Lock(), 2048


def heaviest_combination(rubric, entry, maximum):
    """`_heaviest_combination`, remembered by the content it searched; each caller gets its own copy of the answer."""
    try:
        key = digest([rubric, entry, maximum, SEARCH_LIMIT])
    except (TypeError, ValueError):  # content that is not plain JSON is searched every time, as before
        return _heaviest_combination(rubric, entry, maximum)
    with _COMBINATIONS_LOCK:
        if key in _COMBINATIONS:
            _COMBINATIONS.move_to_end(key)
            return copy.deepcopy(_COMBINATIONS[key])
    found = _heaviest_combination(rubric, entry, maximum)
    with _COMBINATIONS_LOCK:
        _COMBINATIONS[key] = copy.deepcopy(found)
        while len(_COMBINATIONS) > COMBINATIONS_MAX:
            _COMBINATIONS.popitem(last=False)
    return found


def _heaviest_combination(rubric, entry, maximum):
    """The most points a grader could still select at once, and with which lines, or None when that is within `maximum`.

    requirement, 2026-09-21: when the lines of a question add up to more than it is worth, some of them MUST be alternatives,
    and the finalization has to determine which. "Resolved" is therefore a fact about the exclusions, not an opinion: no
    selection they still allow may exceed the question's value. Measured on the first confirmed finalization: 7 of 16
    questions still allowed more (6 lines for 7 of 6 points, where point-slope and slope-intercept form were never paired),
    and the same question got 17 pairs in one version and 4 in the other. Parts need no rule of their own: their values add
    up to the question's, so an unresolved part shows as an unresolved question. A deducting rubric has no such bound.

    The search is exact. Lines that all exclude each other (the alternatives of one part, taken greedily heaviest first) can give
    at most one line, so the heaviest undecided line of each such group bounds what is left, and a branch that cannot exceed the
    question's value is closed. Should it still reach `SEARCH_LIMIT`, the question is UNDECIDED (`undecided`, no points or
    lines), never resolved: the best found so far once called too tangled a rubric resolved while a heavier selection existed.
    """
    if rubric['mode'] != 'positive':
        return None
    lines = sorted((item for item in rubric['items'] if item['points'] > 0), key=lambda item: -item['points'])
    apart = {frozenset(pair) for pair in exclusive_pairs(entry, rubric)}
    single = {g['id'] for g in rubric.get('groups', []) if g['selection'] == 'single'}
    for item in rubric['items']:  # The rubric's own structure counts as well.
        apart.update(frozenset((item['id'], other)) for other in item.get('excludes', []))
        if not item.get('stackable', True):
            apart.update(frozenset((item['id'], other['id'])) for other in rubric['items'] if other['id'] != item['id'])
        if item.get('group') in single:
            apart.update(frozenset((item['id'], other['id'])) for other in rubric['items'] if other['id'] != item['id'] and other.get('group') == item['group'])
    ids, points = [item['id'] for item in lines], [item['points'] for item in lines]
    cliques, clique_of = [], []  # Members in index order, which is heaviest first.
    for k, line in enumerate(ids):
        home = next((n for n, members in enumerate(cliques) if all(frozenset((line, ids[m])) in apart for m in members)), None)
        if home is None:
            home = len(cliques)
            cliques.append([])
        cliques[home].append(k)
        clique_of.append(home)
    used = [0] * len(cliques)
    best, calls = [0, []], [0]

    def bound(k):  # One line at most from each group without a selected line: its heaviest line not yet decided.
        return sum(next((points[m] for m in members if m >= k), 0) for n, members in enumerate(cliques) if not used[n])

    def search(k, total, chosen):
        calls[0] += 1
        if total > best[0]:
            best[:] = [total, list(chosen)]
        # Only a selection above the question's value changes the answer, so a branch that cannot beat max(best, value) is done.
        if calls[0] > SEARCH_LIMIT or k == len(lines) or total + bound(k) <= max(best[0], maximum + 1e-9):
            return
        if all(frozenset((ids[k], other)) not in apart for other in chosen):
            chosen.append(ids[k])
            used[clique_of[k]] += 1
            search(k + 1, total + points[k], chosen)
            used[clique_of[k]] -= 1
            chosen.pop()
        search(k + 1, total, chosen)
    search(0, 0, [])
    order = {item['id']: n for n, item in enumerate(rubric['items'])}
    if best[0] > maximum + 1e-9:  # A real selection above the value, even if the search stopped early: the question is open.
        return {'points': round(best[0], 4), 'maximum': maximum, 'lines': sorted(best[1], key=order.get)}
    if calls[0] > SEARCH_LIMIT:
        return {'points': None, 'maximum': maximum, 'lines': [], 'undecided': True}
    return None


def overflow_text(over):
    """What an open question still allows, in words for the instructor."""
    if over.get('undecided'):
        return (f"its lines are too entangled to prove that no selection exceeds {over['maximum']:g} points. Mark more of its alternatives "
                "(lines that can never be selected together) until the check can finish")
    return f"{', '.join(over['lines'])} can be selected together for {over['points']:g} of {over['maximum']:g} points"


def unresolved_questions(policy, context):
    """Questions whose confirmed (or proposed) exclusions still allow more points than the question is worth."""
    found = []
    for q in context['questions']:
        entry = policy_question(policy, q['question_id'], q['rubric_id'])
        over = heaviest_combination(q['rubric'], entry or {'pairs': [], 'builtin': q.get('builtin', [])}, q['max_points'])
        if over:
            found.append({'question_id': q['question_id'], **over})
    return found


FULL_CREDIT_RULE = 'full-credit-alone-v4'  # Frozen per batch and named in its acceptance scope.
FULL_CREDIT_RULES = ('full-credit-alone-v1', 'full-credit-alone-v2', 'full-credit-alone-v3', FULL_CREDIT_RULE)
_BY_STEPS = ('full-credit-alone-v2', 'full-credit-alone-v3', FULL_CREDIT_RULE)
_BY_PARTS = ('full-credit-alone-v3', FULL_CREDIT_RULE)
_PART = re.compile(r'\s*part\s+([a-h])\b', re.I)
# What `full_credit_alone` says of a grader that reaches full marks while refusing the full-credit line, by name (2026-10-11: the
# review card's causes read it, `review_queue.reason_causes`); the words are unchanged.
REFUSED_LINE = 'The grader selected the full-credit item but said its requirements were not satisfied.'
REFUSED_SUM = "The grader's selected lines add up to full credit, but it said the full-credit item's requirements were not satisfied."


def part_lines(rubric, entry, maximum):
    """{a part's full-credit line: the other lines of its part}, decided by the rubric's structure and the confirmed finalization.

    A part is the lines whose wording BEGINS "Part A:". Its full-credit line is known only where nothing is guessed: every part
    has ONE line worth more than its others, those lines add up to EXACTLY the question's value (6 + 2 of 8: each is then worth
    its whole part), and the confirmed exclusions already keep that line apart from every other line of its part. Anything less
    (one part only, a tie, parts that do not add up, a line the finalization never paired) gives no part lines at all.
    """
    if not entry or rubric['mode'] != 'positive':
        return {}
    parts = {}
    for item in rubric['items']:
        found = _PART.match(item.get('description') or '')
        if found:
            parts.setdefault(found[1].lower(), []).append(item)
    tops = {}
    for lines in parts.values():
        best = max(item['points'] for item in lines)
        top = [item for item in lines if item['points'] == best]
        if best <= 0 or len(top) != 1:
            return {}
        tops[top[0]['id']] = [item['id'] for item in lines if item['id'] != top[0]['id']]
    points = {item['id']: item['points'] for item in rubric['items']}
    if len(tops) < 2 or abs(sum(points[line] for line in tops) - maximum) > 1e-9:
        return {}
    apart = {frozenset(pair) for pair in exclusive_pairs(entry, rubric)}
    return tops if all(frozenset((line, other)) in apart for line, others in tops.items() for other in others) else {}


def adjusted(graded):
    """The point adjustments a grade already carries (a grader's approved deductions, GRADING "GRADERS TAKE OFF APPROVED
    DEDUCTIONS"), kept when its lines are read again, so the comparison is of the same grade."""
    return ((graded.get("score") or {}).get("adjustments")) or ()


def part_full_credit_alone(graded, entry, rubric, maximum, rule):
    """A PART's full-credit line ticked beside that part's step lines, read as that line alone (requirement, 2026-09-21).

    Measured on one batch: 17 of 54 additional assessments involved a grader breaking a confirmed exclusion, most of them the
    second grader ticking "Part A: Correct Answer ..." (6) beside Part A's step lines. One grader then gave 8 of 8 by the
    full-credit line, the other 8 of 8 by both parts' lines and the steps, and a third assessment was bought for a score nobody
    disputed. The same reading as the whole-question line, with the same limits: what the model ticked stays on the record
    (`subsumed_by`, `normalized.parts`, the raw answer); never when that part's zero line is ticked too (a contradiction to look
    at); and never if the score would change, so ticks whose double counting was hidden only by the question's ceiling stay as
    written, flagged as the exclusion they break.
    """
    from .scoring import score
    ruled = {found['item_id'] for found in (entry or {}).get('builtin', [])}
    applied = [a['rubric_item_id'] for a in graded['applications'] if a['applied']]
    if ruled & set(applied) or not graded.get('score'):
        return graded  # The whole-question lines have their own reading, and a no-credit line beside others is a contradiction.
    points = {item['id']: item['points'] for item in rubric['items']}
    read = []
    for line, others in part_lines(rubric, entry, maximum).items():
        beside = [i for i in applied if i in others]
        if line in applied and beside and all(points[i] > 0 for i in beside):
            read.append({'line': line, 'dropped': beside})
    if not read:
        return graded
    by = {i: part['line'] for part in read for i in part['dropped']}
    applications = [{**a, 'applied': False, 'subsumed_by': by[a['rubric_item_id']]} if a['rubric_item_id'] in by else a for a in graded['applications']]
    try:
        alone = score(rubric, applications, maximum, adjusted(graded))
    except ValueError:
        return graded
    if alone['score'] != graded['score']['score']:
        return graded
    reasons = [r for r in graded['review_reasons'] if not any(r.startswith(i + ':') for i in by)]
    return {**graded, 'applications': applications, 'score': alone, 'review_reasons': reasons, 'normalized': {'rule': rule, 'parts': read}}


def full_credit_alone(graded, entry, rubric, maximum, rule=FULL_CREDIT_RULE):
    """Read an assessment that ticks the full-credit line as that line ALONE (requirement, 2026-09-21).

    The instructor's rule makes that line exclusive with every other line. Graders were told so and two of three kept ticking
    it beside the step lines anyway (measured: the primary in 25 of 119 answers, the second in 8, the reviewer in none): all
    three then gave 8 of 8 with three different sets of ticks, the reviewer "matched neither", 20 of 64 additional assessments
    were bought for it and 8 of 19 responses stayed unresolved. The step lines beside a ticked full-credit line say nothing it
    does not: they are unticked, what the model originally ticked stays on the record (`normalized`, `subsumed_by`, and the raw
    answer), and the score is unchanged. Never when the no-credit line is ticked as well (that is a contradiction to look at),
    and never if the score would change.

    v2 (requirement, 2026-09-21) reads FULL MARKS BY THE STEP LINES the same way. Measured on one batch: in 21 of 89 disagreements
    every grader gave full marks, one by the full-credit line and another by every step line, and the additional assessment was
    bought for a score nobody disputed (8 more stayed unresolved with a third grader below them). Where the ticked lines earn
    points only, none excludes another, and they add up to EXACTLY the question's value, the assessment is the full-credit line:
    it is ticked with the step lines' evidence, the steps are unticked with `subsumed_by`, and the record says `route: steps`.
    A sum above the value is never read this way (lines that exclude each other were ticked together), nor a ticked zero line.

    v3 (requirement, 2026-09-21) first reads a PART's full-credit line beside its own step lines as that line alone
    (`part_full_credit_alone`); both parts' lines then add up to the question's value and are read as its full-credit line.

    v4 requires an explicit semantic eligibility decision before promoting component selections to full credit. An arbitrator
    rejected the ordered-triple requirement while its component selections totaled six: v3 overwrote that rejection. Equal
    points do not establish eligibility. Old receipts without the new field remain usable but are not promoted under v4. Under v4
    a grader that reaches full marks while saying the full-credit requirements were NOT met is flagged, whichever lines it ticked;
    its ticks and its score stay exactly as it gave them.

    In every version (fixed 2026-09-22) a line worth nothing or a deduction ticked beside the full-credit line ("Part B: No answer
    or completely incorrect") is a contradiction to look at, like the no-credit line, and an assessment without a score (a selection
    that cannot be scored) is never turned into a scored one here: the score it would have is exactly what cannot be compared.
    """
    from .scoring import score
    if rule in _BY_PARTS:
        graded = part_full_credit_alone(graded, entry, rubric, maximum, rule)  # v3: a part's line first, then the question's.
    ruled = {found['item_id']: found['rule'] for found in (entry or {}).get('builtin', [])}
    applied = [a['rubric_item_id'] for a in graded['applications'] if a['applied']]
    line = next((i for i in applied if ruled.get(i) == 'full_credit'), None)
    if rule == FULL_CREDIT_RULE and graded.get('full_credit_eligible') is False and (line or at_full_marks(graded, rubric, maximum, ruled)):
        reason = REFUSED_LINE if line else REFUSED_SUM
        return {**graded, 'review_reasons': list(dict.fromkeys([*graded['review_reasons'], reason]))}
    if not line and rule in _BY_STEPS:
        return full_marks_by_steps(graded, entry, rubric, maximum, ruled, applied, rule)
    points = {item['id']: item['points'] for item in rubric['items']}
    if not line or len(applied) == 1 or any(ruled.get(i) == 'no_credit' or points[i] <= 0 for i in applied if i != line) or graded.get('score') is None:
        return graded  # Alone already, or a zero line, a deduction or the no-credit line beside it, or nothing scored to keep equal.
    dropped = [i for i in applied if i != line]
    applications = [{**a, 'applied': False, 'subsumed_by': line} if a['rubric_item_id'] in dropped else a for a in graded['applications']]
    try:
        alone = score(rubric, applications, maximum, adjusted(graded))
    except ValueError:
        return graded
    if alone['score'] != graded['score']['score']:
        return graded
    # A doubt about the evidence of an unticked line is no longer about this assessment.
    reasons = [r for r in graded['review_reasons'] if not any(r.startswith(i + ':') for i in dropped)]
    return {**graded, 'applications': applications, 'score': alone, 'review_reasons': reasons, 'normalized': {'rule': rule, 'line': line, 'dropped': dropped}}


def at_full_marks(graded, rubric, maximum, ruled):
    """Whether a positive rubric's one whole-question full-credit line exists and the assessment's own score is the full value."""
    lines = [i for i, kind in ruled.items() if kind == 'full_credit']
    return rubric['mode'] == 'positive' and len(lines) == 1 and bool(graded.get('score')) and graded['score']['score'] >= maximum


def full_marks_by_steps(graded, entry, rubric, maximum, ruled, applied, rule=FULL_CREDIT_RULE):
    """Step lines that add up to exactly the question's value, read as its one full-credit line (see `full_credit_alone`)."""
    from .scoring import score
    if rule == FULL_CREDIT_RULE and graded.get('full_credit_eligible') is not True:
        return graded
    lines = [i for i, kind in ruled.items() if kind == 'full_credit']
    points = {item['id']: item['points'] for item in rubric['items']}
    if rubric['mode'] != 'positive' or len(lines) != 1 or len(applied) < 2 or not graded.get('score') or graded['score']['score'] != maximum:
        return graded
    if any(points[i] <= 0 for i in applied) or abs(sum(points[i] for i in applied) - maximum) > 1e-9:
        return graded  # A zero line says something the full-credit line does not; a sum above the value is lines that exclude each other.
    apart = {frozenset(pair) for pair in exclusive_pairs(entry, rubric)}
    if any(frozenset((a, b)) in apart for k, a in enumerate(applied) for b in applied[k + 1:]):
        return graded
    line = lines[0]
    evidence = [e for a in graded['applications'] if a['applied'] for e in a.get('evidence', [])]
    applications = [{**a, 'applied': True, 'evidence': evidence, 'read_from': applied,
                     'reason': f"Read as the full-credit line: the ticked step lines ({', '.join(applied)}) add up to the question's full value."} if a['rubric_item_id'] == line
                    else {**a, 'applied': False, 'subsumed_by': line} if a['applied'] else a for a in graded['applications']]
    try:
        alone = score(rubric, applications, maximum, adjusted(graded))
    except ValueError:
        return graded
    if alone['score'] != graded['score']['score']:
        return graded
    # A part's line that was read alone on the way here stays on the record beside this reading.
    return {**graded, 'applications': applications, 'score': alone, 'normalized': {**(graded.get('normalized') or {}), 'rule': rule, 'line': line, 'dropped': applied, 'route': 'steps'}}


def grader_rubric(rubric, entry):
    """What the graders read: each original line with the instructor's internal clarification and what it may never be combined
    with. Never stored, scored, shown as the rubric or exported. The import warnings are addressed to the instructor, who settled
    them in the finalization; graders quoted them back as doubts on every response (measured 2026-09-20).

    Nor do they read a line's `original_source_text`, the imported source kept for the audit: for a Gradescope export it repeats the
    description inside a JSON row (about half the rubric's size), and after the instructor corrects a line's wording
    (`correct_rubric_line`) it still holds the OLD wording, which a grader and the arbitrator each reported as a conflict in the rubric
    (trial batch 1c1ce6cb, 2026-09-22). The rubric revision keeps it; only what the graders are sent leaves it out."""
    if not entry:
        return rubric
    value, excluded = copy.deepcopy(rubric), {}
    for a, b in exclusive_pairs(entry, rubric):
        excluded.setdefault(a, []).append(b)
        excluded.setdefault(b, []).append(a)
    for item in value['items']:
        item.pop('original_source_text', None)
        if entry.get('clarifications', {}).get(item['id']):
            item['clarification'] = entry['clarifications'][item['id']]
        if excluded.get(item['id']):
            item['exclusive_with'] = sorted(excluded[item['id']])
    value['warnings'] = []
    return value


# EVERY question is asked at once (requirement, 2026-09-21: a pool of 8 sent sixteen questions in two waves, and a question asked a
# second time held its place for both requests, so a check of five-minute answers took over ten). This is a ceiling on threads
# only. What paces the requests is the reviewer connection's own in-flight limit inside `cloud_call`, which also halves itself
# on a rate limit. The printed problems are still cropped in the document sandbox a few at a time; no request waits for another's.
PARALLEL = 64
CROPS = threading.BoundedSemaphore(8)


def review_budget(endpoint, context, versions=1):
    """Room for the reviewer's answer. One request reads ONE question (requirement, 2026-09-21) and must name every line of it,
    so the answer grows with that question's rubric, never with the exam; an answer cut short is refused and billed. A problem
    read on every version at once (2026-09-25) names every line of each version: room for that many."""
    lines = max((len(q['rubric']['items']) for q in context['questions']), default=0) * max(1, versions)
    return {**endpoint, 'max_tokens': max(endpoint['max_tokens'], min(32768 if versions > 1 else 16384, 2048 + 48 * lines))}


ELSEWHERE = 40  # comments on other problems' lines sent with one review, at most
# NOTHING ASKED TWICE: the instructor's answers a check reads, the latest kept when there are more (2026-10-26 review: Exam 1's 47
# answers at a limit of 40 left its seven earliest out of later checks; Exam 2 reached 31 before its Version C and makeup papers).
EARLIER_ANSWERS = 80


def comments_elsewhere(context, questions):
    """The instructor's line comments on the plan's OTHER problems, each once with the line it was written on (2026-09-28). They
    are policy, not a rubric: Quiz 3's comment that a calculator rref counts only when shown was written on one problem, and the
    next problem's "algebraic solution" line, reviewed without it, left graders asking the same question. [] when there are none,
    so a payload without comments elsewhere is exactly what it was."""
    mine, seen, found = {q['question_id'] for q in questions}, set(), []
    for q in sorted(context.get('questions', []), key=lambda q: (printed_order(q), q.get('exam_version', ''))):
        if q['question_id'] in mine:
            continue
        lines = {item['id']: item.get('description', '') for item in q['rubric']['items']}
        for iid, comment in sorted((q.get('comments') or {}).items()):
            if comment.strip() and comment not in seen and len(found) < ELSEWHERE:
                seen.add(comment)
                found.append({'problem': q['title'], **({'version': q['exam_version']} if q.get('exam_version') else {}),
                              'line': lines.get(iid, ''), 'comment': comment})
    return found


def question_payload(context, question, images=0):
    """What the reviewer reads for one question: its rubric, its built-in exclusions, its answer key and solution, and where
    among the attached pictures its printed problem is. No other question shares the context; only the instructor's comments
    on other problems' lines come along, as their policy (`comments_elsewhere`)."""
    reference = {k: v for k, v in (question.get('reference') or {}).items() if k != 'printed'}
    elsewhere = comments_elsewhere(context, [question])
    return {'rules': context['rules'], 'exam_version': question['exam_version'],
        'question': {**{k: question[k] for k in ('title', 'printed_number', 'max_points', 'rubric', 'builtin')},
                     # The instructor's own guidance about lines (2026-09-24): the reviewer folds each into that line's clarification.
                     **({'instructor_comments': question['comments']} if question.get('comments') else {})},
        'reference': {**reference, 'problem_images': list(range(images))},
        **({'instructor_comments_elsewhere': elsewhere} if elsewhere else {}),
        # RUBRIC MEMORIES (2026-10-14): the course's kept rules and watches, named only where the course has some, less those that
        # contradict this question's rubric or the instructor's word on it (FITTED TO EACH QUESTION, 2026-10-28).
        **rules_for(context, [question]),
        **assignment_word(context)}


def rules_for(context, questions):
    """{'standing_rules': ...} for a request about `questions`, without the memories Jev found contradicting one of them
    (`rubric_memory.memory_fit`, kept beside the context as `memory_fit`); {} where none is left, so the request is the one a
    course without memories sends."""
    left = {mid for q in questions for mid in (context.get('memory_fit') or {}).get(q['question_id'], [])}
    rules = [rule for rule in context.get('standing_rules') or [] if rule['id'] not in left]
    return {'standing_rules': rules} if rules else {}


def assignment_word(context):
    """NOTHING ASKED TWICE (requirement, 2026-10-24): the instructor's own word on THIS assignment beside the rubric, named only where
    there is some, so every other request is the request it always was: their quick answers given before drafting
    (`assignment_choices`, GRADING "RUBRIC CHOICES") and what they answered to this assignment's earlier rubric questions
    (`earlier_answers`)."""
    return {key: context[key] for key in ('assignment_choices', 'earlier_answers') if context.get(key)}


def clarified(row, known):
    value = row.get('clarifications') or {}
    if not isinstance(value, dict) or set(value) - known:
        raise ValueError('A clarification must belong to an item of its own question.')
    return {key: note for key in sorted(value) if (note := text(value[key], 'Clarification', 2000).strip())}


def normalize_questions(rows, context, *, model=False):
    expected = {q['question_id']: q for q in context['questions']}
    result, seen = [], set()
    for row in items(rows, 'Question exclusions', 100):
        if not isinstance(row, dict) or not isinstance(row.get('question_id'), str) or row['question_id'] not in expected or row['question_id'] in seen:
            raise ValueError('Exclusions must name each supplied question exactly once.')
        q = expected[row['question_id']]
        if row.get('rubric_id') != q['rubric_id']:
            raise ValueError('Exclusions name a different rubric revision.')
        known = {i['id'] for i in q['rubric']['items']}
        pairs, reasons, notes = set(), [], {}
        if model:
            decided = set()
            proposed = []
            for entry in items(row.get('items'), 'Item findings', 1000):
                if not isinstance(entry, dict) or not isinstance(entry.get('item_id'), str) or entry['item_id'] not in known or entry['item_id'] in decided:
                    raise ValueError('The reviewer must examine every rubric item exactly once.')
                decided.add(entry['item_id'])
                targets = items(entry.get('excludes'), 'Excluded items', 1000)
                proposed.extend([entry['item_id'], other] for other in targets)
                reason = text(entry.get('reason', ''), 'Exclusion explanation', 4000)
                if targets:
                    reasons.append({'item_id': entry['item_id'], 'reason': reason})
                notes[entry['item_id']] = entry.get('clarification') or ''
            if decided != known:
                raise ValueError('The reviewer omitted rubric items. No exclusions were confirmed.')
        else:
            proposed = items(row.get('pairs'), 'Exclusive pairs', 20000)
            notes = row.get('clarifications')
        ruled = {(rule['item_id']) for rule in q['builtin']}
        for pair in proposed:
            if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(i, str) or i not in known for i in pair) or pair[0] == pair[1]:
                raise ValueError('An exclusive pair must contain two different items from the same question.')
            if not ruled & set(pair):  # A built-in rule already covers it and cannot be edited away.
                pairs.add(tuple(sorted(pair)))
        seen.add(q['question_id'])
        result.append({'question_id': q['question_id'], 'rubric_id': q['rubric_id'], 'pairs': [list(p) for p in sorted(pairs)], 'reasons': reasons,
            'clarifications': clarified({'clarifications': notes}, known), 'builtin': q['builtin']})
    if seen != set(expected):
        raise ValueError('Review the exclusions for every uploaded question.')
    return sorted(result, key=lambda q: q['question_id'])


def decision_fields(issue):
    """GRADING: display metadata belongs to the term plan's retained check, never its rubric or score. Older replies have none.
    Empty choices mean open feedback; the UI must not manufacture a policy choice out of the finding's prose."""
    found = {}
    for key, limit in (('title', 120), ('decision', 600), ('group', 80), ('topic', 60)):
        if key in issue and (value := text(issue[key], 'Concern ' + key, limit).strip()):
            found[key] = value
    if 'choices' in issue:
        choices = items(issue['choices'], 'Concern choices', 4)
        if len(choices) == 1:
            raise ValueError('A concern offers either no preset choices or at least two supported alternatives.')
        labels, feedback, rows = set(), set(), []
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError('Each concern choice needs a label and feedback for the reviewer.')
            label = text(choice.get('label'), 'Choice label', 120, False).strip()
            response = text(choice.get('feedback'), 'Choice feedback', 4000, False).strip()
            if not label or not response or label in labels or response in feedback:
                raise ValueError('Concern choices must have distinct, nonempty labels and feedback.')
            labels.add(label)
            feedback.add(response)
            rows.append({'label': label, 'feedback': response,
                         'consequence': text(choice.get('consequence', ''), 'Choice consequence', 160).strip()})
        if rows:
            found['choices'] = rows
    return found


# AN EDIT ONLY THE INSTRUCTOR CAN MAKE (requirement, 2026-10-29). The reviewer (rubric_quality v13 and its siblings) may name the points
# change the instructor's own word already asks for (`edit`: [{version, item_id, points}]); it is kept by question ID, only for lines
# that exist and whose points it changes, within the question's maximum, and the window offers it as one Approve
# (`apply_rubric_edit`), which saves it as the instructor's own rubric edit. Anything it cannot read is dropped, never a failure.
def edit_fields(issue, by_version):
    """{'edit': [{question_id, item_id, points}]} of one finding (`by_version`: {version name: question}), or {}."""
    edit = issue.get('edit') if isinstance(issue, dict) else None
    lines = edit.get('lines') if isinstance(edit, dict) else None
    lone = next(iter(by_version.values())) if len({q['question_id'] for q in by_version.values()}) == 1 else None
    found, seen = [], set()
    for line in lines if isinstance(lines, list) else []:
        if not isinstance(line, dict):
            continue
        q, points = by_version.get(line.get('version')) or lone, line.get('points')
        if not q or type(points) not in (int, float):
            continue
        item = next((i for i in q['rubric']['items'] if i['id'] == line.get('item_id')), None)
        maximum = float(q.get('max_points') or 0)
        if (not item or (q['question_id'], item['id']) in seen or float(points) == float(item.get('points', 0))
                or (maximum and abs(float(points)) > maximum)):
            continue
        seen.add((q['question_id'], item['id']))
        found.append({'question_id': q['question_id'], 'item_id': item['id'], 'points': points})
    return {'edit': found} if found else {}


def question_errors(issues, question):
    """One question's suspected errors as the reviewer named them, or ValueError: each names items of that question only."""
    known = {item['id'] for item in question['rubric']['items']}
    errors = []
    for issue in items(issues, 'Suspected errors', 50):
        refs = items(issue.get('item_ids'), 'Suspected items', 100) if isinstance(issue, dict) else None
        if refs is None or any(not isinstance(i, str) or i not in known for i in refs):
            raise ValueError('A suspected error names an unknown item.')
        errors.append({'question_id': question['question_id'], 'item_ids': list(dict.fromkeys(refs)),
                       'message': text(issue.get('message'), 'Suspected error', 4000, False), **decision_fields(issue), **fix_fields(issue),
                       # A problem's finding names its versions' lines itself (`problem_findings`); a lone question's names its own.
                       **({} if 'versions' in issue else edit_fields(issue, {question.get('exam_version') or '': question}))})
    return errors


# BOOKKEEPING IS FIXED, NOT ASKED (requirement, 2026-10-24). The
# reviewer marks such a finding with a `fix`: "guidance" when the clarifications it wrote already carry the one right reading, "key"
# when an answer key part lacks forms the instructor's own rubric line, comment or other versions accept (`keys`: the part and the
# forms to add). A finding with choices is a decision and never a fix; a fix that cannot be read is dropped and the finding is asked
# as before. The window lists fixed findings apart, each with Undo (`fix_states`, `undo_rubric_fix`); only forms are ever ADDED to a
# key, only on a question asked by this check that has no grade yet (`apply_rubric_fixes`).
FIX_KINDS = ('guidance', 'key')


def fix_fields(issue):
    """{'fix': ...} of one finding as the reviewer marked it, or {} (a decision, as every finding was before)."""
    fix = issue.get('fix') if isinstance(issue, dict) else None
    if not isinstance(fix, dict) or fix.get('kind') not in FIX_KINDS or issue.get('choices'):
        return {}
    note = fix.get('note') if isinstance(fix.get('note'), str) else ''
    found = {'kind': fix['kind'], 'note': ' '.join(note.split())[:1000]}
    if fix.get('applied') is not None:  # carried by a resolution from the check that applied it (`with_fix_states`)
        found['applied'] = fix['applied']
    if fix['kind'] == 'key':
        rows = []
        for row in fix.get('keys') if isinstance(fix.get('keys'), list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('part', ''), str) or not isinstance(row.get('add'), list):
                return {}
            forms = [' '.join(form.split())[:500] for form in row['add'] if isinstance(form, str) and form.strip()]
            if forms:
                rows.append({'part': row.get('part', '').strip()[:40], 'add': list(dict.fromkeys(forms))[:20]})
        if not rows and 'applied' not in found:
            return {}
        found['keys'] = rows
    return {'fix': found}


def validate_question(value, question):
    """One question's reviewed answer, or ValueError. The reviewer names items only; the question is the one it was asked about."""
    if not isinstance(value, dict):
        raise ValueError('The rubric reviewer returned an invalid object.')
    errors = question_errors(value.get('suspected_errors'), question)
    row = {'question_id': question['question_id'], 'rubric_id': question['rubric_id'], 'items': value.get('items')}
    entry = normalize_questions([row], {'questions': [question]}, model=True)[0]
    # The prompt tells the reviewer to carry every instructor comment into its line's clarification. Where it left one empty, the
    # comment itself is proposed, and the result says so: the instructor's guidance is never lost between their page and the graders.
    fallback = sorted(iid for iid, comment in (question.get('comments') or {}).items() if not entry['clarifications'].get(iid))
    if fallback:
        entry['clarifications'] = dict(sorted({**entry['clarifications'], **{iid: question['comments'][iid] for iid in fallback}}.items()))
        entry['comment_fallback'] = fallback
    return {**entry, 'summary': text(value.get('summary', ''), 'Question summary', 4000)}, errors


# EVERY VERSION OF A PROBLEM IN ONE REQUEST (requirement, 2026-09-25). A check of a plan with versions is queued under this scheme:
# the questions are grouped into problems (each problem with its copies on the other versions), and each problem is ONE request that
# holds every version's rubric, answer key and printed problem. It is still one PROBLEM per request, never the whole exam (2026-09-21).
# The answer keeps each version's findings under that version's own question, so confirmation, grading and the record are unchanged,
# and adds the differences between the versions' rubrics (`consistency_issues`). A plan without versions, and a check queued before
# the scheme, are checked one question per request as before.
VERSIONS_SCHEME = 'versions-v1'


# A CHECK ASKS ONLY WHAT DOES NOT STAND (requirement, 2026-10-05: Quiz 4's makeup Version E sent all five versions' rubrics to the
# reviewer again, though A to D's finalization stood and only E had none). Where the plan's latest confirmation still stands for some
# questions (`finalization_standing`, nothing left open) and not for others, those are SETTLED: the check's input carries their
# confirmed entries (`settled`, never part of what a confirmation covers) and only the others are asked. Under the versions scheme
# a problem is still ONE request with every version side by side, the settled ones marked with their confirmed guidance as the
# reference (`rubric_quality_added`); the reviewer answers for the others only and may still report findings on any version. A
# problem with nothing to ask is not asked. The result gives each settled question its confirmed entry, so the review window,
# Resolve concerns and the ONE confirmation that approves every rubric work unchanged, and a settled question's guidance (and its
# grades) stays as it was unless the instructor edits it. Every question standing (an explicit re-check) or none is a full check.
SETTLED_PROMPT = 'rubric_quality_added'


def settled_entries(context, standing):
    """{question_id: confirmed entry} of the questions a check need not ask again, or {} when it asks every question."""
    found = {q['question_id']: dict(row['entry']) for q in context['questions']
             if (row := standing.get(q['question_id'])) and row['rubric_id'] == q['rubric_id'] and not row['open']}
    return found if 0 < len(found) < len(context['questions']) else {}


def settled_of(context):
    """A check input's settled entries (`settled_entries`), {} for a full check."""
    return (context.get('settled') or {}).get('questions') or {}


# A CHECK ASKS ONLY WHAT CHANGED (requirement, 2026-10-25: on Exam 2, changing Version B's R7_16 from 1 to 0.5, the one concern left
# after two rounds of Resolve concerns, asked for a check of all 24 rubrics again, which would have replaced the 65 concerns already
# resolved). Before any confirmation stands for a question, the plan's latest finished check is where the instructor's work on it
# stands: each question whose input is still what that check read is CARRIED (`carried_entries`), settled with that check's entry
# exactly as a standing confirmation's entry is settled, and only the others are asked. The check's concerns that name only carried
# questions, none in a problem asked again, stay listed (`settled.carried`). Carrying confirms nothing: the one confirmation still
# approves every rubric, and a question that stands keeps its confirmed entry.
def settled_words(context):
    """(the questions a partial check asked, what the others keep), in its summary's words."""
    carried = ((context.get('settled') or {}).get('carried') or {}).get('questions') or []
    if not carried:
        return 'without a standing confirmation', 'confirmed clarifications and exclusions'
    if len(carried) < len(settled_of(context)):
        return 'that changed or have no standing confirmation', 'clarifications and exclusions as they stood'
    return 'that changed since the last check', 'clarifications and exclusions from the last check'


def scope_text(questions, ids):
    """The questions `ids` names, in words: a version whose every question is named is named as the version."""
    by_version = {}
    for q in questions:
        by_version.setdefault(q['exam_version'], []).append(q)
    parts = []
    for name, members in sorted(by_version.items()):
        named = [q for q in sorted(members, key=printed_order) if q['question_id'] in ids]
        if name and named and len(named) == len(members) and len(by_version) > 1:
            parts.append(f'Version {name}')
        else:
            parts.extend((f'Version {name} · ' if name else '') + q['title'] for q in named)
    return ', '.join(parts)


def origins(compiler):
    """What a rubric revision was made from, followed back through the instructor's edits and corrections: the question an
    adaptation was made from, and the problem a drafting of every version wrote it as."""
    found, seen = [], 0
    while isinstance(compiler, dict) and seen < 50:
        source = compiler.get('adapted_from')
        if isinstance(source, dict) and isinstance(source.get('question_id'), str):
            found.append(['question', source['question_id']])
        if isinstance(compiler.get('family'), str) and isinstance(compiler.get('generation_id'), str):
            found.append(['family', compiler['generation_id'], compiler['family']])
        compiler, seen = compiler.get('based_on'), seen + 1
    return found


def linked_groups(questions):
    """Questions known to be one problem from how their rubrics were made. A link that would put two questions of one version
    together is contradictory and is dropped: the grouping request decides those."""
    parent = {q['question_id']: q['question_id'] for q in questions}

    def root(qid):
        while parent[qid] != qid:
            parent[qid] = parent[parent[qid]]
            qid = parent[qid]
        return qid
    families = {}
    for q in questions:
        for origin in q.get('origins', []):
            if origin[0] == 'question' and origin[1] in parent:
                parent[root(q['question_id'])] = root(origin[1])
            elif origin[0] == 'family':
                families.setdefault(tuple(origin[1:]), []).append(q['question_id'])
    for members in families.values():
        for qid in members[1:]:
            parent[root(qid)] = root(members[0])
    grouped = {}
    for q in questions:
        grouped.setdefault(root(q['question_id']), []).append(q)
    return [[q['question_id'] for q in group] for group in grouped.values()
            if len(group) > 1 and len({q['exam_version'] for q in group}) == len(group)]


def printed_order(question):
    number = locations.printed_number(question.get('printed_number') or '')
    return (int(number) if number.isdigit() else 10**6, question['title'])


def group_payload(questions, linked, references):
    """What the grouping request reads: every question by a short ref, what it asks and what its rubric pays for. Text only."""
    refs = {q['question_id']: f"{q['exam_version']}{index}" for index, q in enumerate(sorted(questions, key=lambda q: (q['exam_version'], printed_order(q))), 1)}
    rows = []
    for q in sorted(questions, key=lambda q: refs[q['question_id']]):
        reference = references.get(q['question_id'], {})
        rows.append({'ref': refs[q['question_id']], 'version': q['exam_version'], 'printed_number': q.get('printed_number', ''), 'title': q['title'],
                     'points': q['max_points'], 'statement': reference.get('problem', ''),
                     'accepted_answers': [answer for part in reference.get('answer_key', []) for answer in part.get('accept', [])],
                     'rubric_lines': [item['description'] for item in q['rubric']['items']]})
    return {'questions': rows, 'linked': [sorted(refs[qid] for qid in group) for group in linked]}, refs


def question_groups(value, questions, linked, refs):
    """Each question with its copies on the other versions, or ValueError: the grouping is then asked once more. Every question
    exactly once, at most one of each version per group, and every group already known from the rubrics' making kept whole."""
    if not isinstance(value, dict):
        raise ValueError('The grouping returned an invalid object.')
    by_ref = {ref: qid for qid, ref in refs.items()}
    version = {q['question_id']: q['exam_version'] for q in questions}
    groups, seen = [], set()
    for group in items(value.get('groups'), 'Question groups', max(1, len(questions))):
        members = items(group.get('refs') if isinstance(group, dict) else None, 'Grouped questions', len(questions))
        qids = [by_ref.get(ref) for ref in members]
        if not qids or None in qids or seen & set(qids) or len({version[qid] for qid in qids}) != len(qids):
            raise ValueError('Group every question exactly once, with at most one question of each version per group.')
        seen.update(qids)
        groups.append(qids)
    if seen != set(version):
        raise ValueError(f"The grouping left out {len(set(version) - seen)} questions.")
    where = {qid: index for index, group in enumerate(groups) for qid in group}
    if any(len({where[qid] for qid in group}) > 1 for group in linked):
        raise ValueError('Keep together the questions `linked` says are one problem.')
    return groups, [text(note, 'Grouping note', 2000).strip() for note in items(value.get('notes', []), 'Grouping notes', 50) if isinstance(note, str) and note.strip()]


def guidance_lines(question, entry):
    """One question's guidance line by line, as the reviewer reads guidance that is already held: each item's clarification and
    the items it excludes."""
    notes, pairs = entry.get('clarifications') or {}, entry.get('pairs') or []
    return [{'item_id': item['id'], 'clarification': notes.get(item['id'], ''),
             'excludes': sorted(other for pair in pairs if item['id'] in pair for other in pair if other != item['id'])}
            for item in question['rubric']['items']]


def problem_payload(context, family, counts, settled=None):
    """What the reviewer reads for one problem: every version's question as `question_payload` gives it, which attached
    pictures show each version's printed problem, and the instructor's comments on the other problems' lines. A settled version
    (`settled`: {question_id: confirmed entry}) is marked, with its confirmed guidance."""
    members, start = [], 0
    for question, count in zip(family, counts):
        single = question_payload(context, question, count)
        members.append({'version': question['exam_version'], 'question': single['question'],
                        'reference': {**single['reference'], 'problem_images': list(range(start, start + count))},
                        **({'settled': True, 'confirmed': guidance_lines(question, settled[question['question_id']])}
                           if settled and question['question_id'] in settled else {})})
        start += count
    elsewhere = comments_elsewhere(context, family)
    # LINKED VERSIONS (2026-10-02): the instructor's links and the candidates to verify, named only where some lie in THIS problem
    # (by question: item IDs such as R1 repeat across problems), each line by its version and item ID.
    qids = {q['question_id'] for q in family}
    links = [{**link, 'lines': [{'version': line['version'], 'item_id': line['item_id']} for line in link['lines']]}
             for link in (context.get('resolves') or {}).get('links') or context.get('known_links') or []
             if all(line.get('question_id') in qids for line in link['lines'])]
    return {'rules': context['rules'], 'problem': {'versions': members}, **({'instructor_comments_elsewhere': elsewhere} if elsewhere else {}),
            **({'known_links': links} if links else {}),
            **rules_for(context, family), **assignment_word(context)}


def version_items(value, by_version, label):
    """{version: [item IDs]} from a v4 finding's `item_ids` ([{version, item_ids}]), or ValueError. Each version's IDs are checked
    against its own rubric by `validate_question`."""
    found = {}
    for row in items(value, label, len(by_version)):
        if not isinstance(row, dict) or row.get('version') not in by_version or row['version'] in found:
            raise ValueError('A finding names its items under a version the problem does not have, or twice.')
        found[row['version']] = items(row.get('item_ids'), 'Suspected items', 100)
    return found


def problem_findings(value, family):
    """The problem's suspected errors as (version, issue, shared presentation) per version, or ValueError. A finding of the reviewer
    v4 names every version it arises on ONCE (LINKED VERSIONS, requirement, 2026-10-02): it is kept as one row per version, so
    confirmation, numbering and resolution stay per question, and the rows share the server's `shared` key, which presents them
    as one decision. An older finding names one `version`."""
    by_version = {q['exam_version']: q for q in family}
    found = []
    for n, issue in enumerate(items(value.get('suspected_errors'), 'Suspected errors', 100)):
        if not isinstance(issue, dict):
            raise ValueError('A suspected error must be an object.')
        if 'versions' not in issue:
            if issue.get('version') not in by_version:
                raise ValueError('A suspected error names a version the problem does not have.')
            found.append((issue['version'], issue, {}))
            continue
        named = list(dict.fromkeys(items(issue.get('versions'), 'Versions of a suspected error', len(family))))
        if not named or any(name not in by_version for name in named):
            raise ValueError('A suspected error names a version the problem does not have.')
        refs = version_items(issue.get('item_ids', []), {name: by_version[name] for name in named}, 'Items of a suspected error')
        notes = {}
        for row in items(issue.get('version_notes', []), 'Version notes', len(family)):
            if not isinstance(row, dict) or row.get('version') not in named:
                raise ValueError('A version note names a version its finding does not.')
            if note := text(row.get('note'), 'Version note', 1000).strip():
                notes[row['version']] = note
        shared = {'shared': f"{family[0]['question_id']}:{n}", 'versions_named': named} if len(named) > 1 else {}
        shared.update(edit_fields(issue, by_version))
        for name in named:
            found.append((name, {**issue, 'item_ids': refs.get(name, []), **version_fix(issue, name)},
                          {**shared, **({'version_note': notes[name]} if name in notes else {})}))
    return found


def version_fix(issue, name):
    """A finding's fix as it reaches one of its versions: a key fix keeps only that version's keys, and a version whose key needs
    nothing has the fix in its guidance only."""
    fix = issue.get('fix')
    if not isinstance(fix, dict) or fix.get('kind') != 'key':
        return {}
    keys = [row for row in fix.get('keys') or [] if isinstance(row, dict) and row.get('version') == name]
    return {'fix': {**fix, 'keys': keys} if keys else {'kind': 'guidance', 'note': fix.get('note', '')}}


def validate_problem(value, family, asked=None):
    """One problem's reviewed answer, or ValueError: {question_id: (entry, errors)} for every version as `validate_question` gives
    them, then the differences between the versions' rubrics and the reviewer's summary. Where only `asked` (some of the family's
    questions) were asked, the others being settled, only they need an answer and have an entry; a settled version has None, with
    the findings that name it."""
    if not isinstance(value, dict):
        raise ValueError('The rubric reviewer returned an invalid object.')
    by_version = {q['exam_version']: q for q in family}
    wanted = {q['exam_version'] for q in (family if asked is None else asked)}
    rows = {}
    for row in items(value.get('versions'), 'Reviewed versions', len(family)):
        if not isinstance(row, dict) or row.get('version') not in by_version or row['version'] in rows:
            raise ValueError('Review every version of the problem exactly once.')
        rows[row['version']] = row
    rows = {name: row for name, row in rows.items() if name in wanted}  # A settled version's echo is never its guidance.
    if set(rows) != wanted:
        raise ValueError('The reviewer left out a version of the problem.')
    errors, extras = {name: [] for name in by_version}, {name: [] for name in by_version}
    for name, issue, extra in problem_findings(value, family):
        errors[name].append(issue)
        extras[name].append(extra)
    found = {}
    for name, q in by_version.items():
        if name in wanted:
            entry, listed = validate_question({'summary': rows[name].get('summary', ''), 'suspected_errors': errors[name], 'items': rows[name].get('items')}, q)
        else:
            entry, listed = None, question_errors(errors[name], q)
        found[q['question_id']] = (entry, [{**issue, **extra} for issue, extra in zip(listed, extras[name])])
    consistency = []
    for issue in items(value.get('consistency_issues', []), 'Consistency issues', 50):
        named = items(issue.get('versions') if isinstance(issue, dict) else None, 'Versions of an issue', len(family))
        if any(name not in by_version for name in named):
            raise ValueError('A consistency issue names a version the problem does not have.')
        refs = version_items(issue.get('item_ids', []), by_version, 'Items of a consistency issue')
        lines = {}
        for name, ids in refs.items():
            known = {item['id'] for item in by_version[name]['rubric']['items']}
            if any(not isinstance(i, str) or i not in known for i in ids):
                raise ValueError('A consistency issue names an unknown item.')
            if ids:
                lines[by_version[name]['question_id']] = list(dict.fromkeys(ids))
        consistency.append({'question_ids': [by_version[name]['question_id'] for name in dict.fromkeys(named)],
                            'message': text(issue.get('message'), 'Consistency issue', 4000, False), **decision_fields(issue),
                            **edit_fields(issue, by_version), **({'lines': lines} if lines else {})})
    return found, consistency, text(value.get('summary', ''), 'Problem summary', 4000)


LINK_KINDS = ('same', 'related')


def parallel_links(value, family):
    """The reviewer's parallel lines of one problem (versions v4) as line links [{kind, origin, note, lines:[{question_id, item_id}]}],
    and how many sets were dropped. Links are an aid to the instructor, never guidance: a set naming an unknown version or line,
    two lines of one version, a line already in another set or fewer than two lines is DROPPED, never a reason to refuse the
    mandatory check."""
    by_version = {q['exam_version']: q for q in family}
    known = {name: {item['id'] for item in q['rubric']['items']} for name, q in by_version.items()}
    rows = value.get('parallel_lines') if isinstance(value, dict) else None
    links, used, dropped = [], set(), 0
    for row in rows if isinstance(rows, list) else []:
        lines = row.get('lines') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get('kind') not in LINK_KINDS or not isinstance(lines, list) or not 2 <= len(lines) <= len(family)
                or any(not isinstance(line, dict) or not isinstance(line.get('version'), str) or line['version'] not in known
                       or not isinstance(line.get('item_id'), str) or line['item_id'] not in known[line['version']] for line in lines)
                or len({line['version'] for line in lines}) != len(lines) or any((line['version'], line['item_id']) in used for line in lines)):
            dropped += 1
            continue
        used.update((line['version'], line['item_id']) for line in lines)
        note = row.get('note') if isinstance(row.get('note'), str) else ''
        links.append({'kind': row['kind'], 'origin': 'reviewer', **({'note': note.strip()[:1000]} if note.strip() else {}),
                      'lines': [{'question_id': by_version[line['version']]['question_id'], 'item_id': line['item_id']}
                                for line in sorted(lines, key=lambda line: line['version'])]})
    return links, dropped


# RESOLVING THE LISTED CONCERNS (requirement, 2026-09-25). A resolution is a
# check of the same rubric inputs: the completed check's input plus `resolves` (the response, the concerns as listed, the
# clarifications and exclusions as the review window held them, the problems to ask). Each problem is ONE request with all its
# versions, never the whole exam. Its result is shaped like a check's, so review and confirmation are unchanged: nothing is
# approved until the instructor confirms, and the rubric's wording and points are never touched.
# v2 (instructor, the same day: "put a comment box under each one of the concerns at the top", collapsible): a comment per concern
# instead of one response. Only the concerns the instructor commented on are sent, each with its comment, and only the problems
# they name are asked; a concern left without a comment stays listed as it was. A v1 input (one response for every concern) is
# still read as that response on each concern.
# v3 (instructor, the same day: "part of the prompt should be to tell the model to resolve any remaining concerns in a way
# consistent with the problem/feedback"): EVERY concern of the problems a concern names is sent, the commented ones with their
# comments, and the reviewer settles the others itself, consistently with the printed problem, the key, the rubric and the
# instructor's comments (their comments on other problems' concerns come along as `instructor_feedback_elsewhere`). No comment is
# required. A v1 or v2 input keeps sending only what it sent.
RESOLVE = 'resolve-concerns-v3'


def concern_list(result):
    """The findings listed at the top of a completed check, in their order, each naming its questions."""
    return ([{**issue, 'kind': 'suspected_error', 'question_ids': [issue['question_id']]} for issue in result.get('suspected_errors') or []]
            + [{**issue, 'kind': 'consistency'} for issue in result.get('consistency_issues') or []])


def commented(resolves):
    """The concerns of a resolution the instructor commented on, as (number, concern, comment)."""
    return [(n, c, c.get('response') or resolves.get('response', '')) for n, c in enumerate(resolves['concerns'])
            if c.get('response') or resolves.get('response')]


def to_resolve(resolves):
    """The concerns a resolution sends, as (number, concern, comment or ''): under v3 every concern, each with this round's
    comment or else the one an earlier round kept beside it, else the commented ones."""
    if resolves.get('rule') == 'resolve-concerns-v3':
        return [(n, c, c.get('response') or c.get('comment', '')) for n, c in enumerate(resolves['concerns'])]
    return commented(resolves)


def with_fix_states(concerns, states):
    """The concerns a resolution sends, each fix as it stands: an undone or skipped fix is gone (the concern is a decision now), and
    an applied key fix names what it added, so the resolution's result keeps it fixed without adding anything again."""
    found = []
    for n, concern in enumerate(concerns):
        state = states.get(n) or states.get(str(n))
        if concern.get('fix') and (not state or state['state'] != 'applied'):
            concern = {k: v for k, v in concern.items() if k != 'fix'}
        elif concern.get('fix') and concern['fix'].get('kind') == 'key':
            concern = {**concern, 'fix': {**concern['fix'], 'applied': state.get('added') or []}}
        found.append(concern)
    return found


def answered_concerns(result, responses):
    """The concerns a completed check listed (`concern_list`), each one the instructor commented on with its comment (`responses`:
    {number: comment}), or Conflict when there is nothing to resolve or a comment names a concern the check does not list."""
    concerns = concern_list(result)
    if not concerns:
        raise Conflict('This review lists no concerns to resolve.')
    if responses and max(responses) >= len(concerns):
        raise Conflict('A comment names a concern this review does not list. Refresh first.')
    return [{**concern, 'response': responses[n]} if n in responses else concern for n, concern in enumerate(concerns)]


def resolve_links(sets, instructor, questions):
    """The line links as they stand, told to a resolution (LINKED VERSIONS, 2026-10-02): the sets that hold ("same" and "related",
    never a conflicting one) and the instructor's "none" pairs, each line with its question, version and item ID."""
    version_of = {q['question_id']: q['exam_version'] for q in questions}
    line = lambda qid, iid: {'question_id': qid, 'version': version_of[qid], 'item_id': iid}
    links = [{'kind': s['kind'], 'origin': s['origin'], **({'note': s['note']} if s.get('note') else {}),
              'lines': sorted((line(l['question_id'], l['item_id']) for l in s['lines']), key=lambda row: row['version'])}
             for s in sets if s['kind'] in LINK_KINDS and not s.get('conflict')]
    links += [{'kind': 'none', 'origin': 'instructor', 'lines': sorted([line(*a), line(*b)], key=lambda row: row['version'])}
              for (a, b), kind in sorted(instructor.items()) if kind == 'none' and a[0] in version_of and b[0] in version_of]
    return links


def resolves_input(result, questions, window, concerns, check_id, links=()):
    """The `resolves` part of a resolution's input (`RESOLVE`): where the reviewer starts (`window`: the clarifications and exclusions
    as held, each question once, with the model's explanations of the check), the concerns as `answered_concerns` lists them, the
    problems they name, what earlier rounds resolved and the check's layout. One builder for the plan's own Resolve concerns
    (`resolve_rubric_concerns`) and a quiz draft's (`quiz_drafts`, 2026-10-11), so both ask exactly the same thing."""
    shown = {q['question_id']: q for q in result['questions']}
    # Where the reviewer starts: the window as it stands, each question once, with the model's explanations of the check.
    base = [{**row, 'reasons': shown.get(row['question_id'], {}).get('reasons', []), 'summary': shown.get(row['question_id'], {}).get('summary', '')}
            for row in window]
    named = {qid for concern in concerns for qid in concern['question_ids']}
    families = [family for family in result.get('families') or [[q['question_id']] for q in questions] if named & set(family)]
    return {'rule': RESOLVE, 'check_id': check_id, 'base': base, 'concerns': concerns, 'families': families,
            'earlier_resolved': (result.get('resolution') or {}).get('resolved', []),
            'layout': {k: result[k] for k in ('mode', 'families', 'consistency_issues', 'line_links') if k in result},
            **({'links': links} if links else {})}


def resolve_payload(context, family, counts, current, concerns, elsewhere=()):
    """What the reviewer reads to resolve one problem's concerns: the problem as the check read it, each version's clarifications
    and exclusions as the instructor has them now, the concerns that name it (by their number in the list), each with the
    instructor's comment on it where there is one, and the instructor's comments on other problems' concerns (v3)."""
    payload = problem_payload(context, family, counts)
    names = {q['question_id']: q['exam_version'] for q in family}
    for member, q in zip(payload['problem']['versions'], family):
        member['current'] = guidance_lines(q, current[q['question_id']])
    return {**payload, 'concerns': [
        {'concern': n, 'kind': c['kind'], 'versions': [names[qid] for qid in c['question_ids'] if qid in names],
         **({'item_ids': c['item_ids']} if c.get('item_ids') else {}), 'message': c['message'], **decision_fields(c),
         **({'version_note': c['version_note']} if c.get('version_note') else {}), **({'shared': c['shared']} if c.get('shared') else {}),
         **({'earlier_note': c['note']} if c.get('note') else {}), **({'instructor_response': comment} if comment else {}),
         **({'handled_by_faculty': c['fix'].get('note') or c['fix']['kind']} if c.get('fix') else {})} for n, c, comment in concerns],
        **({'instructor_feedback_elsewhere': list(elsewhere)} if elsewhere else {})}


def validate_resolution(value, family, numbers):
    """One problem's resolution, or ValueError: its versions as `validate_problem` gives them (new findings included), and one
    outcome for each of its concerns, by number."""
    found, consistency, summary = validate_problem(value, family)
    outcomes = {}
    for row in items(value.get('concerns') if isinstance(value, dict) else None, 'Concern outcomes', len(numbers)):
        if (not isinstance(row, dict) or type(row.get('concern')) is not int or row['concern'] not in numbers or row['concern'] in outcomes
                or type(row.get('resolved')) is not bool):
            raise ValueError('Answer every concern of the problem exactly once, by its number.')
        names = {q['exam_version'] for q in family}
        skipped = [name for name in dict.fromkeys(items(row.get('not_applicable', []), 'Versions not applicable', len(family))) if name in names]
        presented = {**decision_fields(row), **edit_fields(row, {q['exam_version']: q for q in family})} if any(
            key in row for key in ('title', 'decision', 'group', 'choices', 'topic', 'edit')) else None
        outcomes[row['concern']] = {'resolved': row['resolved'], 'how': text(row.get('how'), 'How the concern was handled', 4000, False),
            **({'not_applicable': skipped} if skipped else {}), **({'presentation': presented} if presented is not None else {})}
    if set(outcomes) != set(numbers):
        raise ValueError('The reviewer left a concern unanswered.')
    return found, consistency, summary, outcomes


def policy_question(policy, qid, rubric_id):
    return next((q for q in (policy or {}).get('questions', []) if q['question_id'] == qid and q['rubric_id'] == rubric_id), None)


# ONE QUESTION'S RUBRIC HOLDS BACK ONLY ITS OWN GRADES (requirement, 2026-09-25: editing one of Test 1's sixteen rubrics took all 2,359
# automatic grades out of the export). The finalization is still one confirmation that gates every new batch, but what it decided is
# judged question by question: a question STANDS while its input (its entry of `rubric_quality_context`) is what the plan's latest
# confirmation covered, and a grade stays final while the confirmation its graders read gave its question the same GUIDANCE.

def question_input(question):
    """One question's part of what a confirmation covers: its entry of `rubric_quality_context` (rubric revision, rules, built-in
    exclusions, comments, title, number, points, version, document), without what a check adds beside it (reference, origins)."""
    return digest({k: v for k, v in question.items() if k not in ('reference', 'origins')})


def question_inputs(context):
    return {q['question_id']: question_input(q) for q in context['questions']}


# What a check's input adds beside the context a confirmation covers (`rubric_check_input`): never part of its fingerprint.
_BESIDE = ('scheme', 'known_links', 'settled', 'standing_rules', 'assignment_choices', 'earlier_answers')


def read_inputs(payload, checksum, beside):
    """A check input's per-question input digests, or None where, without what it adds (`beside`, references and origins), it does
    not reproduce its fingerprint `checksum` exactly: nothing is guessed."""
    payload = {k: v for k, v in payload.items() if k not in beside}
    payload['questions'] = [{k: v for k, v in q.items() if k not in ('reference', 'origins')} for q in payload.get('questions', [])]
    return question_inputs(payload) if digest(payload) == checksum else None


def guidance_key(policy, entry):
    """What graders and the flags read of one question's confirmed entry (`grader_rubric`, `exclusive_pairs`, the full-credit and
    part rules, `quality_flags`): the rules label, the rubric revision, the exclusive pairs, the built-in exclusions and the
    clarifications. None when the confirmation has no entry for it. Order does not matter: pairs and built-in rules are sorted."""
    if not entry:
        return None
    return digest({'rules': (policy or {}).get('rules'), 'rubric_id': entry['rubric_id'],
                   'pairs': sorted(sorted(pair) for pair in entry.get('pairs', [])),
                   'builtin': sorted((rule['item_id'], rule['rule']) for rule in entry.get('builtin', [])),
                   'clarifications': {k: v for k, v in (entry.get('clarifications') or {}).items() if v}})


_REVIEWS = {}  # (catalog, review id) -> {'policy', 'inputs'}: confirmations and checks are immutable, so this never goes stale
_REVIEWS_LIMIT = 128
_REVIEWS_LOCK = threading.Lock()
# (catalog, review id, question id, rubric id) -> `guidance_key` of that confirmation's entry: derived from the same immutable rows, so
# it never goes stale either (PERFORMANCE, 2026-11-01: 10,478 calls and ~200 ms of the Assignments list, one per automatic grade)
_GUIDANCE = OrderedDict()
_GUIDANCE_LIMIT = 4096


def quality_flags(status, qid, rubric_id, applications):
    """Pure quality evidence over a finished selection. Never alters selections or scores. A status without `standing` has no
    question standing."""
    reasons, conflicts = [], []
    standing = (status.get('standing') or {}).get(qid)
    open_question = next((u for u in status.get('unresolved', []) if u['question_id'] == qid), None) or (standing or {}).get('open')
    if open_question and open_question.get('undecided'):
        reasons.append(f"Rubric quality check: {overflow_text(open_question)}. Decide in the rubric finalization before trusting this proposal.")
    elif open_question:
        reasons.append(f"Rubric quality check: {', '.join(open_question['lines'])} can still be selected together for {open_question['points']:g} of "
                       f"{open_question['maximum']:g} points. Decide in the rubric finalization which of them are alternatives before trusting this proposal.")
    elif not standing:
        reasons.append("Rubric quality check: finish the rubric finalization for this question's current rubric before trusting this proposal.")
    elif standing['rubric_id'] != rubric_id:
        reasons.append('Rubric quality check: this rubric revision has not been checked.')
    else:
        entry = standing['entry']
        selected = {a['rubric_item_id'] for a in applications if a['applied']}
        ruled = {rule['item_id']: rule['rule'] for rule in entry.get('builtin', [])}
        for item in sorted(ruled.keys() & selected):
            for other in sorted(selected - {item}):
                if [other, item] not in conflicts and [item, other] not in conflicts:
                    conflicts.append([item, other])
                    reasons.append(f'Rubric quality check: {item} is {RULES[ruled[item]]} of this question and cannot be combined with {other}. Review this grade manually.')
        for a, b in entry['pairs']:
            if {a, b} <= selected and [a, b] not in conflicts:
                conflicts.append([a, b])
                reasons.append(f'Rubric quality check: {a} and {b} were both selected but are confirmed as mutually exclusive. Review this grade manually.')
    current = standing and standing['rubric_id'] == rubric_id
    return {'enabled': True, 'review_id': standing['review_id'] if standing else None, 'guidance_sha256': standing['guidance_sha256'] if current else None,
            'pairs': conflicts, 'reasons': reasons}


def apply_quality(result, status, qid, rubric_id):
    """Project current policy over a proposal, preserving its immutable receipt."""
    result = copy.deepcopy(result)
    old = result['signals'].get('rubric_quality', {})
    base = old.get('base_state', result['state'])
    flags = quality_flags(status, qid, rubric_id, result['applications'])
    result['reasons'] = [r for r in result['reasons'] if r not in old.get('reasons', [])] + flags['reasons']
    result['state'] = ('both' if base in ('ocr_review', 'both') else 'grading_review') if flags['reasons'] else base
    result['signals']['rubric_quality'] = {**flags, 'base_state': base}
    return result


class CheckHost:
    """WHERE A CHECK RUNS (requirement, 2026-10-11): who owns the reviewer's requests, where each
    printed problem is read and what the reviewer may call while it checks. The prompts, payloads, validation, schemes and links
    are the check's own and the same for every host. This one is the TERM plan's check, exactly as it always ran: each call owned
    by the check, the problems cropped from the confirmed blank templates (`rubric_problem_images`), the reviewer's tools with a
    closer look at those templates (`rubric_check_tools`). A quiz draft's revision runs the same check before there is a plan
    (`quiz_drafts.DraftCheckHost`): `cloud_call` sends a revision's request only while that revision is running."""

    def __init__(self, grading):
        self.grading = grading

    def owner(self, task, step, attempt):
        return {'rubric_check_id': task['id'], 'step': step, 'task_attempt': attempt}

    def images(self, question):
        return self.grading.rubric_problem_images(question)

    def tools(self, task, endpoint, questions):
        return self.grading.rubric_check_tools(task, endpoint, questions)


class RubricQualityMixin:
    @staticmethod
    def rebuilt_documents(db, aid):
        """The printed versions a quiz rebuild replaced in this plan (QUIZZES "REBUILDING AN APPROVED QUIZ"): their questions came off the
        plan and their print jobs were superseded, so they are no longer this term's paper and take no question again."""
        return {question['assignment_version_id'] for (detail,) in db.execute(
                    "SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='quiz_rebuild_questions_withdrawn'", (aid,))
                for question in json.loads(detail).get('questions') or []}

    @staticmethod
    def rubric_upload_documents(db, plan):
        """Term projection: printed versions and explicit question pins, not another term's unused PDFs, nor a version a quiz rebuild
        replaced, nor the DR center's copy of a version (DR COPIES: its papers are that version's, so it takes no rubrics of its own)."""
        documents = [dict(r) for r in db.execute("""SELECT id,label FROM assignment_versions WHERE assignment_id=?
            AND json_extract(compiler_json,'$.dr_copy.version_id') IS NULL ORDER BY created_at,id""", (plan['source_assignment_id'],))]
        used = {r[0] for r in db.execute('SELECT b.assignment_version_id FROM exam_event_batches e JOIN print_batches b ON b.id=e.batch_id WHERE e.event_id=?', (plan['event_id'],))}
        if used:
            pinned = {r[0] for r in db.execute('SELECT q.assignment_version_id FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=?', (plan['id'],))}
            used = (used - RubricQualityMixin.rebuilt_documents(db, plan['id'])) | pinned
            documents = [d for d in documents if d['id'] in used]
        return documents

    def rubric_quality_context(self, db, aid):
        plan = self.plan(db, aid)
        documents = sorted(self.rubric_upload_documents(db, plan), key=lambda d: d['id'])
        letters = sorted(set(self.plan_versions(db, aid)) | set(self.version_letters(db, plan)))
        questions = []
        missing = []
        comments = self.line_comments(db, aid)
        for q in db.execute('''SELECT p.question_id,p.rubric_id,q.exam_version,q.assignment_version_id,r.content_json,b.rules_json
                FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
                JOIN frq_question_revisions r ON r.id=p.question_revision_id LEFT JOIN frq_rubric_revisions b ON b.id=p.rubric_id
                WHERE p.assignment_id=? ORDER BY p.question_id''', (aid,)):
            content = json.loads(q['content_json'])
            if not q['rubric_id']:
                missing.append(content['title'])
                continue
            rubric = json.loads(q['rules_json'])
            # Named only where the instructor wrote some, so a plan without comments keeps the fingerprint it always had.
            noted = {iid: note for iid, note in comments.get(q['question_id'], {}).items() if any(i['id'] == iid for i in rubric['items'])}
            questions.append({'question_id': q['question_id'], 'rubric_id': q['rubric_id'], 'exam_version': q['exam_version'],
                'document_id': q['assignment_version_id'], 'title': content['title'], 'printed_number': content.get('printed_number', ''),
                'max_points': content['max_points'], 'rubric': rubric, 'builtin': builtin_exclusions(rubric, content['max_points']),
                **({'comments': dict(sorted(noted.items()))} if noted else {})})
        if letters:
            missing.extend('Version ' + letter for letter in letters if not any(q['exam_version'] == letter for q in questions))
            if any(not q['exam_version'] for q in questions):
                missing.append('Assign the unversioned questions to their exam version')
        else:
            missing.extend(d['label'] for d in documents if not any(q['document_id'] == d['id'] for q in questions))
        context = {'rules': BUILTIN, 'documents': documents, 'versions': letters, 'questions': questions}
        return context, list(dict.fromkeys(missing))

    @staticmethod
    def line_comments(db, aid, qid=None):
        """{question_id: {item_id: comment}}: each line's latest comment (requirement, 2026-09-24). An empty comment clears it."""
        result = {}
        for question, item, comment in db.execute('''SELECT c.question_id,c.item_id,c.comment FROM frq_rubric_line_comments c
                WHERE c.assignment_id=? AND (? IS NULL OR c.question_id=?) AND c.rowid=(SELECT MAX(n.rowid) FROM frq_rubric_line_comments n
                WHERE n.assignment_id=c.assignment_id AND n.question_id=c.question_id AND n.item_id=c.item_id)''', (aid, qid, qid)):
            if comment.strip():
                result.setdefault(question, {})[item] = comment
        return result

    @staticmethod
    def review_readiness(db, aid, context, standing):
        """Per question: whether the instructor confirmed its exact rubric revision and comments for the reviewer (2026-09-24).
        A question whose confirmed finalization STANDS counts as confirmed: what a confirmation covered includes the rubric and its
        comments, so the reviewer and the instructor have already been through exactly this (a finalization from before this
        confirmation existed, or one carried to a line corrected in step three). One question's edit no longer un-readies the others."""
        result = {}
        for q in context['questions']:
            comments = q.get('comments') or {}
            checksum = digest(comments)
            row = db.execute('SELECT * FROM frq_rubric_review_ready WHERE assignment_id=? AND question_id=? ORDER BY rowid DESC LIMIT 1', (aid, q['question_id'])).fetchone()
            explicit = bool(row and row['rubric_id'] == q['rubric_id'] and row['comments_sha256'] == checksum)
            finalized = (standing.get(q['question_id']) or {}).get('rubric_id') == q['rubric_id']
            result[q['question_id']] = {'rubric_id': q['rubric_id'], 'comments': comments, 'comments_sha256': checksum, 'ready': explicit or finalized,
                                        'confirmed_at': row['created_at'] if explicit else None, 'by_finalization': finalized and not explicit}
        return result

    def confirm_rubric_review(self, aid, data, *, actor='workspace'):
        """The instructor confirms questions' exact rubrics and comments for the reviewer: {questions: {qid: {rubric_id, comments_sha256}}},
        as the page showed them. Anything changed since is refused, so a confirmation always names what the instructor saw. `actor`
        says which act of theirs it was: the page's own button, or `draft-acceptance` when accepting a model's draft is that
        confirmation for the questions it creates (requirement, 2026-10-11; `rubric_generation._after_acceptance`)."""
        asked = data.get('questions')
        if not isinstance(asked, dict) or not asked or len(asked) > 100:
            raise ValueError('Choose the questions to confirm for review.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            status = self.rubric_quality_status(db, aid)
            confirmed = []
            for qid, seen in asked.items():
                current = status['readiness'].get(qid)
                if not current:
                    raise ValueError('Confirm questions of this assignment that have a rubric.')
                if not isinstance(seen, dict) or seen.get('rubric_id') != current['rubric_id'] or seen.get('comments_sha256') != current['comments_sha256']:
                    raise Conflict('A rubric or its comments changed. Refresh and confirm what is current.')
                db.execute('INSERT INTO frq_rubric_review_ready VALUES(?,?,?,?,?,?,?)', (uid(), aid, qid, current['rubric_id'], current['comments_sha256'], actor, timestamp()))
                confirmed.append(qid)
            # Like the finalization's own confirmation, this leaves the plan's version alone: it has its own fingerprint.
            self.event(db, aid, 'rubric_review_confirmed', {'questions': [{'question_id': q, 'rubric_id': status['readiness'][q]['rubric_id'],
                                                                           'comments': len(status['readiness'][q]['comments'])} for q in confirmed]})
        return self.assignment(aid)

    def rubric_reference(self, db, aid):
        """What the reviewer reads beside the rubrics: each problem, its solution and its answer key. It is NOT part of what a
        confirmation covers, so correcting a key never sends the instructor back through the finalization."""
        reference = {}
        for q in db.execute('SELECT p.question_id,r.content_json FROM frq_assignment_questions p JOIN frq_question_revisions r ON r.id=p.question_revision_id WHERE p.assignment_id=?', (aid,)):
            content = json.loads(q['content_json'])
            areas = [content['problem_region']] if content.get('problem_region') else content.get('regions', [])
            reference[q['question_id']] = {'problem': content.get('problem', ''), 'expected_answer': content.get('expected_answer', ''),
                'worked_solution': content.get('worked_solution', ''),
                'answer_key': [{'label': part.get('label', ''), 'accept': part.get('accept', [])} for part in content.get('answer_key', [])],
                # Where the problem is PRINTED on the confirmed blank exam. Imported questions carry no statement, and a scanned
                # blank may never have been read: a reviewer that sees images reads the problem there instead.
                'printed': [{'template_id': content['template_id'], 'page': area['page'], 'region': area['region']} for area in areas
                            if content.get('template_id') and isinstance(area, dict) and area.get('page') and area.get('region')][:4]}
        return reference

    def rubric_quality_status(self, db, aid, *, details=False):
        self.plan(db, aid)
        opt = db.execute('SELECT * FROM frq_rubric_quality_options WHERE assignment_id=?', (aid,)).fetchone()
        # Mandatory since 2026-09-20 (instructor): the saved option no longer decides anything and stays as history.
        result = {'enabled': True, 'version': opt['version'] if opt else 0, 'state': 'needed', 'review': None, 'check': None}
        context, missing = self.rubric_quality_context(db, aid)
        checksum = digest(context)
        result.update(input_sha256=checksum, missing=missing)
        if details:
            result['context'] = context
        if opt and opt['review_id']:
            row = db.execute('SELECT * FROM frq_rubric_quality_reviews WHERE id=? AND input_sha256=?', (opt['review_id'], checksum)).fetchone()
            if row:
                result['review'] = {'id': row['id'], 'check_id': row['check_id'], 'policy': json.loads(row['policy_json'])}
        latest = "SELECT *,json_extract(input_json,'$.resolves.check_id') resolves FROM frq_rubric_quality_checks WHERE {} ORDER BY rowid DESC LIMIT 1"
        # The plan's newest check by its rowid from the index, so the large input of every other check is never read or sorted.
        row = db.execute("""SELECT *,json_extract(input_json,'$.resolves.check_id') resolves FROM frq_rubric_quality_checks
            WHERE rowid=(SELECT MAX(rowid) FROM frq_rubric_quality_checks WHERE assignment_id=?)""", (aid,)).fetchone()
        if row and row['resolves'] and (row['cancelled'] or row['status'] == 'failed'):
            # A resolution that failed or was cancelled leaves the review it answered as it was, with the response kept to try again.
            resolves = json.loads(db.execute("SELECT json_extract(input_json,'$.resolves') FROM frq_rubric_quality_checks WHERE id=?", (row['id'],)).fetchone()[0])
            result['resolution_failed'] = {'check_id': row['id'], 'cancelled': bool(row['cancelled']), 'error': row['error'],
                'responses': [{'concern': n, 'response': comment} for n, _, comment in commented(resolves)]}
            row = db.execute(latest.format('id=?'), (row['resolves'],)).fetchone() or row
        elif row and (row['cancelled'] or row['status'] == 'failed'):
            # A RE-RUN THAT WAS CANCELLED OR FAILED (requirement, 2026-10-05: cancelling Quiz 4's second check hid the finished one of the
            # same rubrics) leaves the newest finished check of exactly those rubrics as it was, to review and confirm.
            earlier = db.execute(latest.format("assignment_id=? AND input_sha256=? AND rowid<(SELECT rowid FROM frq_rubric_quality_checks WHERE id=?) AND status='complete' AND cancelled=0"),
                                 (aid, row['input_sha256'], row['id'])).fetchone()
            if earlier:
                result['rerun_failed'] = {'check_id': row['id'], 'cancelled': bool(row['cancelled']), 'error': row['error']}
                row = earlier
        if row:
            result['check'] = {k: row[k] for k in ('id', 'status', 'error', 'input_sha256', 'updated_at')}
            if row['resolves']:
                result['check']['resolves'] = row['resolves']
            if row['cancelled']:
                result['check']['status'] = 'cancelled'
            result['check']['model'] = json.loads(row['profile_json'])['endpoint']['name']
            result['check']['current'] = row['input_sha256'] == checksum
            if row['status'] in ('pending', 'running'):
                # Questions are asked side by side; each settled answer is a completed call of this check. A resolution asks problems.
                asked = json.loads(row['input_json'])
                asked = len(asked['resolves']['families']) if 'resolves' in asked else len(asked['questions']) - len(settled_of(asked))
                answered = db.execute("SELECT COUNT(DISTINCT step) FROM frq_cloud_calls WHERE rubric_check_id=? AND state='completed'", (row['id'],)).fetchone()[0]
                result['check']['progress'] = {'completed': min(answered, asked), 'total': asked}
            if details:
                result['check']['result'] = json.loads(row['result_json']) if row['result_json'] else None
                # BOOKKEEPING IS FIXED, NOT ASKED (2026-10-24): named only for a check with fixes, so every other answer is as it was.
                fixes = self.fix_states(db, aid, row['id'], result['check']['result']) if result['check']['result'] else {}
                if fixes:
                    result['check']['fixes'] = fixes
        # A confirmation made before 2026-09-21 may still allow more points than a question is worth: it then needs the
        # instructor again for those questions, and only those are flagged meanwhile.
        result['unresolved'] = unresolved_questions(result['review']['policy'], context) if result['review'] else []
        result['standing'] = self.finalization_standing(db, aid, context, result['review'], result['unresolved'])
        result['held'] = [q['title'] for q in context['questions'] if q['question_id'] not in result['standing']]
        # What a check would ask, named only when it would not ask every question (A CHECK ASKS ONLY WHAT DOES NOT STAND), and the
        # questions it would carry from the latest finished check (A CHECK ASKS ONLY WHAT CHANGED).
        # Only the latest finished check, and only once it is no longer current: while it is, it holds everything.
        carried = self.carried_entries(db, context, row['id'], result['standing']) if (
            row and row['status'] == 'complete' and not row['cancelled'] and row['result_json'] and row['input_sha256'] != checksum) else {}
        settled = settled_entries(context, {**carried, **result['standing']})
        if settled:
            result['to_check'] = [q['question_id'] for q in context['questions'] if q['question_id'] not in settled]
            if carried:
                result['carried'] = {'check_id': row['id'], 'questions': sorted(qid for qid in settled if qid in carried)}
        result['readiness'] = self.review_readiness(db, aid, context, result['standing'])
        result['unconfirmed'] = [q['title'] for q in context['questions'] if not result['readiness'][q['question_id']]['ready']]
        result['state'] = ('needs_review' if result['unresolved'] else 'confirmed') if result['review'] else 'missing' if missing or not context['questions'] else 'needed'
        if result['state'] == 'needed' and result['check'] and result['check']['current']:
            result['state'] = 'needed' if row['cancelled'] else 'needs_review' if row['status'] == 'complete' else row['status']
        return result

    def confirmation_record(self, db, review_id):
        """{'policy', 'inputs'} of a retained confirmation, or None. `inputs` are the per-question input digests it covered: recorded
        with it since 2026-09-25, else read from its check's input, but ONLY where that input reproduces the confirmation's own
        fingerprint exactly (nothing is guessed: a confirmation that does not reproduce, such as an older carried one, gives None)."""
        key = (str(self.path), review_id)
        with _REVIEWS_LOCK:
            cached = _REVIEWS.get(key)
        if cached is not None:
            return cached
        row = db.execute('''SELECT r.input_sha256,r.policy_json,c.input_json FROM frq_rubric_quality_reviews r
            LEFT JOIN frq_rubric_quality_checks c ON c.id=r.check_id WHERE r.id=?''', (review_id,)).fetchone() if review_id else None
        if not row:
            return None
        policy = json.loads(row['policy_json'])
        inputs = policy.get('inputs') if isinstance(policy.get('inputs'), dict) else None
        if inputs is None and row['input_json']:
            inputs = read_inputs(json.loads(row['input_json']), row['input_sha256'], _BESIDE)
        record = {'policy': policy, 'inputs': inputs}
        with _REVIEWS_LOCK:
            _REVIEWS[key] = record
            while len(_REVIEWS) > _REVIEWS_LIMIT:
                _REVIEWS.pop(next(iter(_REVIEWS)))
        return record

    def finished_check(self, db, check_id):
        """What a completed check read and answered, for carrying (A CHECK ASKS ONLY WHAT CHANGED): the per-question input digests it
        read, ONLY where its input reproduces its own fingerprint exactly (else None), and its entries, open questions, problems,
        concerns. A completed check never changes (migrations 011 and 012), so this is cached like a confirmation."""
        key = (str(self.path), 'check', check_id)
        with _REVIEWS_LOCK:
            cached = _REVIEWS.get(key)
        if cached is not None:
            return cached
        row = db.execute('SELECT input_sha256,input_json,result_json FROM frq_rubric_quality_checks WHERE id=?', (check_id,)).fetchone()
        payload, result = json.loads(row['input_json']), json.loads(row['result_json'])
        record = {'rules': payload.get('rules'), 'inputs': read_inputs(payload, row['input_sha256'], _BESIDE + ('resolves',)),
                  'entries': {entry['question_id']: entry for entry in result.get('questions') or []},
                  'unresolved': sorted({row['question_id'] for row in result.get('unresolved') or []}),
                  'families': result.get('families') or [], 'suspected_errors': result.get('suspected_errors') or [],
                  'consistency_issues': result.get('consistency_issues') or []}
        with _REVIEWS_LOCK:
            _REVIEWS[key] = record
            while len(_REVIEWS) > _REVIEWS_LIMIT:
                _REVIEWS.pop(next(iter(_REVIEWS)))
        return record

    def carried_entries(self, db, context, check_id, standing):
        """{question_id: {rubric_id, entry, open}} of the questions the completed check `check_id` still holds: its input for each is
        what the question is now, it has an entry for the current rubric revision and left nothing open on it, and no confirmation
        stands for it."""
        record = self.finished_check(db, check_id)
        if not record['inputs'] or record['rules'] != BUILTIN:
            return {}
        found = {}
        for q in context['questions']:
            qid, entry = q['question_id'], record['entries'].get(q['question_id'])
            if (qid in standing or not entry or entry.get('rubric_id') != q['rubric_id'] or qid in record['unresolved']
                    or record['inputs'].get(qid) != question_input(q)):
                continue
            over = heaviest_combination(q['rubric'], entry, q['max_points'])
            found[qid] = {'rubric_id': q['rubric_id'], 'entry': entry, 'open': {'question_id': qid, **over} if over else None}
        return found

    def finalization_standing(self, db, aid, context, review, unresolved):
        """{question_id: {review_id, rubric_id, entry, guidance_sha256, open}} for every question whose finalization stands. While the
        plan's confirmation covers the whole plan, every question it has an entry for stands under it (exactly as before). Otherwise
        the plan's LATEST confirmation (only confirming or carrying writes one, and each points the plan at it) still stands for each
        question whose input is what it covered, under the rules it was confirmed with; the others wait for a new confirmation."""
        standing = {}

        def stand(review_id, policy, q, entry, over):
            standing[q['question_id']] = {'review_id': review_id, 'rubric_id': q['rubric_id'], 'entry': entry,
                                          'guidance_sha256': guidance_key(policy, entry), 'open': over}
        if review:
            opened = {u['question_id']: u for u in unresolved}
            for q in context['questions']:
                entry = policy_question(review['policy'], q['question_id'], q['rubric_id'])
                if entry:
                    stand(review['id'], review['policy'], q, entry, opened.get(q['question_id']))
            return standing
        latest = db.execute('SELECT id,input_sha256 FROM frq_rubric_quality_reviews WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1', (aid,)).fetchone()
        record = self.confirmation_record(db, latest['id']) if latest else None
        if not record or record['policy'].get('rules') != BUILTIN:
            return standing
        if latest['input_sha256'] == digest(context):
            # It covered exactly today's rubrics (a check was merely asked again, which unsets the plan's pointer): every question it
            # has an entry for stands, whether or not it recorded its inputs (a carried confirmation from before 2026-09-25 did not).
            for q in context['questions']:
                entry = policy_question(record['policy'], q['question_id'], q['rubric_id'])
                if entry:
                    over = heaviest_combination(q['rubric'], entry, q['max_points'])
                    stand(latest['id'], record['policy'], q, entry, {'question_id': q['question_id'], **over} if over else None)
            return standing
        if not record['inputs']:
            return standing
        for q in context['questions']:
            entry = policy_question(record['policy'], q['question_id'], q['rubric_id'])
            if entry and record['inputs'].get(q['question_id']) == question_input(q):
                over = heaviest_combination(q['rubric'], entry, q['max_points'])
                stand(latest['id'], record['policy'], q, entry, {'question_id': q['question_id'], **over} if over else None)
        return standing

    def same_finalization(self, db, graded_under, flags, qid, rubric_id):
        """Whether a grade made under the confirmation `graded_under` (its scope's `rubric_quality`) had the guidance for its question
        that stands now (`flags` from `quality_flags`). The same confirmation always does; an earlier one does only where it gave this
        question and rubric revision exactly the same guidance. No confirmation, or one without an entry, never does."""
        if not flags.get('enabled'):
            return True
        if graded_under and graded_under == flags.get('review_id'):
            return True
        if not graded_under or graded_under == 'unconfirmed' or not flags.get('guidance_sha256'):
            return False
        key = (str(self.path), graded_under, qid, rubric_id)
        with _REVIEWS_LOCK:
            found = _GUIDANCE.get(key, _GUIDANCE)
            if found is not _GUIDANCE:
                _GUIDANCE.move_to_end(key)
        if found is _GUIDANCE:
            record = self.confirmation_record(db, graded_under)
            if not record:
                return False
            found = guidance_key(record['policy'], policy_question(record['policy'], qid, rubric_id))
            with _REVIEWS_LOCK:
                _GUIDANCE[key] = found
                while len(_GUIDANCE) > _GUIDANCE_LIMIT:
                    _GUIDANCE.popitem(last=False)
        return found == flags['guidance_sha256']

    def rubric_guidance(self, db, review_id, qid, rubric_id):
        """One question's entry of a retained confirmation, or None for a batch from before finalizations."""
        row = db.execute('SELECT policy_json FROM frq_rubric_quality_reviews WHERE id=?', (review_id,)).fetchone() if review_id else None
        return policy_question(json.loads(row['policy_json']), qid, rubric_id) if row else None

    def rubric_quality(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            result = self.rubric_quality_status(db, aid, details=True)
        settings = self.settings()['settings']
        result['model'] = settings['rubric_reviewer'] or settings['tiers']['cloud_standard']['primary']
        return result

    def set_rubric_quality(self, aid, data):
        """The switch of the once optional check. An older page may still send it: on changes nothing, off is refused."""
        if data.get('enabled') is not True:
            raise Conflict('The rubric finalization is required before grading and cannot be turned off.')
        return self.rubric_quality(aid)

    def cancel_rubric_quality(self, aid, data):
        """Stop a waiting check. A call already submitted keeps its receipt; nothing further is sent for it."""
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            db.execute('BEGIN IMMEDIATE')
            stopped = db.execute("UPDATE frq_rubric_quality_checks SET status='failed',cancelled=1,updated_at=? WHERE assignment_id=? AND status IN ('pending','running')", (timestamp(), aid)).rowcount
            if stopped:
                self.event(db, aid, 'rubric_quality_cancelled', {})
        return self.rubric_quality(aid)

    def queue_rubric_quality(self, aid, data):
        request_id = text(data.get('request_id'), 'Request ID', 128, False)
        with self.rosters.lock(), connect(self.path) as db:
            status = self.rubric_quality_status(db, aid, details=True)
            if data.get('input_sha256') != status['input_sha256']:
                raise Conflict('Rubrics changed. Refresh before requesting their review.')
            if status['missing'] or not status['context']['questions']:
                raise Conflict('Upload rubrics for every version first. Missing: ' + ', '.join(status['missing']))
            if status['unconfirmed']:
                raise Conflict('Confirm each rubric for review in Rubrics & Templates first. Not confirmed: ' + ', '.join(status['unconfirmed']))
            if len(status['context']['questions']) > 100 or len(encoded(status['context']).encode()) > 2 * 1024**2:
                raise ValueError('This rubric set is too large for one review (100 questions / 2 MiB maximum).')
            name, settings, endpoint = self.rubric_reviewer_profile(status['context'])
            checksum = digest([aid, status['input_sha256'], endpoint])
            prior = db.execute('SELECT * FROM frq_rubric_quality_checks WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another rubric review.')
                return self.rubric_quality(aid)
            busy = db.execute("SELECT 1 FROM frq_rubric_quality_checks WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone()
            if busy:
                raise Conflict('A rubric review is already queued or running for this assignment.')
            if db.execute("SELECT COUNT(*) FROM frq_rubric_quality_checks WHERE status IN ('pending','running')").fetchone()[0] >= 20:
                raise Conflict('Too many rubric reviews are waiting.')
            now = timestamp()
            payload, settled = self.rubric_check_input(db, aid, status)
            db.execute('INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,\'pending\',?,?)',
                (uid(), aid, request_id, checksum, status['input_sha256'], encoded(payload), encoded({'endpoint': endpoint, 'settings': settings}), now, now))
            db.execute('INSERT INTO frq_rubric_quality_options VALUES(?,1,1,NULL,?) ON CONFLICT(assignment_id) DO UPDATE SET review_id=NULL,version=version+1,updated_at=excluded.updated_at', (aid, now))
            # Asking the reviewer again suspends nothing (2026-09-25): until a confirmation changes a question's guidance, the
            # confirmation that stands for it still does. Questions that do not stand are held by `quality_flags` meanwhile.
            self.event(db, aid, 'rubric_quality_queued', {'request_id': request_id, 'model': name, 'input_sha256': status['input_sha256'],
                **({'settled': len(settled)} if settled else {})})
        self.inbox.wake.set()
        return self.rubric_quality(aid)

    def rubric_check_input(self, db, aid, status):
        """(input, settled entries) of a check of the plan's rubrics as `status` holds them (`rubric_quality_status(details=True)`): the
        context a confirmation covers (its digest is `status['input_sha256']`) and, beside it, what a check adds: each question's
        reference and, with versions, the scheme, how each rubric was made and the known links; then the settled questions. One
        builder (2026-10-11) for a check queued here and for a check that already ran while its quiz was drafted
        (`record_drafted_check`), so the two are recorded with the very same input under the very same fingerprint."""
        versioned = len(status['context']['versions']) > 1
        reference = self.rubric_reference(db, aid)
        payload = {**status['context'], 'questions': [{**q, 'reference': reference.get(q['question_id'], {})} for q in status['context']['questions']]}
        if versioned:
            # How each rubric was made tells which questions are one problem; like the reference, it is no part of what a
            # confirmation covers.
            made = {qid: pair[0] for qid, pair in self.question_origins(db, aid).items()}
            payload = {**payload, 'scheme': VERSIONS_SCHEME, 'questions': [{**q, 'origins': made.get(q['question_id'], [])} for q in payload['questions']]}
            # LINKED VERSIONS (2026-10-02): the instructor's line links (fixed) and the others as candidates; like the origins,
            # no part of what a confirmation covers, and named only where there are some.
            from . import rubric_links
            state = self.line_link_state(db, aid, status['context'])
            links = rubric_links.known_links(state['sets'], state['instructor'], status['context']['questions'])
            if links:
                payload['known_links'] = links
        # RUBRIC MEMORIES (2026-10-14): the course's kept rules and watches as they stand now, like the links no part of what a
        # confirmation covers, and named only where the course has some; fitted to each question when the check runs (2026-10-28).
        from .rubric_memory import plan_rules
        rules = plan_rules(db, aid)
        if rules:
            payload['standing_rules'] = rules
        # NOTHING ASKED TWICE (2026-10-24): the instructor's quick answers for this assignment and what they answered to its earlier
        # rubric questions, like the memories no part of what a confirmation covers, and named only where there are some.
        from . import rubric_choices
        chosen = rubric_choices.stated(rubric_choices.of_plan(db, aid))
        if chosen:
            payload['assignment_choices'] = chosen
        answered = self.earlier_answers(db, aid)
        if answered:
            payload['earlier_answers'] = answered
        # A CHECK ASKS ONLY WHAT DOES NOT STAND (2026-10-05): the settled questions' confirmed entries, and the reviewer's links of
        # the latest finished check that lie wholly among them, kept for the problems that will not be asked again.
        carried = status.get('carried')
        rows = self.carried_entries(db, status['context'], carried['check_id'], status['standing']) if carried else {}
        settled = settled_entries(status['context'], {**rows, **status['standing']})
        if settled:
            earlier = db.execute("""SELECT json_extract(result_json,'$.line_links') FROM frq_rubric_quality_checks WHERE assignment_id=? AND status='complete'
                AND cancelled=0 AND json_extract(result_json,'$.families') IS NOT NULL ORDER BY rowid DESC LIMIT 1""", (aid,)).fetchone() if versioned else None
            kept = [link for link in json.loads(earlier[0] or '[]') if all(line['question_id'] in settled for line in link['lines'])] if earlier else []
            payload['settled'] = {'questions': settled, **({'links': kept} if kept else {})}
            held = sorted(qid for qid in settled if qid in rows)
            if held:
                # A CHECK ASKS ONLY WHAT CHANGED (2026-10-25): the concerns that check left open on the carried questions alone, outside
                # every problem asked again, stay listed as they were. A fix made there is done and not listed again; one skipped or
                # undone there is a decision, as in a resolution (`with_fix_states`).
                record = self.finished_check(db, carried['check_id'])
                asked = {q['question_id'] for q in status['context']['questions']} - set(settled)
                touched = asked | {qid for family in record['families'] if asked & set(family) for qid in family}
                alone = lambda ids: bool(ids) and not touched & set(ids) and all(qid in settled for qid in ids)
                plain = lambda issue: {k: v for k, v in issue.items() if k not in ('fix', 'new')}
                fixes = self.fix_states(db, aid, carried['check_id'], {'suspected_errors': record['suspected_errors']})
                errors = [plain(issue) for n, issue in enumerate(record['suspected_errors'])
                          if alone([issue.get('question_id')]) and (fixes.get(n) or {}).get('state') != 'applied']
                consistency = [plain(issue) for issue in record['consistency_issues'] if alone(issue.get('question_ids') or [])]
                payload['settled']['carried'] = {'check_id': carried['check_id'], 'questions': held,
                                                 **({'suspected_errors': errors} if errors else {}), **({'consistency_issues': consistency} if consistency else {})}
        return payload, settled

    def record_drafted_check(self, aid, request_id, seen, profile, result, metadata, detail):
        """A rubric check that already ran while a quiz was DRAFTED, recorded as the plan's completed check (requirement, 2026-10-11). The caller (`quiz_drafts._carry_drafted_check`) has proved, question
        by question, that the reviewer read exactly what a check queued here would send, and gives the result with the plan's own
        question and rubric IDs. Nothing is asked of a model: the row is INSERTED complete (migrations 011 and 012 guard updates and
        deletions of a check, never its insertion), with the input and the `input_sha256` `queue_rubric_quality` would record
        (`rubric_check_input`), so Key & Rubric Finalization shows it exactly as a check that ran here and a confirmation is recorded
        against the same fingerprint. Like a queued check it unsets the plan's pointer and suspends nothing, and it CONFIRMS nothing.

        Returns the check's ID, or None wherever a check queued now would not be this one: the rubrics are no longer what the caller
        compared (`seen`, the status's `input_sha256`), a rubric is missing or not confirmed for review, some questions' finalization
        stands (a check would ask only the others), another check is waiting, or `request_id` already names another request. The
        caller then queues the check as before. The same request again answers with the check it recorded."""
        request_id = text(request_id, 'Request ID', 128, False)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            status = self.rubric_quality_status(db, aid, details=True)
            checksum = digest([aid, status['input_sha256'], profile['endpoint'], detail])
            prior = db.execute('SELECT id,request_sha256,status,cancelled FROM frq_rubric_quality_checks WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                return prior['id'] if prior['request_sha256'] == checksum and prior['status'] == 'complete' and not prior['cancelled'] else None
            context = status['context']
            if (status['input_sha256'] != seen or status['missing'] or not context['questions'] or status['unconfirmed'] or len(context['questions']) > 100
                    or len(encoded(context).encode()) > 2 * 1024**2 or status.get('to_check')
                    or db.execute("SELECT 1 FROM frq_rubric_quality_checks WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone()):
                return None
            payload, _ = self.rubric_check_input(db, aid, status)
            now, cid = timestamp(), uid()
            db.execute("""INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,result_json,
                provider_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'complete',?,?,?,?)""",
                (cid, aid, request_id, checksum, status['input_sha256'], encoded(payload), encoded(profile), encoded(result), encoded(metadata), now, now))
            db.execute('INSERT INTO frq_rubric_quality_options VALUES(?,1,1,NULL,?) ON CONFLICT(assignment_id) DO UPDATE SET review_id=NULL,version=version+1,updated_at=excluded.updated_at', (aid, now))
            # One event, the one a check that finishes writes, naming where it ran: nothing was queued and nothing was sent.
            self.event(db, aid, 'rubric_quality_ready', {'check_id': cid, 'request_id': request_id, 'model': profile['endpoint']['name'],
                                                         'input_sha256': status['input_sha256'], **detail}, 'worker')
        if any((issue.get('fix') or {}).get('kind') == 'key' for issue in ((result or {}).get('suspected_errors') or [])
               + (((result or {}).get('resolution') or {}).get('resolved') or [])):
            self.apply_rubric_fixes(aid, cid)  # BOOKKEEPING IS FIXED, NOT ASKED: as for a check that ran here
        return cid

    def rubric_reviewer_profile(self, context):
        """The reviewer's name, the settings and its connection, sized for one problem of this plan."""
        settings = self.settings()['settings']
        name = settings['rubric_reviewer'] or settings['tiers']['cloud_standard']['primary']
        if not name:
            raise ValueError('Choose a Rubric drafting and review model in Compute settings first.')
        # With its reasoning choice, like any team role: without one a model that thinks was told not to, or given its least effort.
        # A plan with versions is checked one problem at a time with every version side by side (2026-09-25).
        versions = len(context['versions'])
        return name, settings, review_budget(config.rubric_reviewer(settings), context, versions if versions > 1 else 1)

    def resolve_rubric_concerns(self, aid, data):
        """Ask the reviewer again about the concerns a completed check listed that the instructor commented on, each with its
        comment (see `RESOLVE`). The window's clarifications and exclusions, unconfirmed edits included, are where it starts; only
        the problems those concerns name are asked. Like any check it approves nothing and replaces the review's suggestions only
        once it completes."""
        request_id = text(data.get('request_id'), 'Request ID', 128, False)
        responses = {}
        for row in items(data.get('responses'), 'Comments on the concerns', 200):
            if not isinstance(row, dict) or type(row.get('concern')) is not int or row['concern'] < 0 or row['concern'] in responses:
                raise ValueError('Comment on each concern once, by its number.')
            comment = text(row.get('response'), 'Your comment on a concern', 4000).strip()
            if comment:
                responses[row['concern']] = comment
        with self.rosters.lock(), connect(self.path) as db:
            status = self.rubric_quality_status(db, aid, details=True)
            window = normalize_questions(data.get('questions'), status['context'])
            # The request is what the instructor sent, so sending it again returns its resolution, finished or not.
            checksum = digest([aid, RESOLVE, data.get('check_id'), data.get('input_sha256'), sorted(responses.items()), window])
            prior = db.execute('SELECT * FROM frq_rubric_quality_checks WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['request_sha256'] != checksum:
                    raise Conflict('This request ID already belongs to another rubric review.')
                return self.rubric_quality(aid)
            check = status['check']
            if (not check or check['id'] != data.get('check_id') or check['status'] != 'complete' or not check['current']
                    or data.get('input_sha256') != status['input_sha256']):
                raise Conflict('Resolve the concerns of a completed check of the current rubric revisions. Refresh first.')
            concerns = with_fix_states(answered_concerns(check['result'], responses), check.get('fixes') or {})
            name, settings, endpoint = self.rubric_reviewer_profile(status['context'])
            payload = json.loads(db.execute('SELECT input_json FROM frq_rubric_quality_checks WHERE id=?', (check['id'],)).fetchone()[0])
            payload.pop('resolves', None)
            # RUBRIC MEMORIES: a resolution is a request of its own, queued now, so it reads the course's memories as they stand
            # now and not as the check it answers froze them (2026-11-09 review: a memory the instructor dropped after that
            # check, perhaps for the very concern being resolved, was sent to the reviewer again).
            from .rubric_memory import plan_rules
            payload.pop('standing_rules', None)
            rules = plan_rules(db, aid)
            if rules:
                payload['standing_rules'] = rules
            # LINKED VERSIONS (2026-10-02): the links as they stand now, the instructor's included, tell the reviewer where one
            # answer reaches unchanged ("same"), in part ("related") or not at all ("none").
            links = []
            if len(status['context']['versions']) > 1:
                state = self.line_link_state(db, aid, status['context'])
                links = resolve_links(state['sets'], state['instructor'], status['context']['questions'])
            payload['resolves'] = resolves_input(check['result'], status['context']['questions'], window, concerns, check['id'], links)
            families = payload['resolves']['families']
            if db.execute("SELECT 1 FROM frq_rubric_quality_checks WHERE assignment_id=? AND status IN ('pending','running')", (aid,)).fetchone():
                raise Conflict('A rubric review is already queued or running for this assignment.')
            if db.execute("SELECT COUNT(*) FROM frq_rubric_quality_checks WHERE status IN ('pending','running')").fetchone()[0] >= 20:
                raise Conflict('Too many rubric reviews are waiting.')
            now, rid = timestamp(), uid()
            db.execute('INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,\'pending\',?,?)',
                (rid, aid, request_id, checksum, status['input_sha256'], encoded(payload), encoded({'endpoint': endpoint, 'settings': settings}), now, now))
            db.execute('INSERT INTO frq_rubric_quality_options VALUES(?,1,1,NULL,?) ON CONFLICT(assignment_id) DO UPDATE SET review_id=NULL,version=version+1,updated_at=excluded.updated_at', (aid, now))
            self.event(db, aid, 'rubric_concerns_queued', {'request_id': request_id, 'check_id': rid, 'resolves': check['id'], 'model': name,
                'concerns': len(concerns), 'commented': len(responses), 'problems': len(families)})
        self.inbox.wake.set()
        return self.rubric_quality(aid)

    def rubric_problem_images(self, question):
        """The question as printed on its confirmed blank exam, cropped like a response. Course material: no student wrote on it.
        Never a reason to fail the check: a reviewer without pictures judges from the rubric and the key, and the result says so."""
        from . import extraction
        try:
            with CROPS:
                return [extraction.extract(self.templates.read(area['template_id'], 'image', area['page'])[0], area['region'], None, [])['images']['cloud']
                        for area in (question.get('reference') or {}).get('printed', [])]
        except Exception:  # A missing page, an unavailable sandbox: whatever it was, the check goes on without the pictures.
            return []

    def rubric_check_tools(self, task, endpoint, questions):
        """On a Claude or ChatGPT subscription connection, what the reviewer may call while it checks (requirement, 2026-10-01): the rubric writers' context (`drafting_tools.rubric_tools`), with a closer look at the blank
        pages these questions are printed on. None on any other connection."""
        from .providers import tools
        if not tools.offered(endpoint):
            return None
        pages = {}
        if endpoint['modality'] == 'vision':
            for question in questions:
                printed = (question.get('reference') or {}).get('printed') or []
                try:
                    if printed:
                        pages[question['exam_version']] = TemplatePages(self.templates, printed[0]['template_id'])
                except Exception:  # a template that cannot be read only leaves its pages out, as the pictures are left out
                    continue
        definition, assignment = self._course_of(task)
        return rubric_tools(self, endpoint, definition, pages, assignment_id=assignment, reviewer=True)

    def review_question(self, task, profile, context, question, host=None):
        """One question, alone in its request. A refused answer is asked once more; what was answered before is never sent again.
        `host` (`CheckHost`): where the check runs; the plan's own by default."""
        host = host or CheckHost(self)
        endpoint, settings = profile['endpoint'], profile['settings']
        images = host.images(question) if endpoint['modality'] == 'vision' else []
        if sum(map(len, images)) > 20 * 1024**2:
            images = []
        payload, refused, found = question_payload(context, question, len(images)), None, None
        tools = host.tools(task, endpoint, [question])
        for attempt in (0, 1):
            try:
                reply = self.cloud_call(host.owner(task, 'rubric_quality:' + question['question_id'], attempt), 'rubric_reviewer', endpoint,
                    {'prompt': 'rubric_quality', 'payload': payload, 'images': images, **({'reask': True} if attempt else {}),
                     **({'tools': tools} if tools else {})}, settings)
            except ProviderError as failure:
                if not found:
                    raise
                # The first answer was valid and only left the question open. What it leaves open is reported for the instructor,
                # never a failed check: the re-ask's failure (an attempt cap, a truncated or refused request) is kept on the receipt.
                found['receipt']['reask_error'] = str(failure)
                return found
            try:
                entry, errors = validate_question(reply['output'], question)
            except (ValueError, TypeError, KeyError) as failure:
                refused = failure
                continue
            found = {'entry': entry, 'errors': errors, 'images': len(images), 'receipt': {'metadata': reply['metadata'], 'raw': reply['raw']}}
            over = heaviest_combination(question['rubric'], entry, question['max_points'])
            if not over or over.get('undecided'):
                return found  # Resolved, or too entangled to name lines for the reviewer: either way the instructor sees what is open.
            # Its exclusions still allow more than the question is worth: it is told exactly which lines, once. What is
            # left after that is the instructor's to resolve, and the confirmation waits for it.
            payload = {**payload, 'unresolved': {**over, 'your_exclusions': entry['pairs']}}
        if found:
            return found
        raise ValueError(str(refused))

    def rubric_families(self, task, profile, context, host=None):
        """The plan's questions as problems, each with its copies on the other versions. How the rubrics were made settles it where
        every problem is known on every version; otherwise ONE text request groups them by what they ask (never by printed number),
        keeping those links. A grouping refused twice leaves the unlinked questions alone in their own requests, and says so."""
        host = host or CheckHost(self)
        questions, letters = context['questions'], sorted({q['exam_version'] for q in context['questions']})
        linked = linked_groups(questions)
        covered = {qid for group in linked for qid in group}
        known = {q['question_id']: q for q in questions}
        notes, receipt = [], None
        if all(len(group) == len(letters) for group in linked) and covered == set(known):
            groups = linked
        else:
            payload, refs = group_payload(questions, linked, {q['question_id']: q.get('reference', {}) for q in questions})
            groups, refused = None, None
            for attempt in (0, 1):
                reply = self.cloud_call(host.owner(task, 'rubric_quality:group', attempt), 'rubric_reviewer',
                    profile['endpoint'], {'prompt': 'rubric_quality_group', 'payload': payload, 'images': [], **({'reask': True} if attempt else {})}, profile['settings'])
                try:
                    groups, notes = question_groups(reply['output'], questions, linked, refs)
                    receipt = {'metadata': reply['metadata'], 'raw': reply['raw']}
                    break
                except (ValueError, TypeError, KeyError, AttributeError) as failure:
                    refused = failure
            if groups is None:
                groups = linked + [[qid] for qid in known if qid not in covered]
                notes = [f"The versions could not be grouped ({refused}); questions not known to be one problem were checked alone."]
        families = [sorted((known[qid] for qid in group), key=lambda q: q['exam_version']) for group in groups]
        return sorted(families, key=lambda family: (family[0]['exam_version'], printed_order(family[0]))), notes, receipt

    def review_problem(self, task, profile, context, family, host=None):
        """One problem with every version side by side in its request. A refused answer is asked once more; a problem whose
        exclusions still allow more than a version is worth is told exactly which lines, once, like a single question."""
        return self._ask_problem(task, profile, family, 'rubric_quality:problem:' + family[0]['question_id'], 'rubric_quality_versions',
            lambda counts: problem_payload(context, family, counts),
            lambda output: {**dict(zip(('answers', 'consistency', 'summary'), validate_problem(output, family))),
                            **dict(zip(('links', 'dropped_links'), parallel_links(output, family)))}, host)

    def review_added(self, task, profile, context, family, host=None):
        """One problem whose settled versions come along as the reference (A CHECK ASKS ONLY WHAT DOES NOT STAND): every version side
        by side as `review_problem` sends them, the settled ones with their confirmed guidance; only the others are answered for."""
        settled = {qid: entry for qid, entry in settled_of(context).items() if any(q['question_id'] == qid for q in family)}
        asked = [q for q in family if q['question_id'] not in settled]
        return self._ask_problem(task, profile, family, 'rubric_quality:problem:' + family[0]['question_id'], SETTLED_PROMPT,
            lambda counts: problem_payload(context, family, counts, settled),
            lambda output: {**dict(zip(('answers', 'consistency', 'summary'), validate_problem(output, family, asked))),
                            **dict(zip(('links', 'dropped_links'), parallel_links(output, family)))}, host)

    def resolve_problem(self, task, profile, context, family, concerns, host=None):
        """One problem's concerns, each with the instructor's comment where there is one, asked exactly as `review_problem` asks a
        problem. Under v3 the instructor's comments on other problems' concerns come along as their evident policy."""
        current, numbers = {row['question_id']: row for row in context['resolves']['base']}, [n for n, _, _ in concerns]
        ids, elsewhere = {q['question_id'] for q in family}, []
        if context['resolves'].get('rule') == 'resolve-concerns-v3':
            elsewhere = [{'message': c['message'], 'instructor_response': comment} for _, c, comment in to_resolve(context['resolves'])
                         if comment and not ids & set(c['question_ids'])]
        return self._ask_problem(task, profile, family, 'rubric_quality:resolve:' + family[0]['question_id'], 'rubric_quality_resolve',
            lambda counts: resolve_payload(context, family, counts, current, concerns, elsewhere),
            lambda output: dict(zip(('answers', 'consistency', 'summary', 'outcomes'), validate_resolution(output, family, numbers))), host)

    def _ask_problem(self, task, profile, family, step, prompt, build, validate, host=None):
        host = host or CheckHost(self)
        endpoint, settings = profile['endpoint'], profile['settings']
        pictures = [host.images(q) if endpoint['modality'] == 'vision' else [] for q in family]
        if sum(len(image) for images in pictures for image in images) > 20 * 1024**2:
            pictures = [[] for _ in family]
        counts = [len(images) for images in pictures]
        payload, refused, found = build(counts), None, None
        tools = host.tools(task, endpoint, family)
        for attempt in (0, 1):
            try:
                reply = self.cloud_call(host.owner(task, step, attempt), 'rubric_reviewer', endpoint,
                    {'prompt': prompt, 'payload': payload, 'images': [image for images in pictures for image in images],
                     **({'reask': True} if attempt else {}), **({'tools': tools} if tools else {})}, settings)
            except ProviderError as failure:
                if not found:
                    raise
                found['receipt']['reask_error'] = str(failure)
                return found
            try:
                answer = validate(reply['output'])
            except (ValueError, TypeError, KeyError) as failure:
                refused = failure
                continue
            answers = answer['answers']
            found = {**answer, 'images': dict(zip((q['question_id'] for q in family), counts)),
                     'receipt': {'metadata': reply['metadata'], 'raw': reply['raw']}}
            open_ = [{'version': q['exam_version'], **over, 'your_exclusions': answers[q['question_id']][0]['pairs']} for q in family if answers[q['question_id']][0]
                     and (over := heaviest_combination(q['rubric'], answers[q['question_id']][0], q['max_points'])) and not over.get('undecided')]
            if not open_:
                return found
            payload = {**payload, 'unresolved': open_}
        if found:
            return found
        raise ValueError(str(refused))

    def run_rubric_versions(self, task, profile, context, host=None):
        """A check of a plan with versions: each problem once, with all its versions (see `VERSIONS_SCHEME`). Where some questions are
        settled (A CHECK ASKS ONLY WHAT DOES NOT STAND), a problem with nothing else is not asked and one with both is asked about the
        others only; each settled question keeps its confirmed entry."""
        try:
            families, notes, grouping = self.rubric_families(task, profile, context, host)
        except RetryLater as waiting:
            return 'pending', None, None, '', later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError) as failure:
            return 'failed', None, None, f"The questions could not be grouped by problem: {failure}", ''
        label = lambda family: f"{family[0]['title']} (Version{'s' if len(family) > 1 else ''} {', '.join(q['exam_version'] for q in family)})"
        settled = settled_of(context)
        asked = [family for family in families if any(q['question_id'] not in settled for q in family)]

        def one(family):
            try:
                ask = self.review_added if any(q['question_id'] in settled for q in family) else self.review_problem
                return family, ask(task, profile, context, family, host), None, None
            except RetryLater as waiting:
                return family, None, waiting.seconds, None
            except (ProviderError, ValueError, TypeError, KeyError) as failure:
                return family, None, None, str(failure)
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(asked)))) as pool:
            outcomes = list(pool.map(one, asked))
        waits = [seconds for _, _, seconds, _ in outcomes if seconds is not None]
        failures = [f"{label(family)}: {failure}" for family, _, _, failure in outcomes if failure]
        if failures:
            return 'failed', None, None, f"{len(failures)} of {len(outcomes)} problems could not be checked. " + ' '.join(failures[:3]), ''
        if waits:
            return 'pending', None, None, '', later(min(waits))
        entries, errors, consistency = [], [], []
        for family, done, _, _ in outcomes:
            for q in family:
                entry, found = done['answers'][q['question_id']]
                if entry:  # A settled question's entry is its confirmed one (below).
                    entries.append(entry)
                errors.extend(found)
            consistency.extend(done['consistency'])
        proposed = entries
        entries = sorted(entries + list(settled.values()), key=lambda q: q['question_id'])
        still = unresolved_questions({'questions': entries}, context)
        pictures = sum(1 for _, done, _, _ in outcomes for count in done['images'].values() if count)
        together = sum(1 for family, _, _, _ in outcomes if len(family) > 1)
        reasked = sum(bool(done['receipt'].get('reask_error')) for _, done, _, _ in outcomes)
        links = [link for _, done, _, _ in outcomes for link in done.get('links', [])]
        # The reviewer's earlier links of a problem not asked again stay, so its lines keep their links.
        unasked = {q['question_id'] for family in families if family not in asked for q in family}
        links += [link for link in (context.get('settled') or {}).get('links', []) if all(line['question_id'] in unasked for line in link['lines'])]
        dropped = sum(done.get('dropped_links', 0) for _, done, _, _ in outcomes)
        others, (which, keeps) = len(settled), settled_words(context)
        kept = (context.get('settled') or {}).get('carried') or {}
        result = {'mode': 'per_problem', 'questions': entries, 'suspected_errors': kept.get('suspected_errors', []) + errors,
            'consistency_issues': kept.get('consistency_issues', []) + consistency, 'unresolved': still,
            'families': [[q['question_id'] for q in family] for family in families], 'grouping_notes': notes, 'line_links': links,
            **({'settled': sorted(settled)} if settled else {}), **({'carried': kept['questions']} if kept else {}),
            'summary': (f"Only the rubrics {which} were checked ({scope_text(context['questions'], {q['question_id'] for q in context['questions']} - set(settled))}): "
                        f"{len(outcomes)} problem{'s' if len(outcomes) != 1 else ''}, each with all its versions side by side in one request and its {'other' if kept else 'confirmed'} versions as the reference. "
                        f"The other {others} question{'s' if others != 1 else ''} keep{'' if others != 1 else 's'} {'their' if others != 1 else 'its'} {keeps} and "
                        f"{'were' if others != 1 else 'was'} not reviewed again. Each version has its own rubric, answer key and "
                        if settled else
                        f"{len(outcomes)} problem{'s' if len(outcomes) != 1 else ''} checked, "
                        + ("each with all its versions side by side in one request" if together == len(outcomes)
                           else f"{together} with all their versions side by side in one request and {len(outcomes) - together} alone")
                        + ", each version with its own rubric, answer key and ")
                + (f"printed problem ({pictures} read from the blank assignments)." if pictures else "no picture of its printed problem.")
                + f" {sum(len(q['clarifications']) for q in proposed)} clarifications, {sum(len(q['pairs']) for q in proposed)} exclusive pairs, {len(errors)} suspected errors"
                + f" and {len(consistency)} differences between versions are proposed."
                + (f" {dropped} set{'s' if dropped != 1 else ''} of parallel lines the reviewer named could not be used and {'were' if dropped != 1 else 'was'} left out." if dropped else "")
                + (f" {len(still)} question{'s' if len(still) > 1 else ''} still allow more points than they are worth: decide which of their lines are alternatives before you confirm." if still else "")
                + (f" For {reasked} problem{'s' if reasked > 1 else ''} the reviewer could not be asked again (see the receipt); its first answer stands." if reasked else "")
                + (' ' + ' '.join(notes) if notes else '')}
        metadata = {'questions': {q['question_id']: done['receipt'] for family, done, _, _ in outcomes for q in family},
                    'problem_images': {qid: count for _, done, _, _ in outcomes for qid, count in done['images'].items()},
                    'problem_summaries': {family[0]['question_id']: done['summary'] for family, done, _, _ in outcomes}, **({'grouping': grouping} if grouping else {})}
        return 'complete', result, metadata, '', ''

    def run_rubric_resolution(self, task, profile, context, host=None):
        """A resolution of the concerns a completed check listed (`RESOLVE`): each problem a commented concern names once, all its
        versions together. Every other question keeps what the window held. A commented concern stays listed unless every problem it
        names resolved it; a concern without a comment stays listed as it was."""
        resolves = context['resolves']
        by_id = {q['question_id']: q for q in context['questions']}
        families = [[by_id[qid] for qid in family] for family in resolves['families']]
        concerns, asked = list(enumerate(resolves['concerns'])), to_resolve(resolves)
        comments = {n: comment for n, _, comment in asked if comment}
        label = lambda family: f"{family[0]['title']}" + (f" (Version{'s' if len(family) > 1 else ''} {', '.join(q['exam_version'] for q in family)})" if family[0]['exam_version'] else '')

        def one(family):
            ids = {q['question_id'] for q in family}
            try:
                return family, self.resolve_problem(task, profile, context, family, [row for row in asked if ids & set(row[1]['question_ids'])], host), None, None
            except RetryLater as waiting:
                return family, None, waiting.seconds, None
            except (ProviderError, ValueError, TypeError, KeyError) as failure:
                return family, None, None, str(failure)
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(families)))) as pool:
            outcomes = list(pool.map(one, families))
        waits = [seconds for _, _, seconds, _ in outcomes if seconds is not None]
        failures = [f"{label(family)}: {failure}" for family, _, _, failure in outcomes if failure]
        if failures:
            return 'failed', None, None, f"{len(failures)} of {len(outcomes)} problems could not be resolved. " + ' '.join(failures[:3]), ''
        if waits:
            return 'pending', None, None, '', later(min(waits))
        base = {row['question_id']: row for row in resolves['base']}
        # A SETTLED VERSION STAYS SETTLED (requirement, 2026-10-15: finalizing makeup Version E of Quiz 2 rewrote Versions A-D's
        # clarifications and sent 612 of their graded answers back to review): where the check asked only some questions, a
        # resolution changes only those. A settled question keeps its confirmed entry whatever the reviewer answers for it, and a
        # concern that names only settled questions stays listed, unresolved, for the instructor to take up from those rubrics. A
        # question carried from an earlier check (A CHECK ASKS ONLY WHAT CHANGED) has no confirmation yet: it is resolved like any other.
        carried = set(((context.get('settled') or {}).get('carried') or {}).get('questions') or [])
        settled = {qid: entry for qid, entry in settled_of(context).items() if qid not in carried}
        for qid, entry in settled.items():
            if qid in base:
                base[qid] = {**base[qid], **{k: entry[k] for k in ('pairs', 'clarifications', 'builtin') if k in entry}}
        entries, before, answered, new_errors, new_consistency = dict(base), {}, {}, [], []
        for family, done, _, _ in outcomes:
            for q in family:
                entry, found = done['answers'][q['question_id']]
                if q['question_id'] not in settled:
                    entries[q['question_id']] = entry
                    before[q['question_id']] = {k: base[q['question_id']][k] for k in ('pairs', 'clarifications')}
                # A new finding shared across versions keeps one key of its own, apart from the concerns it was asked about.
                new_errors.extend({**issue, 'new': True, **({'shared': f"{task['id'][:8]}:{issue['shared']}"} if issue.get('shared') else {})} for issue in found)
            new_consistency.extend({**issue, 'new': True} for issue in done['consistency'])
            for n, outcome in done['outcomes'].items():
                answered.setdefault(n, []).append(outcome)
        resolved, open_errors, open_consistency = [], [], []
        for n, concern in concerns:
            shown = {k: v for k, v in concern.items() if k not in ('kind', 'note', 'new', 'response', 'comment', 'question_id' if concern['kind'] == 'consistency' else 'question_ids')}
            said, comment = answered.get(n, []), comments.get(n) or concern.get('comment', '')
            shown = {**shown, **({'comment': comment} if comment else {})}  # The instructor's latest comment on it stays beside it.
            presentations = [outcome['presentation'] for outcome in said if 'presentation' in outcome]
            if presentations:
                shown = {k: v for k, v in shown.items() if k not in ('title', 'decision', 'group', 'choices', 'topic', 'edit')}
                if all(value == presentations[0] for value in presentations):
                    shown.update(presentations[0])
            skipped = sorted({name for outcome in said for name in outcome.get('not_applicable', [])})
            shown = {**shown, **({'not_applicable': skipped} if skipped else {})}
            ids = concern.get('question_ids') or ([concern['question_id']] if concern.get('question_id') else [])
            if settled and ids and all(qid in settled for qid in ids):
                note = 'Kept as confirmed: this check asked only the questions without a standing finalization, so it changes none of these.'
                (open_errors if concern['kind'] == 'suspected_error' else open_consistency).append({**shown, 'note': note})
                continue
            if said and all(outcome['resolved'] for outcome in said):
                resolved.append({**shown, 'kind': concern['kind'], 'how': ' '.join(outcome['how'] for outcome in said),
                                 **({} if n in comments else {'without_comment': True})})  # Settled by the reviewer on its own (v3).
                continue
            note = ' '.join(outcome['how'] for outcome in said if not outcome['resolved']) or concern.get('note', '')
            (open_errors if concern['kind'] == 'suspected_error' else open_consistency).append({**shown, **({'note': note} if note else {})})
        entries = sorted(entries.values(), key=lambda q: q['question_id'])
        still = unresolved_questions({'questions': entries}, context)
        layout, errors, consistency = resolves['layout'], open_errors + new_errors, open_consistency + new_consistency
        new = len(new_errors) + len(new_consistency)
        reasked = sum(bool(done['receipt'].get('reask_error')) for _, done, _, _ in outcomes)
        result = {'mode': layout.get('mode', 'per_question'), 'questions': entries, 'suspected_errors': errors, 'unresolved': still,
            **({'consistency_issues': consistency} if 'consistency_issues' in layout or consistency else {}),
            **({'families': layout['families']} if 'families' in layout else {}),
            **({'line_links': layout['line_links']} if 'line_links' in layout else {}),
            'resolution': {'rule': resolves['rule'], 'check_id': resolves['check_id'], 'commented': sorted(comments),
                           'asked': [q['question_id'] for family in families for q in family], 'before': before,
                           'resolved': resolves['earlier_resolved'] + resolved},
            'summary': f"{len(asked)} concern{'s were' if len(asked) != 1 else ' was'} sent, one request per problem with all its versions "
                + f"({len(families)} problem{'s' if len(families) != 1 else ''}): {len(comments)} with your comment"
                + (f" and {len(asked) - len(comments)} for the reviewer to settle consistently with the problem and your comments" if len(asked) > len(comments) else "")
                + f". {len(resolved)} resolved, {len(asked) - len(resolved)} still open" + (f", {new} new" if new else "") + "."
                + (f" {len(concerns) - len(asked)} concern{'s' if len(concerns) - len(asked) != 1 else ''} without a comment "
                   + ("was" if len(concerns) - len(asked) == 1 else "were") + " left as listed." if len(concerns) > len(asked) else "")
                + (f" {len(still)} question{'s' if len(still) > 1 else ''} still allow more points than they are worth: decide which of their lines are alternatives before you confirm." if still else "")
                + (f" For {reasked} problem{'s' if reasked > 1 else ''} the reviewer could not be asked again (see the receipt); its first answer stands." if reasked else "")}
        metadata = {'questions': {q['question_id']: done['receipt'] for family, done, _, _ in outcomes for q in family},
                    'problem_images': {qid: count for _, done, _, _ in outcomes for qid, count in done['images'].items()},
                    'problem_summaries': {family[0]['question_id']: done['summary'] for family, done, _, _ in outcomes}}
        return 'complete', result, metadata, '', ''

    def run_rubric_check(self, task, profile, context, host=None):
        """One turn of a check, whoever runs it: (status, result, provider metadata, error, not before). A resolution of the concerns
        a completed check listed, a check with versions (one problem per request), else one question per request. The plan's check
        publishes it on its own row (`run_rubric_quality`); a quiz draft's revision keeps it with the revision
        (`quiz_drafts._quiz_rubric_check`, `host`).

        FITTED TO EACH QUESTION (requirement, 2026-10-28): before any request, Jev
        reads each question with the standing memories (`rubric_memory.memory_fit`), and a memory it finds contradicting that
        question is left out of that question's requests (`rules_for`)."""
        if context.get('standing_rules') and 'memory_fit' not in context:
            from .rubric_memory import memory_fit
            context = {**context, 'memory_fit': memory_fit(getattr(self, 'jev', None), task['id'], context)}
        if context.get('resolves'):
            return self.run_rubric_resolution(task, profile, context, host)
        if context.get('scheme') == VERSIONS_SCHEME:
            return self.run_rubric_versions(task, profile, context, host)
        return self.run_rubric_questions(task, profile, context, host)

    def run_rubric_quality(self, task):
        profile, context = json.loads(task['profile_json']), json.loads(task['input_json'])
        status, result, metadata, error, after = self.run_rubric_check(task, profile, context)
        with self.rosters.lock(), connect(self.path) as db:
            published = db.execute("UPDATE frq_rubric_quality_checks SET status=?,result_json=?,provider_json=?,error=?,not_before=?,updated_at=? WHERE id=? AND status='running'",
                (status, encoded(result) if result else None, encoded(metadata) if metadata else None, error, after, timestamp(), task['id']))
            if status == 'complete' and published.rowcount:
                self.event(db, task['assignment_id'], 'rubric_quality_ready', {'check_id': task['id']}, 'worker')
        if status == 'complete' and result and any((issue.get('fix') or {}).get('kind') == 'key' for issue in result.get('suspected_errors') or []):
            self.apply_rubric_fixes(task['assignment_id'], task['id'])

    def apply_rubric_fixes(self, aid, check_id):
        """BOOKKEEPING IS FIXED, NOT ASKED: the forms a completed check's key fixes add, saved through the ordinary `save_answer_key`
        (actor `automatic`; a key is no part of what a confirmation covers, so the check stays current), once per check, with one event
        naming what each fix added or why it was left to the instructor: a question this check did not ask (settled), one with grades
        already, a part the key does not have, or a key that changed meanwhile. Never a reason for the check to fail: a fix that cannot
        be made is simply asked as a decision. Returns the event's outcomes."""
        import logging
        try:
            with self.rosters.lock(), connect(self.path) as db:
                row = db.execute('SELECT result_json,status,cancelled FROM frq_rubric_quality_checks WHERE id=? AND assignment_id=?', (check_id, aid)).fetchone()
                if (not row or row['status'] != 'complete' or row['cancelled']
                        or db.execute("SELECT 1 FROM frq_events WHERE assignment_id=? AND kind='rubric_fixes_applied' AND json_extract(detail_json,'$.check_id')=?",
                                      (aid, check_id)).fetchone()):
                    return []
                result = json.loads(row['result_json'])
                settled, plan_version = set(result.get('settled') or []), self.plan(db, aid)['version']
                outcomes, keys, entries = [], {}, {}
                # A drafted quiz's key fix that an answers revision resolved (2026-10-29: Quiz 6's Problems 2 and 3) left the listed
                # findings without ever being made, since the draft's keys are the draft's until approval: it is made here too, named
                # by its place among the resolved findings (`resolved`), with no Undo in the window, which lists only open findings.
                rows = [({'concern': n}, issue) for n, issue in enumerate(result.get('suspected_errors') or [])] + [
                    ({'resolved': n}, issue) for n, issue in enumerate((result.get('resolution') or {}).get('resolved') or [])
                    if issue.get('kind') == 'suspected_error' and issue.get('question_id')]
                for where, issue in rows:
                    fix, qid = issue.get('fix') or {}, issue.get('question_id')
                    if fix.get('kind') != 'key':
                        continue
                    if fix.get('applied') is not None:  # an earlier check of these rubrics already added them
                        outcomes.append({**where, 'question_id': qid, 'state': 'applied', 'added': fix['applied'], 'carried': True})
                        continue
                    why = ('This version kept its confirmed guidance and was not asked again.' if qid in settled
                           else 'This question has grades already.' if self.graded_yet(db, aid, qid) else '')
                    if not why:
                        if qid not in keys:
                            q = self.question(db, aid, qid)
                            entries[qid] = {'question_id': qid, 'version': q['version']}
                            keys[qid] = [{'label': part.get('label', ''), 'accept': list(part.get('accept') or [])} for part in q['content'].get('answer_key') or []]
                        trial, added = [dict(part, accept=list(part['accept'])) for part in keys[qid]], []
                        for wanted in fix.get('keys') or []:
                            # A named part by its label; an unnamed one is the key's unlabeled part, or its only part.
                            target = [part for part in trial if part['label'] == wanted['part']] or (trial if not wanted['part'] and len(trial) == 1 else [])
                            if len(target) != 1:
                                why = 'The answer key has no part the fix names.'
                                break
                            new = [form for form in wanted['add'] if form not in target[0]['accept']]
                            if len(target[0]['accept']) + len(new) > 20:
                                why = 'The answer key part would hold more than 20 accepted answers.'
                                break
                            target[0]['accept'] += new
                            if new:
                                added.append({'part': target[0]['label'], 'forms': new})
                        if not why:
                            keys[qid] = trial
                            outcomes.append({**where, 'question_id': qid, 'state': 'applied', 'added': added})
                            continue
                    outcomes.append({**where, 'question_id': qid, 'state': 'skipped', 'why': why})
            changed = sorted({o['question_id'] for o in outcomes if o['state'] == 'applied' and o.get('added') and not o.get('carried')})
            if changed:
                try:
                    self.save_answer_key(aid, {'version': plan_version, 'questions': [{**entries[qid], 'answer_key': keys[qid]} for qid in changed]}, actor='automatic')
                except (ValueError, Conflict) as failure:  # the key or the plan changed meanwhile: asked instead
                    why = 'The answer key changed meanwhile: ' + (getattr(failure, 'description', None) or str(failure))
                    outcomes = [{**o, 'state': 'skipped', 'why': why, 'added': []} if o['question_id'] in changed and not o.get('carried') else o for o in outcomes]
            with self.rosters.lock(), connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                self.event(db, aid, 'rubric_fixes_applied', {'check_id': check_id, 'fixes': outcomes}, 'automatic')
            return outcomes
        except Exception:  # noqa: BLE001 - the check stands; its key fixes are then asked as decisions
            logging.getLogger(__name__).exception('the key fixes of rubric check %s could not be made', check_id)
            return []

    @staticmethod
    def fix_states(db, aid, check_id, result):
        """{concern number: {kind, note, state, added?, why?}} of a check's fixed findings: 'applied' (listed apart, with Undo),
        'waiting' (a key fix not made yet), 'skipped' and 'undone' (asked as decisions)."""
        states = {}
        for n, issue in enumerate(result.get('suspected_errors') or []):
            fix = issue.get('fix')
            if isinstance(fix, dict) and fix.get('kind') in FIX_KINDS:
                states[n] = {'kind': fix['kind'], 'note': fix.get('note', ''), 'state': 'applied' if fix['kind'] == 'guidance' else 'waiting'}
        if not states:
            return {}
        for kind, detail in db.execute("""SELECT kind,detail_json FROM frq_events WHERE assignment_id=? AND kind IN ('rubric_fixes_applied','rubric_fix_undone')
                AND json_extract(detail_json,'$.check_id')=? ORDER BY rowid""", (aid, check_id)):
            detail = json.loads(detail)
            if kind == 'rubric_fixes_applied':
                for outcome in detail.get('fixes') or []:
                    if outcome.get('concern') in states:
                        states[outcome['concern']].update({'state': outcome['state'], 'added': outcome.get('added') or [],
                                                           **({'why': outcome['why']} if outcome.get('why') else {})})
            else:
                for n in detail.get('concerns') or []:
                    if n in states:
                        states[n]['state'] = 'undone'
        return states

    def undo_rubric_fix(self, aid, data):
        """Undo fixed findings of the current check ({check_id, concerns: [numbers]}, a shared finding's every row at once): each is
        asked as a decision from then on, and a key fix takes back exactly the forms it added, while its question has no grade."""
        wanted = items(data.get('concerns'), 'Fixes to undo', 200)
        if not wanted or any(type(n) is not int or n < 0 for n in wanted):
            raise ValueError('Name the fixes to undo by their number.')
        with self.rosters.lock(), connect(self.path) as db:
            status = self.rubric_quality_status(db, aid, details=True)
            check = status['check']
            if not check or check['id'] != data.get('check_id') or check['status'] != 'complete' or not check['current']:
                raise Conflict('Undo the fixes of the current rubric check. Refresh first.')
            states = check.get('fixes') or {}
            if any(states.get(n, {}).get('state') != 'applied' for n in wanted):
                raise Conflict('That fix is not in place. Refresh first.')
            errors = check['result']['suspected_errors']
            take = {}
            for n in wanted:
                for added in states[n].get('added') or []:
                    take.setdefault(errors[n]['question_id'], []).append(added)
            entries = []
            for qid, removed in take.items():
                if self.graded_yet(db, aid, qid):
                    raise Conflict('This question has grades now: change its answer key under Answer key instead.')
                q = self.question(db, aid, qid)
                key = [{'label': part.get('label', ''), 'accept': list(part.get('accept') or [])} for part in q['content'].get('answer_key') or []]
                for added in removed:
                    for part in key:
                        if part['label'] == added['part']:
                            part['accept'] = [form for form in part['accept'] if form not in added['forms']] or part['accept']
                entries.append({'question_id': qid, 'version': q['version'], 'answer_key': key})
            plan_version = self.plan(db, aid)['version']
        if entries:
            self.save_answer_key(aid, {'version': plan_version, 'questions': entries})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.event(db, aid, 'rubric_fix_undone', {'check_id': data['check_id'], 'concerns': sorted(set(wanted)), 'keys': [e['question_id'] for e in entries]})
        return self.rubric_quality(aid)

    def apply_rubric_edit(self, aid, data):
        """AN EDIT ONLY THE INSTRUCTOR CAN MAKE (requirement, 2026-10-29): the points change a decision of the current check proposes
        ({check_id, concerns: [numbers]}, a shared decision's every row at once), approved by the instructor's one click and saved
        as their own rubric edit (`edit_rubric`: a new immutable revision of each question it names, the compiler record naming the
        change). Approving names exactly those rubrics again for the reviewer (`confirm_rubric_review`), so the next step is the
        check of the changed rubrics alone. Refused for a check that is not the current one or a decision that proposes no edit."""
        wanted = items(data.get('concerns'), 'Decisions', 200)
        if not wanted or any(type(n) is not int or n < 0 for n in wanted):
            raise ValueError('Name the decisions whose change you approve, by their number.')
        with self.rosters.lock(), connect(self.path) as db:
            status = self.rubric_quality_status(db, aid, details=True)
            check = status['check']
            if not check or check['id'] != data.get('check_id') or check['status'] != 'complete' or not check['current']:
                raise Conflict('Approve the changes of the current rubric check. Refresh first.')
            concerns = concern_list(check['result'])
            if any(n >= len(concerns) or not concerns[n].get('edit') for n in wanted):
                raise Conflict('That decision proposes no change to approve. Refresh first.')
            lines = {}
            for n in wanted:
                for line in concerns[n]['edit']:
                    lines.setdefault(line['question_id'], {})[line['item_id']] = line['points']
            current = {qid: self.question(db, aid, qid) for qid in lines}
        changed = []
        for qid, points in lines.items():
            q = current[qid]
            asked = [{'item_id': iid, 'points': value} for iid, value in points.items()
                     if any(item['id'] == iid and float(item['points']) != float(value) for item in q['rubric']['rules']['items'])]
            if asked:
                self.edit_rubric(aid, qid, {'version': q['version'], 'rubric_id': q['rubric']['id'], 'sha256': q['rubric']['sha256'], 'lines': asked})
                changed.append(qid)
        if changed:
            with connect(self.path) as db:
                ready = self.rubric_quality_status(db, aid)['readiness']
            self.confirm_rubric_review(aid, {'questions': {qid: {'rubric_id': ready[qid]['rubric_id'], 'comments_sha256': ready[qid]['comments_sha256']} for qid in changed}})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.event(db, aid, 'rubric_edit_approved', {'check_id': data['check_id'], 'concerns': sorted(set(wanted)), 'questions': changed})
        return self.rubric_quality(aid)

    def earlier_answers(self, db, aid):
        """NOTHING ASKED TWICE (requirement, 2026-10-24: on Quiz 4 the same three questions came back in three checks, and the
        tableau-mark question came back for Version E after it was ruled on A to D): every answer the instructor typed to a rubric
        question of THIS plan, in Key & Rubric Finalization's Resolve concerns and at the draft review of the quiz it was approved
        from, once each with the questions it was about. The check reads them as the instructor's word on this assignment."""
        found, index = [], {}
        cut = lambda value, n: value if len(value) <= n else value[:n].rsplit(' ', 1)[0] + ' […]'

        def add(asked, answer, where):
            asked, answer = ' '.join(str(asked or '').split()), ' '.join(str(answer or '').split())
            if not answer:
                return
            key = (asked[:200].casefold(), answer.casefold())
            if key in index:
                index[key]['about'] = list(dict.fromkeys(index[key]['about'] + where))
                return
            index[key] = {'asked': cut(asked, 900), 'answered': cut(answer, 1500), 'about': list(dict.fromkeys(where))}
            found.append(index[key])
        for (given,) in db.execute("""SELECT input_json FROM frq_rubric_quality_checks WHERE assignment_id=? AND status='complete' AND cancelled=0
                AND json_extract(input_json,'$.resolves') IS NOT NULL ORDER BY rowid""", (aid,)):
            given = json.loads(given)
            names = {q['question_id']: (f"Version {q['exam_version']} · " if q.get('exam_version') else '') + q['title'] for q in given.get('questions') or []}
            for concern in given['resolves'].get('concerns') or []:
                ids = concern.get('question_ids') or [concern.get('question_id')]
                add(concern.get('decision') or concern.get('title') or concern.get('message'), concern.get('response'), [names[q] for q in ids if q in names])
        memory = getattr(self, 'rubric_memory', None)
        for problem, _, asked, answer in (memory._draft_answers(db, aid) if memory else []):
            add(asked, answer, [f'Problem {problem[1:]}' if problem else 'At the quiz draft review'])
        return found[-EARLIER_ANSWERS:]

    def run_rubric_questions(self, task, profile, context, host=None):
        """A check without versions: every question alone in its own request (ONE QUESTION PER REQUEST, requirement, 2026-09-21)."""
        label = lambda q: (f"Version {q['exam_version']} · " if q['exam_version'] else '') + q['title']

        def one(question):
            try:
                return question, self.review_question(task, profile, context, question, host), None, None
            except RetryLater as waiting:
                return question, None, waiting.seconds, None
            except (ProviderError, ValueError, TypeError, KeyError) as failure:
                return question, None, None, str(failure)
        # Every question is its own request and its own context, asked side by side. Each settled answer is a retained call of
        # this check, so a later turn (after a wait, or a restart) sends only what is still missing. A question whose confirmed
        # finalization stands is not asked when others are (A CHECK ASKS ONLY WHAT DOES NOT STAND).
        settled = settled_of(context)
        asked = [q for q in context['questions'] if q['question_id'] not in settled]
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(asked)))) as pool:
            outcomes = list(pool.map(one, asked))
        waits = [seconds for _, _, seconds, _ in outcomes if seconds is not None]
        failures = [f"{label(q)}: {failure}" for q, _, _, failure in outcomes if failure]
        result, metadata, error, status, after = None, None, '', 'complete', ''
        if failures:
            status, error = 'failed', f"{len(failures)} of {len(outcomes)} questions could not be checked. " + ' '.join(failures[:3])
        elif waits:
            status, after = 'pending', later(min(waits))
        else:
            found = [done for _, done, _, _ in outcomes]
            proposed = [done['entry'] for done in found]
            entries = sorted(proposed + list(settled.values()), key=lambda q: q['question_id'])
            errors = [issue for done in found for issue in done['errors']]
            pictures = sum(bool(done['images']) for done in found)
            still = unresolved_questions({'questions': entries}, context)
            reasked = sum(bool(done['receipt'].get('reask_error')) for done in found)
            others, (which, keeps) = len(settled), settled_words(context)
            kept = (context.get('settled') or {}).get('carried') or {}
            result = {'mode': 'per_question', 'questions': entries, 'suspected_errors': kept.get('suspected_errors', []) + errors, 'unresolved': still,
                **({'settled': sorted(settled)} if settled else {}), **({'carried': kept['questions']} if kept else {}),
                'summary': (f"Only the rubrics {which} were checked ({scope_text(context['questions'], {q['question_id'] for q in asked})}): "
                            f"{len(proposed)} question{'s' if len(proposed) != 1 else ''}, each separately with its own rubric, answer key and " if settled
                            else f"{len(entries)} questions were checked separately, each with its own rubric, answer key and ")
                    + (f"printed problem ({pictures} read from the blank exam)." if pictures else "no picture of its printed problem.")
                    + (f" The other {others} question{'s' if others != 1 else ''} keep{'' if others != 1 else 's'} {'their' if others != 1 else 'its'} {keeps} and {'were' if others != 1 else 'was'} not reviewed again." if settled else "")
                    + f" {sum(len(q['clarifications']) for q in proposed)} clarifications, {sum(len(q['pairs']) for q in proposed)} exclusive pairs and {len(errors)} suspected errors are proposed."
                    + (f" {len(still)} question{'s' if len(still) > 1 else ''} still allow more points than they are worth: decide which of their lines are alternatives before you confirm." if still else "")
                    + (f" For {reasked} of them the reviewer could not be asked again (see the receipt); its first answer stands." if reasked else "")
                    + " Versions are not compared with each other: that would put two questions in one request."}
            metadata = {'questions': {q['question_id']: done['receipt'] for q, done, _, _ in outcomes}, 'problem_images': {q['question_id']: done['images'] for q, done, _, _ in outcomes}}
        return status, result, metadata, error, after

    def confirm_rubric_quality(self, aid, data):
        if data.get('confirm_review') is not True:
            raise ValueError('Review the clarifications, exclusions and suspected errors of every version, then confirm.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            status = self.rubric_quality_status(db, aid, details=True)
            version(data, status['version'])
            check = status['check']
            if not check or check['id'] != data.get('check_id') or check['status'] != 'complete' or not check['current'] or data.get('input_sha256') != status['input_sha256']:
                raise Conflict('Inspect a completed check of the current rubric revisions before confirming.')
            policy = {'rules': BUILTIN, 'questions': normalize_questions(data.get('questions'), status['context']),
                      'consistency_issues': check['result'].get('consistency_issues', []), 'suspected_errors': check['result'].get('suspected_errors', []),
                      # What this confirmation covered, question by question, so each question's standing can be judged alone.
                      'inputs': question_inputs(status['context'])}
            confirmation = self._record_confirmation(db, aid, status, check['id'], policy, {})
        confirmation.pop('review_id')  # This answer stays as it was; the plan's status names the new confirmation.
        memory = getattr(self, 'rubric_memory', None)
        if memory:  # NOTHING ASKED TWICE ACROSS ASSIGNMENTS (2026-10-24): the answers just approved teach the course's memories now
            memory.after_finalization(aid)
        return {**self.rubric_quality(aid), 'confirmation': confirmation}

    def clarify_question(self, aid, data):
        """Questions' clarifications and exclusive pairs, settled from the review workspace or a flag review's proposal (instructor,
        2026-10-03: "fix inside review, then regrade"; a rule settled on one version goes to the same line of the others). One new
        confirmation that keeps every other question's confirmed entry exactly. `questions`: [{question_id, rubric_id, clarifications,
        pairs}] (or one question's fields at the top level). Only the plan's standing confirmation of the current rubrics can be
        clarified this way, and only while no rubric check is waiting or running, so nothing the instructor has not read is adopted with
        it. It approves no response; regrading is a separate, explicit batch."""
        if data.get('confirm_review') is not True:
            raise ValueError("Read the question's rubric lines and concerns, then save the clarification.")
        asked = data.get('questions') if 'questions' in data else [{k: data.get(k) for k in ('question_id', 'rubric_id', 'clarifications', 'pairs')}]
        asked = items(asked, 'Questions to clarify', 100)
        if (not asked or any(not isinstance(q, dict) or not isinstance(q.get('question_id'), str) for q in asked)
                or len({q['question_id'] for q in asked}) != len(asked)):
            raise ValueError('Name each question to clarify once.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            status = self.rubric_quality_status(db, aid, details=True)
            version(data, status['version'])
            review, check = status['review'], status['check']
            if status['state'] != 'confirmed' or not review or review['id'] != data.get('review_id'):
                raise Conflict('The rubric finalization changed. Refresh, or clarify the rule in the rubric finalization.')
            if check and (check['status'] in ('pending', 'running') or check['id'] != review['check_id']):
                raise Conflict('A newer rubric check waits for you: clarify the rule in the rubric finalization.')
            by_id = {q['question_id']: q for q in status['context']['questions']}
            entries = {e['question_id']: e for e in review['policy'].get('questions', [])}
            if set(entries) != set(by_id):
                raise Conflict('The rubric finalization does not cover every question. Clarify the rule in the rubric finalization.')
            for row in asked:
                question = by_id.get(row.get('question_id'))
                if not question or row.get('rubric_id') != question['rubric_id']:
                    raise Conflict("A question's rubric changed. Refresh before clarifying it.")
                old = policy_question(review['policy'], question['question_id'], question['rubric_id'])
                if not old:
                    raise Conflict('The rubric finalization does not cover this rubric. Clarify the rule in the rubric finalization.')
                entry = normalize_questions([{'question_id': question['question_id'], 'rubric_id': question['rubric_id'], 'pairs': row.get('pairs'),
                                              'clarifications': row.get('clarifications')}], {'questions': [question]})[0]
                entry['reasons'] = [r for r in old.get('reasons', []) if any(r['item_id'] in pair for pair in entry['pairs'])]
                entries[question['question_id']] = entry
            policy = {**review['policy'], 'questions': sorted(entries.values(), key=lambda q: q['question_id']), 'inputs': question_inputs(status['context'])}
            clarified = [row['question_id'] for row in asked]
            confirmation = self._record_confirmation(db, aid, status, review['check_id'], policy, {'clarified': clarified[0] if len(clarified) == 1 else clarified})
        return {'confirmation': confirmation, 'review_id': confirmation['review_id']}

    def _record_confirmation(self, db, aid, status, check_id, policy, extra):
        """Record a confirmation of `policy` (the plan's whole policy) and point the plan at it: the overflow check, the rubric
        approvals, and the suspension of automatic grades of exactly the questions whose guidance changed. Inside the caller's
        transaction and lifecycle lock."""
        open_questions = unresolved_questions(policy, status['context'])
        if open_questions:
            first = open_questions[0]
            q = next(q for q in status['context']['questions'] if q['question_id'] == first['question_id'])
            raise ValueError(f"{len(open_questions)} question{'s' if len(open_questions) > 1 else ''} still allow more points than they are worth. "
                f"{('Version ' + q['exam_version'] + ' · ') if q['exam_version'] else ''}{q['title']}: {overflow_text(first)}"
                f"{'' if first.get('undecided') else '. Mark which of them are alternatives'}, then confirm.")
        rid = uid()
        db.execute('INSERT INTO frq_rubric_quality_reviews VALUES(?,?,?,?,?,?,?)', (rid, aid, check_id, status['input_sha256'], encoded(policy), 'workspace', timestamp()))
        db.execute('INSERT INTO frq_rubric_quality_options VALUES(?,1,1,?,?) ON CONFLICT(assignment_id) DO UPDATE SET review_id=excluded.review_id,version=version+1,updated_at=excluded.updated_at', (aid, rid, timestamp()))
        # One confirmation finalizes the rubrics: the instructor has just read every line of them beside the findings.
        for q in status['context']['questions']:
            sha = db.execute('SELECT sha256 FROM frq_rubric_revisions WHERE id=?', (q['rubric_id'],)).fetchone()[0]
            if db.execute('INSERT OR IGNORE INTO frq_rubric_approvals VALUES(?,?,?,?,?)', (q['rubric_id'], sha, 'workspace', 'Approved with the rubric finalization.', timestamp())).rowcount:
                self.event(db, aid, 'rubric_approved', {'rubric_id': q['rubric_id'], 'sha256': sha, 'review_id': rid})
        # Only grades whose question's guidance this confirmation CHANGED are suspended (2026-09-25); a question confirmed again
        # with the entry that stood keeps its automatic grades, whichever confirmation they were made under.
        kept, changed = [], []
        for q in status['context']['questions']:
            standing = status['standing'].get(q['question_id'])
            same = standing and standing['guidance_sha256'] == guidance_key(policy, policy_question(policy, q['question_id'], q['rubric_id']))
            (kept if same else changed).append(q['question_id'])

        def keep(qid, scope):
            new = guidance_key(policy, policy_question(policy, qid, scope.get('rubric_id')))
            graded_under = scope.get('rubric_quality')
            if new is None or not graded_under or graded_under == 'unconfirmed':
                return False
            record = self.confirmation_record(db, graded_under)
            return bool(record) and guidance_key(record['policy'], policy_question(record['policy'], qid, scope.get('rubric_id'))) == new
        reason = 'Instructor confirmed a new rubric quality policy for this question.'
        suspended = self.suspend_acceptance(db, aid, reason, keep=keep)
        # GUIDANCE PUT BACK (2026-10-15): grades an earlier finalization suspended count again where this one restores their guidance.
        reinstated = self.reinstate_guidance_suspensions(db, aid, reason, keep, rid)
        self.event(db, aid, 'rubric_quality_confirmed', {'review_id': rid, 'check_id': check_id, 'kept': kept, 'changed': changed, 'suspended': suspended,
                                                          **({'reinstated': reinstated} if reinstated else {}), **extra})
        return {'kept': len(kept), 'changed': len(changed), 'suspended': suspended, 'review_id': rid, **({'reinstated': reinstated} if reinstated else {})}
