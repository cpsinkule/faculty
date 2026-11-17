"""Parse DR bookings as literal records, with local dates/times and stable exam names."""
import re
from datetime import datetime

from accommodation_import import read_export, student_fields

REQUIRED = {'last name','first name','uin','subject','course','section','exam type',
            'date','start time','end time','length','campus','requested on'}
# An accommodation export has Last Updated and no Exam Type (message-only hint for the wrong file).
ACCOMMODATION_SIGNATURE = {'last updated'}
ACCOMMODATION_HINT = 'This looks like an accommodation export. Use Import accommodations.'


def calendar_date(value, line, field):
    for pattern in ('%Y-%m-%dT%H:%M:%S','%Y-%m-%d','%m/%d/%Y'):
        try:
            parsed = datetime.strptime(value.strip(), pattern)
            if parsed.hour or parsed.minute or parsed.second:
                break
            return parsed.date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Row {line}: {field} must be an Excel date or an ISO/US date.')


def clock_time(value, line, field):
    for pattern in ('%I:%M %p','%I:%M%p','%H:%M','%H:%M:%S'):
        try:
            parsed = datetime.strptime(value.strip().upper(), pattern)
            if parsed.second:
                break
            return parsed.strftime('%H:%M')
        except ValueError:
            pass
    raise ValueError(f'Row {line}: {field} must be a time such as 07:05 PM or 19:05.')


def booking_key(exam, date):
    """The exam a booking names. AIM's Exam Type "Quiz" names no particular quiz: every quiz date the DR center lists is booked as
    another "Quiz" row (SCHEDULING "SEND TO AIM" adds one per quiz), so a quiz booking is that quiz date's (ACCOMMODATIONS
    "QUIZ BOOKINGS BY DATE"). Every other exam type names one exam, whatever its date."""
    return f'{exam.casefold()} {date}' if exam.casefold() == 'quiz' else exam.casefold()


def parse_dr_exams(content, filename):
    headers, source = read_export(content, filename, REQUIRED, 'DR exam', other=(ACCOMMODATION_SIGNATURE, ACCOMMODATION_HINT))
    rows, seen = [], {}
    for line, original in source:
        row = student_fields(original, line)
        fields = {k.casefold():v for k,v in original.items()}
        exam = ' '.join(fields['exam type'].split())
        if not exam or len(exam) > 150:
            raise ValueError(f'Row {line}: supply an Exam Type of at most 150 characters.')
        if not re.fullmatch(r'[0-9]{1,4}', fields['length'].strip()) or not 1 <= int(fields['length']) <= 1440:
            raise ValueError(f'Row {line}: Length must be 1–1440 minutes.')
        snapshot = {'exam_type':exam,'date':calendar_date(fields['date'], line, 'Date'),
                    'start_time':clock_time(fields['start time'], line, 'Start Time'),
                    'end_time':clock_time(fields['end time'], line, 'End Time'),
                    'length_minutes':int(fields['length']),'campus':fields['campus'].strip(),
                    'requested_on':calendar_date(fields['requested on'], line, 'Requested On'),
                    'source_fields':original}
        exam_key = booking_key(exam, snapshot['date'])
        key = (row['uin'], row['code'], row['section'], exam_key)
        if key in seen:
            first, kept = seen[key]
            if kept != snapshot:
                differs = ', '.join(name for field, name in (('date','Date'),('start_time','Start Time'),('end_time','End Time'),
                    ('length_minutes','Length'),('campus','Campus'),('requested_on','Requested On')) if kept[field] != snapshot[field]) or 'another column'
                raise ValueError(f"Row {line} conflicts with row {first}: {row['name']} has two {exam} bookings in {row['code']} section "
                                 f"{row['section']} that differ in {differs}. Export one current booking per exam.")
            continue
        seen[key] = (line, snapshot)
        row.update(exam_key=exam_key, snapshot=snapshot)
        rows.append(row)
    return {'headers':headers,'rows':rows}
