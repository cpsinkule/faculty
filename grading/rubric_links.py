"""LINKED VERSIONS (requirement, 2026-10-02): which rubric lines of one problem correspond across its versions.

The instructor no longer settles the same rubric question once per version. Lines of a problem's versions are linked PER LINE:
"same" lines credit the same criterion with the same kind of answer (guidance carries from one to another, written for each
version's own values), "related" lines belong to the same step with a different outcome or structure on each version (shown
together, decided per version: a matrix expression undefined by multiplication on one version, by addition on another and
defined on a third), and "none" is the instructor's unlink. Where they come from, the strongest first:

1. the instructor (`frq_rubric_line_links`, owned by the TERM grading plan, append-only, latest row of a pair wins);
2. the rubric reviewer (`line_links` of the plan's latest completed check, versions v4), which replaces the seeds of its problems;
3. seeds: lines with the same item ID on questions whose rubrics were drafted together or adapted from one another (`origins`).
   A writer that drafted every version at once (rubric_write_versions v4, quiz_rubric v4) also said whether each shared line is the
   same on every version or related (`line_kinds` in the rubric's provenance): such a seed starts as that kind, origin "drafted";
   any other seed is "not verified yet".

Links are an aid to reviewing and editing: never rubric wording, guidance, a score, or part of what a check or a confirmation
covers, so changing one stales nothing.
"""
import json

from course_storage import timestamp
from rosters import connect

from .models import items, text, uid
from .rubric_quality import LINK_KINDS, linked_groups, origins


def pair_key(a, b):
    """One unordered pair of lines, each (question_id, item_id), in the table's order (question_id < other_question_id)."""
    return tuple(sorted((tuple(a), tuple(b))))


def instructor_pairs(db, aid):
    """{pair_key: kind}: the instructor's current decision on each pair of lines they linked or unlinked."""
    found = {}
    for row in db.execute('SELECT question_id,item_id,other_question_id,other_item_id,kind FROM frq_rubric_line_links WHERE assignment_id=? ORDER BY rowid', (aid,)):
        found[pair_key((row[0], row[1]), (row[2], row[3]))] = row[4]
    return found


def drafted_kinds(compiler):
    """{line ID: 'same' | 'related'} the writer gave a rubric's lines when it drafted every version at once, followed back through the
    instructor's edits and corrections (which keep the line IDs); {} for any other rubric."""
    seen = 0
    while isinstance(compiler, dict) and seen < 50:
        if isinstance(compiler.get('line_kinds'), dict):
            return {iid: kind for iid, kind in compiler['line_kinds'].items() if kind in LINK_KINDS}
        compiler, seen = compiler.get('based_on'), seen + 1
    return {}


def seed_sets(questions, families):
    """Lines sharing an item ID on questions of one problem whose rubrics were made together (`linked_groups` of their origins). Where
    every one of a seed's rubrics carries the writer's kind for that line, the seed starts as that kind ("related" if any says so)."""
    by_id = {q['question_id']: q for q in questions}
    sets = []
    for family in families:
        members = [by_id[qid] for qid in family if qid in by_id]
        for group in linked_groups(members):
            lines = {}
            for qid in group:
                for item in by_id[qid]['rubric']['items']:
                    lines.setdefault(item['id'], []).append(qid)
            for iid, qids in lines.items():
                if len(qids) < 2:
                    continue
                said = [(by_id[qid].get('line_kinds') or {}).get(iid) for qid in qids]
                kind = 'related' if 'related' in said else 'same' if all(k == 'same' for k in said) else None
                sets.append({'kind': kind or 'seed', 'origin': 'drafted' if kind else 'seed',
                             'lines': [{'question_id': qid, 'item_id': iid} for qid in qids]})
    return sets


def effective_sets(questions, families, reviewed, instructor, reviewed_questions=()):
    """The plan's linked line sets: [{kind, origin, lines:[{question_id, item_id}], note?, conflict?}], each line in at most one set.
    `reviewed` is the reviewer's `line_links`, `reviewed_questions` the questions of the check that returned them (a check before
    versions v4 returned none), `instructor` `instructor_pairs`. A problem the reviewer saw takes its links instead of the seeds,
    even where it found none; the instructor's pairs then override either, and "none"
    breaks a set apart. A set is "same" only when every link in it is, "related" when any is, else a "seed" still to verify."""
    by_id = {q['question_id']: q for q in questions}
    exists = lambda line: line[0] in by_id and any(item['id'] == line[1] for item in by_id[line[0]]['rubric']['items'])
    seen = set(reviewed_questions) | {line['question_id'] for set_ in reviewed or [] for line in set_['lines']}
    base = [s for s in seed_sets(questions, families) if not seen & {line['question_id'] for line in s['lines']}] + list(reviewed or [])
    edges, notes = {}, {}
    for set_ in base:
        lines = [(line['question_id'], line['item_id']) for line in set_['lines'] if exists((line['question_id'], line['item_id']))]
        for n, a in enumerate(lines):
            for b in lines[n + 1:]:
                edges[pair_key(a, b)] = (set_['kind'], set_['origin'])
                if set_.get('note'):
                    notes[pair_key(a, b)] = set_['note']
    for pair, kind in instructor.items():
        if all(exists(line) for line in pair):
            edges[pair] = (kind, 'instructor')
    parent = {}

    def root(line):
        parent.setdefault(line, line)
        while parent[line] != line:
            parent[line] = parent[parent[line]]
            line = parent[line]
        return line
    live = {pair: value for pair, value in edges.items() if value[0] != 'none'}
    for a, b in live:
        parent[root(a)] = root(b)
    groups = {}
    for pair in live:
        groups.setdefault(root(pair[0]), set()).update(pair)
    order = {q['question_id']: (q.get('exam_version') or '', q['question_id']) for q in questions}
    sets = []
    for lines in groups.values():
        inner = [value for pair, value in live.items() if pair[0] in lines]
        kinds, from_ = {kind for kind, _ in inner}, {origin for _, origin in inner}
        kind = 'related' if 'related' in kinds else 'same' if kinds == {'same'} else 'seed'
        origin = 'instructor' if 'instructor' in from_ else 'reviewer' if 'reviewer' in from_ else 'drafted' if 'drafted' in from_ else 'seed'
        note = next((notes[pair] for pair in live if pair[0] in lines and pair in notes), '')
        ordered = sorted(lines, key=lambda line: (order[line[0]], line[1]))
        sets.append({'kind': kind, 'origin': origin, **({'note': note} if note else {}),
                     # Two lines of one question in one set (only possible from conflicting decisions): shown, never copied or mirrored.
                     **({'conflict': True} if len({line[0] for line in ordered}) != len(ordered) else {}),
                     'lines': [{'question_id': qid, 'item_id': iid} for qid, iid in ordered]})
    return sorted(sets, key=lambda s: (order[s['lines'][0]['question_id']], s['lines'][0]['item_id']))


def known_links(sets, instructor, questions):
    """What a check tells the reviewer about links (`known_links`): the instructor's decisions as fixed pairs, and every other set
    as a candidate to verify ("seed"). Each line names its version and item ID. [] when there is nothing."""
    version = {q['question_id']: q['exam_version'] for q in questions}
    # Each line keeps its question, so a problem's request takes only its own links (item IDs such as R1 repeat across problems).
    line = lambda qid, iid: {'question_id': qid, 'version': version[qid], 'item_id': iid}
    fixed = [{'kind': kind, 'origin': 'instructor', 'lines': sorted([line(*a), line(*b)], key=lambda row: row['version'])} for (a, b), kind in sorted(instructor.items())
             if a[0] in version and b[0] in version]
    candidates = [{'kind': 'seed', 'origin': 'seed', 'lines': sorted((line(l['question_id'], l['item_id']) for l in s['lines']), key=lambda row: row['version'])}
                  for s in sets if s['origin'] != 'instructor' and all(l['question_id'] in version for l in s['lines'])]
    return fixed + candidates




class RubricLinksMixin:
    def question_origins(self, db, aid):
        """{question_id: (origins, line kinds)} from how each pinned rubric was made (`origins`, `drafted_kinds`)."""
        made = {}
        for qid, compiler in db.execute('SELECT p.question_id,b.compiler_json FROM frq_assignment_questions p JOIN frq_rubric_revisions b ON b.id=p.rubric_id WHERE p.assignment_id=?', (aid,)):
            compiler = json.loads(compiler)
            made[qid] = (origins(compiler), drafted_kinds(compiler))
        return made

    def line_link_state(self, db, aid, context=None):
        """{families, sets, instructor}: the plan's problems (the latest completed check's grouping, else how the rubrics were
        made) and their linked line sets. Read only."""
        context = context or self.rubric_quality_context(db, aid)[0]
        made = self.question_origins(db, aid)
        questions = [{**q, 'origins': made.get(q['question_id'], ([], {}))[0], 'line_kinds': made.get(q['question_id'], ([], {}))[1]} for q in context['questions']]
        row = db.execute("""SELECT json_extract(result_json,'$.families'),json_extract(result_json,'$.line_links') FROM frq_rubric_quality_checks
            WHERE assignment_id=? AND status='complete' AND cancelled=0 AND json_extract(result_json,'$.families') IS NOT NULL
            ORDER BY rowid DESC LIMIT 1""", (aid,)).fetchone()
        ids = {q['question_id'] for q in questions}
        families = [[qid for qid in family if qid in ids] for family in json.loads(row[0])] if row else []
        families = [family for family in families if family]
        grouped = {qid for family in families for qid in family}
        families += [group for group in linked_groups([q for q in questions if q['question_id'] not in grouped])]
        reviewed, saw = [], set()
        if row and row[1] is not None:  # A check under versions v4: its problems' links replace their seeds, even where it found none.
            reviewed = [s for s in json.loads(row[1]) if all(line['question_id'] in ids for line in s['lines'])]
            saw = {qid for family in json.loads(row[0]) for qid in family}
        instructor = instructor_pairs(db, aid)
        return {'families': families, 'sets': effective_sets(questions, families, reviewed, instructor, saw), 'instructor': instructor, 'questions': questions}

    def rubric_links(self, aid):
        """GET .../rubric-links: the plan's problems and linked line sets, for the finalization window and Rubrics & Templates."""
        with connect(self.path) as db:
            self.plan(db, aid)
            state = self.line_link_state(db, aid)
        return {'families': state['families'], 'sets': state['sets']}

    def set_rubric_links(self, aid, data):
        """POST .../rubric-links: the instructor links or unlinks lines (LINKED VERSIONS). `operations` in order, each one of
        {op: 'unlink', line}, {op: 'link', line, to, kind} or {op: 'kind', line, kind}, a line being {question_id, item_id}.
        Each writes pair rows: unlinking a line writes "none" to every other line of its set (so no path re-links it); linking
        a line to another set first takes it out of its own, and takes out of that set any line of the same question; a kind is
        written on every pair of the set. Nothing else changes: no check, confirmation, rubric or guidance."""
        operations = items(data.get('operations'), 'Link changes', 50)
        if not operations:
            raise ValueError('Choose a link to change.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.plan(db, aid)
            context = self.rubric_quality_context(db, aid)[0]
            known = {(q['question_id'], item['id']) for q in context['questions'] for item in q['rubric']['items']}
            version = {q['question_id']: q['exam_version'] for q in context['questions']}
            rows, now = [], timestamp()

            def line(value, label):
                found = (value.get('question_id'), value.get('item_id')) if isinstance(value, dict) else None
                if found is None or not all(isinstance(part, str) for part in found) or found not in known:
                    raise ValueError(f'{label} names a rubric line this assignment does not have.')
                return found

            def write(a, b, kind):
                if a[0] == b[0]:
                    return
                a, b = pair_key(a, b)
                db.execute('INSERT INTO frq_rubric_line_links VALUES(?,?,?,?,?,?,?,?,?)', (uid(), aid, a[0], a[1], b[0], b[1], kind, 'workspace', now))
                rows.append({'lines': [list(a), list(b)], 'kind': kind})

            def members(target, state):
                return next(([(l['question_id'], l['item_id']) for l in s['lines']] for s in state['sets']
                             if any((l['question_id'], l['item_id']) == target for l in s['lines'])), [target])

            def unlink(target, state):
                for other in members(target, state):
                    if other != target:
                        write(target, other, 'none')
            for operation in operations:
                if not isinstance(operation, dict) or operation.get('op') not in ('unlink', 'link', 'kind'):
                    raise ValueError('Each link change is unlink, link or kind.')
                state = self.line_link_state(db, aid, context)
                target = line(operation.get('line'), 'A link change')
                if operation['op'] == 'unlink':
                    unlink(target, state)
                    continue
                kind = text(operation.get('kind'), 'Link kind', 16, False)
                if kind not in LINK_KINDS:
                    raise ValueError('A link is "same" or "related".')
                if operation['op'] == 'kind':
                    group = members(target, state)
                    if len(group) < 2:
                        raise ValueError('That line is not linked to another version.')
                    for n, a in enumerate(group):
                        for b in group[n + 1:]:
                            write(a, b, kind)
                    continue
                to = line(operation.get('to'), 'A link')
                if to[0] == target[0] or not version.get(to[0]) or version.get(to[0]) == version.get(target[0]):
                    raise ValueError('Link a line to a line of another version.')
                if to not in members(target, state):
                    unlink(target, state)
                state = self.line_link_state(db, aid, context)
                group = members(to, state)
                for other in group:
                    if other[0] == target[0] and other != target:
                        unlink(other, state)
                state = self.line_link_state(db, aid, context)
                for other in members(to, state):
                    if other != target:
                        write(target, other, kind)
            self.event(db, aid, 'rubric_line_links_set', {'operations': len(operations), 'pairs': rows[:200]})
        return self.rubric_links(aid)

