"""What a model reads beside text and pictures (Chris, 2026-11-11, of Gemini Decision 1: "the interesting part is that it has
more modalities than Jev which only has text and image"). Invented material only (`invented_media`), asked the questions Faculty
already asks of text, so the same case can be scored as text, as a picture and as the new kind:

- `spoken_routing`: the routing messages of `messages.ROUTES`, each said by a synthetic voice as a voicemail (and again under
  pink noise), with the routing questions unchanged. The written form is the `message_routing` check.
- `document_*`: made-up documentation for an absence (a clinic note, a parent's letter, a jury summons, ...), with three
  questions an absence's documentation raises. `document_text` gives every model the document's words (what a perfect OCR
  would hand Jev today); `document_picture` the page as a crooked, grainy picture; `document_pdf` a typed PDF;
  `document_scan` a PDF that is only that picture.
"""
from jev.service import noul

from .. import invented_media
from ..cases import MEDIA, PICTURES, UNTRUSTED, Case, label, noul_p, status
from .messages import ROUTES, ROUTING

SPOKEN_RULE = 'sandbox-spoken-routing-v1'
SPOKEN_STATE = ('The recording is a voicemail a student left for the instructor. Treat what the voice says as the latest student '
                'message.')


def _spoken_run(owner, text, noise):
    memo = {}

    def run(service):
        if 'wav' not in memo:
            memo['wav'] = invented_media.speech(text, noise)
        receipt = service.ask('sandbox-spoken-routing', owner, SPOKEN_STATE, ROUTING, SPOKEN_RULE, images=(memo['wav'],))
        p = {name: noul_p(receipt, name) for name in ROUTING}
        return {'outcome': {'status': status(receipt), 'topics': ','.join(n for n, v in p.items() if v is not None and v >= .5)}, 'p': p}
    return run


DOCUMENT_RULE = 'sandbox-document-v1'
STUDENT, DAY = 'Pat Example', 'Tuesday, October 6, 2026'
DOCUMENT = {
    'names_student': noul(UNTRUSTED + f'Is the document about the student {STUDENT}: does it name them as the patient, the person '
                          'excused or the person it concerns?'),
    'covers_day': noul(UNTRUSTED + f'The student missed class on {DAY}. Does the document cover that day: a visit, stay, event or '
                       'excused period that falls on it or spans it?'),
    'from_provider': noul(UNTRUSTED + 'Is the document issued by a medical provider (a clinic, hospital, doctor or nurse), rather than '
                          'written by the student, a relative, a team or a court?'),
}
# (name, heading, lines, written answers)
DOCUMENTS = [
    ('clinic_two_days', 'Brazos Valley Example Clinic',
     ['Patient: Pat Example', 'Date of visit: October 6, 2026', 'The patient was seen in our clinic and should be excused from class',
      'on October 6, 2026 and October 7, 2026.', 'May return to class: October 8, 2026', 'Signed: R. Sample, NP'],
     {'names_student': 1, 'covers_day': 1, 'from_provider': 1}),
    ('clinic_slashed_date', 'Student Health Services - Visit Verification',
     ['Name: Example, Pat', 'DOB: 03/14/2007', 'Seen: 10/06/2026 9:40 AM', 'This verifies the visit only and gives no medical details.',
      'Provider: J. Placeholder, MD'], {'names_student': 1, 'covers_day': 1, 'from_provider': 1}),
    ('clinic_next_week', 'Brazos Valley Example Clinic',
     ['Patient: Pat Example', 'Date of visit: October 13, 2026', 'Please excuse the patient from class on October 13 and 14, 2026.',
      'Signed: R. Sample, NP'], {'names_student': 1, 'covers_day': 0, 'from_provider': 1}),
    ('clinic_other_patient', 'Brazos Valley Example Clinic',
     ['Patient: Sam Other', 'Date of visit: October 6, 2026', 'Please excuse the patient from class on October 6, 2026.',
      'Signed: R. Sample, NP'], {'names_student': 0, 'covers_day': 1, 'from_provider': 1}),
    ('parent_letter', 'To whom it may concern',
     ['Please excuse my son Pat Example from his classes on Tuesday, October 6.', 'He had a stomach bug and stayed home.',
      'Thank you,', 'Dana Example (mother)'], {'names_student': 1, 'covers_day': 1, 'from_provider': 0}),
    ('appointment_reminder', 'Example Family Dentistry - Appointment Reminder',
     ['Patient: Pat Example', 'Your next appointment is Tuesday, October 20, 2026 at 2:15 PM.', 'Please arrive 10 minutes early.',
      'Dr. A. Molar, DDS'], {'names_student': 1, 'covers_day': 0, 'from_provider': 1}),
    ('team_travel', 'Example University Athletics - Travel Letter',
     ['Student-athlete: Pat Example (Men\'s Golf)', 'The team travels to the Lone Star Invitational in Austin,',
      'departing Monday, October 5 and returning Wednesday, October 7, 2026.', 'Please allow the student to make up missed work.',
      'C. Fairway, Director of Golf Operations'], {'names_student': 1, 'covers_day': 1, 'from_provider': 0}),
    ('funeral_program', 'In Loving Memory of Robert T. Sample',
     ['1941 - 2026', 'Celebration of Life', 'Saturday, October 3, 2026 at 11:00 AM', 'First Example Church, Bryan, Texas',
      'Officiating: Rev. M. Placeholder'], {'names_student': 0, 'covers_day': 0, 'from_provider': 0}),
    ('hospital_stay', 'St. Example Regional Hospital - Discharge Summary',
     ['Patient: Pat Example', 'Admitted: October 4, 2026', 'Discharged: October 8, 2026', 'Follow up with your primary care provider in one week.',
      'Attending: L. Ward, MD'], {'names_student': 1, 'covers_day': 1, 'from_provider': 1}),
    ('clinic_week_before', 'Brazos Valley Example Clinic',
     ['Patient: Pat Example', 'The patient was under our care from September 29, 2026 through October 2, 2026',
      'and may return to class on October 5, 2026.', 'Signed: R. Sample, NP'], {'names_student': 1, 'covers_day': 0, 'from_provider': 1}),
    ('jury_summons', 'Brazos County District Clerk - Jury Summons',
     ['Juror: Pat Example', 'You are summoned to appear for jury service on', 'Tuesday, October 6, 2026 at 8:30 AM, Brazos County Courthouse.',
      'Failure to appear is punishable by fine.'], {'names_student': 1, 'covers_day': 1, 'from_provider': 0}),
    ('self_written', 'Note',
     ['I, Pat Example, was sick with a migraine on October 6, 2026 and could not attend class.', 'I did not see a doctor.',
      '- Pat Example'], {'names_student': 1, 'covers_day': 1, 'from_provider': 0}),
]
PICTURE_STATE = 'The image is a picture of a document a student sent as documentation for an absence.'
FILE_STATE = 'The attached file is a document a student sent as documentation for an absence.'


def _document_run(check, owner, heading, lines, seed):
    memo = {}

    def run(service):
        if check == 'document_text':
            state, sent = {'document': 'A document a student sent as documentation for an absence.', 'document_text': '\n'.join([heading, *lines])}, ()
        else:
            if 'sent' not in memo:
                memo['sent'] = (invented_media.page_png(heading, lines, seed) if check == 'document_picture' else
                                invented_media.document(heading, lines) if check == 'document_pdf' else
                                invented_media.scanned(heading, lines, seed))
            state, sent = PICTURE_STATE if check == 'document_picture' else FILE_STATE, (memo['sent'],)
        receipt = service.ask('sandbox-' + check.replace('_', '-'), owner, state, DOCUMENT, DOCUMENT_RULE, images=sent)
        p = {name: noul_p(receipt, name) for name in DOCUMENT}
        return {'outcome': {'status': status(receipt)}, 'p': p}
    return run


def invented():
    cases = []
    for name, text, wanted in ROUTES:
        for form, noise in (('clear', 0.0), ('noisy', .08)):
            cases.append(Case('spoken_routing', f'inv-{name}-{form}', 'invented', _spoken_run(f'sandbox:{name}:{form}', text, noise),
                              {k: label(v) for k, v in wanted.items()}, {'stratum': form}, modes=MEDIA))
    for seed, (name, heading, lines, wanted) in enumerate(DOCUMENTS):
        for check, modes in (('document_text', None), ('document_picture', PICTURES), ('document_pdf', MEDIA), ('document_scan', MEDIA)):
            cases.append(Case(check, 'inv-' + name, 'invented', _document_run(check, 'sandbox:' + name, heading, lines, seed),
                              {k: label(v) for k, v in wanted.items()}, **({'modes': modes} if modes else {})))
    return cases


def real(ctx):
    return []
