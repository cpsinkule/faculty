"""Keep the final visible offering for every retained exam section."""
from werkzeug.exceptions import Conflict
from course_storage import decode_course
from rosters import term_key


def protect_references(db, previous, replacement=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_events'").fetchone():
        return
    key = term_key(previous)
    term = db.execute('SELECT id FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?', key).fetchone()
    if not term:
        return
    used = {r[0] for r in db.execute('''SELECT s.number FROM exam_event_sections x JOIN course_sections s ON s.id=x.section_id
                                      JOIN exam_events e ON e.id=x.event_id WHERE e.term_id=?''', (term['id'],))}
    if not used:
        return
    covered = set()
    for row in db.execute('SELECT * FROM courses WHERE id!=? AND deleted_at IS NULL', (previous['id'],)):
        course = decode_course(row)
        if term_key(course) == key:
            covered.update(course['sections'])
    if replacement and term_key(replacement) == key:
        covered.update(replacement['sections'])
    if used - covered:
        raise Conflict('This change would detach an exam section. Keep its course, term and section; mark the course not current to archive it.')
