"""ASSISTANT: bounded workspace navigation hints; never permissions or authoritative data."""
import re

FIELDS = ('assignment_id', 'term_id', 'section_id', 'enrollment_id', 'event_id', 'plan_id',
          'question_id', 'response_id', 'job_id', 'item_id', 'row', 'source_key')


def normalize(value):
    if not isinstance(value, dict):
        return {'record': {}, 'unsaved': False}
    record = value.get('record') or {}
    if not isinstance(record, dict):
        record = {}
    return {'record': {key: record[key] for key in FIELDS if isinstance(record.get(key), str)
                       and re.fullmatch(r'[A-Za-z0-9_:./@-]{1,250}', record[key])},
            'unsaved': value.get('unsaved') is True}
