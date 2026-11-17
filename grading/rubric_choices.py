"""Rubric choices (GRADING "RUBRIC CHOICES"; requirement, 2026-10-24).

Asked how to cut the rubric reviewer's questions, the instructor was shown that the same few policies came back on assignment after
assignment (is work needed for full credit, do rounded decimals count, do abbreviations of a required word count, when does an "all
incorrect" line apply) and chose, among other things, "a few quick choices before drafting ... the draft is then written to your
answers and the reviewer doesn't ask" ("I actually like all of these ideas, implement them").

A plan's choices are owned by its TERM grading plan (`frq_rubric_choices`, migration 049, append-only: the latest row stands). A quiz
draft carries its own in the draft's request (`quiz_drafts.input_json`, course-owned like the draft) and its approval records them
as the new plan's first row. A choice left out is "ask me": nothing is said about it. The rubric writers and the rubric check read
them as `assignment_choices`, the instructor's word on THIS assignment (like a line comment that reaches every line it concerns);
they are in no confirmation's fingerprint, and change no rubric, confirmation or grade by themselves.
"""
from __future__ import annotations

import json

from course_storage import timestamp
from rosters import connect

from .models import encoded, uid

# key -> (topic, question, {option: (label, what the writers and the reviewer are told)}). The order is the order shown and sent.
CHOICES = {
    'work': ('Shown work', 'Does a correct answer need supporting work for full credit?', {
        'expected': ('Work expected',
                     'Work is expected for full credit: where the problem asks for work or a rubric line names it, a correct answer with no '
                     'supporting work does not earn full credit, and the rubric charges (or does not credit) the missing work of each part '
                     'that asks for it.'),
        'not_needed': ('The answer alone is enough',
                       'A correct, clearly designated answer earns full credit even with no work shown; words such as "with supporting work" '
                       'in a rubric line do not reduce the score.')}),
    'decimals': ('Rounded answers', 'Where an exact value is asked for, does a rounded decimal or percent count?', {
        'exact': ('Exact values only',
                  'Where a line names an exact value, a rounded decimal or percent does not earn it; an exactly equal form (an unreduced '
                  'fraction, for example) does.'),
        'rounded': ('Rounded decimals count',
                    'Where a line names an exact value, a decimal or percent correctly rounded to at least two decimal places earns it too.')}),
    'variants': ('Spelling of required words', 'Does an abbreviation or misspelling of a required word count?', {
        'accept': ('Recognizable variants count',
                   'A recognizable abbreviation or misspelling of a required word (for example "dep." or "dependant" for dependent) '
                   'satisfies a line that asks for the word.'),
        'exact': ('Only the full, correct word',
                  'A required word must be written in full and spelled correctly; an abbreviation or misspelling does not satisfy the line.')}),
    # LETTERS AND NOTATION (requirement, 2026-10-29). Offered, never answered for
    # them: a course with no answer yet starts on "Ask me".
    'letters': ('Letters and notation', "Where a line names the problem's letters or the course's notation, what else counts?", {
        'printed': ('Only the printed letters and notation',
                    "Where a line names the problem's variables or parameters, or the course's notation, only those exact letters and that "
                    'notation earn it: another letter, the same letter in the other case (X for x) or another standard notation (F\' for '
                    'F^C) does not.'),
        'case': ('The other case of a letter counts too',
                 "Where a line names the problem's variables or parameters, or the course's notation, the same letter in the other case (X "
                 'for x) earns it as well, as does a letter whose case the handwriting leaves unclear; another letter or another notation '
                 'does not.'),
        'consistent': ('Own letters and standard notation count',
                       "Where a line names the problem's variables or parameters, or the course's notation, a student's own letters used "
                       "consistently for the same quantities, either case of a letter, and another standard notation for the same thing (F' "
                       'or a bar for F^C) earn it too.')}),
    'all_incorrect': ('The "all incorrect" line', 'When does a line for completely incorrect answers apply?', {
        'nothing_else': ('Only when nothing else applies',
                         'A line for completely or entirely incorrect answers applies only to a response that is not blank and earns no other '
                         'line of its question (of its part, for a part\'s line); it is never combined with another line.'),
        'all_wrong': ('Whenever every answer is wrong',
                      'A line for answers that are all incorrect applies whenever every answer of its question (or part) is wrong, alongside '
                      'any line the response\'s form or work earns.')}),
}


def options():
    """What the pages show: each choice's topic, question and options, in order."""
    return [{'key': key, 'topic': topic, 'question': question, 'options': [{'value': value, 'label': label} for value, (label, _) in found.items()]}
            for key, (topic, question, found) in CHOICES.items()]


def clean(value):
    """{key: option} with only known keys and options; a missing, empty or null choice is "ask me" and left out. ValueError otherwise."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Rubric choices are given as {choice: option}.')
    found = {}
    for key, option in value.items():
        if key not in CHOICES:
            raise ValueError(f'There is no rubric choice called {key}.')
        if option in (None, ''):
            continue
        if not isinstance(option, str) or option not in CHOICES[key][2]:
            raise ValueError(f'Choose one of the offered answers for “{CHOICES[key][0]}”.')
        found[key] = option
    return {key: found[key] for key in CHOICES if key in found}


def stated(choices):
    """`assignment_choices` of a writer's or reviewer's payload: [{topic, choice}] in order, [] when nothing was chosen (the payload then
    names none, so a request without choices is exactly the request it always was)."""
    return [{'topic': CHOICES[key][0], 'choice': CHOICES[key][2][option][1]} for key, option in clean(choices).items()]


def of_plan(db, aid):
    """A plan's current choices: its latest row, else {}."""
    row = db.execute('SELECT choices_json FROM frq_rubric_choices WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                     (aid,)).fetchone()
    return json.loads(row[0]) if row else {}


def of_course(db, definition_id):
    """The course's latest choices, from any plan or quiz draft of it: what a new request starts from, never what it sends unasked."""
    if not definition_id:
        return {}
    plan = db.execute('''SELECT c.choices_json,c.created_at FROM frq_rubric_choices c JOIN frq_assignments a ON a.id=c.assignment_id
        JOIN exam_events e ON e.id=a.event_id JOIN assignments d ON d.id=e.assignment_id WHERE d.definition_id=?
        ORDER BY c.created_at DESC,c.rowid DESC LIMIT 1''', (definition_id,)).fetchone()
    draft = db.execute("""SELECT json_extract(input_json,'$.rubric_choices'),created_at FROM quiz_drafts WHERE definition_id=?
        AND json_extract(input_json,'$.rubric_choices') IS NOT NULL ORDER BY created_at DESC,rowid DESC LIMIT 1""", (definition_id,)).fetchone()
    latest = max((row for row in (plan, draft) if row), key=lambda row: row[1], default=None)
    return clean(json.loads(latest[0])) if latest else {}


def record(db, aid, choices, actor):
    """Record a plan's choices unless they are what stands already; inside the caller's transaction. Returns whether a row was written."""
    choices = clean(choices)
    if choices == of_plan(db, aid) and (choices or not db.execute('SELECT 1 FROM frq_rubric_choices WHERE assignment_id=?', (aid,)).fetchone()):
        return False
    db.execute('INSERT INTO frq_rubric_choices VALUES(?,?,?,?,?)', (uid(), aid, encoded(choices), actor, timestamp()))
    return True


class RubricChoicesMixin:
    def rubric_choices(self, aid):
        """A plan's choices, the course's latest (what the page starts a plan without any from) and the choices offered."""
        from .rubric_memory import definition_of_plan
        with connect(self.path) as db:
            self.plan(db, aid)
            saved = db.execute('SELECT 1 FROM frq_rubric_choices WHERE assignment_id=?', (aid,)).fetchone()
            return {'choices': of_plan(db, aid), 'saved': bool(saved), 'course': of_course(db, definition_of_plan(db, aid)), 'options': options()}

    def set_rubric_choices(self, aid, data, *, actor='workspace'):
        """The instructor's choices for this assignment ({choices: {key: option}}; a key left out is "ask me"). Read by the next draft and
        the next rubric check; nothing already drafted, checked or confirmed changes."""
        choices = clean(data.get('choices'))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.plan(db, aid)
            if record(db, aid, choices, actor):
                self.event(db, aid, 'rubric_choices_saved', {'choices': choices}, actor)
        return self.rubric_choices(aid)
