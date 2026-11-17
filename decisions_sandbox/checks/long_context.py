"""Long context (requirement, 2026-10-13). One question over invented
makeup logistics of growing length: in which room is one section's makeup for one quiz? The notes give a room for many other
sections and quizzes, so the answer is the one line that names both; half the cases leave that line out ("not stated").
Lengths run from what every model reads to most of Snap's window, and a model that refuses a length is recorded as failed.

Everything here is made up: no course, section, room booking or student is real, and nothing comes from the catalog."""
import random

from jev.service import agreed, both, noul

from ..cases import Case, choice_view, label, noul_p, status

RULE = 'sandbox-long-context-v1'
SIZES = (20_000, 100_000, 400_000, 1_000_000)  # characters of notes; about 4 to a token
DEPTHS = (.1, .5, .9)
ROOMS = ('BLOC 164', 'BLOC 166', 'HELD 111', 'HELD 113', 'ILCB 229', 'ZACH 244', 'HEB 137', 'BLOC 150')
TOPICS = ('row reduction', 'matrix inverses', 'determinants', 'linear programming', 'the simplex method', 'probability rules',
          'conditional probability', 'Bayes\' theorem', 'expected value', 'the normal distribution', 'Markov chains',
          'simple interest', 'annuities', 'amortization', 'sets and counting', 'permutations', 'combinations', 'Leontief models')
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
ABSENT = 'not stated'
# One check per length, so the report reads by length.
CHECKS = {size: f'long_context_{size // 1000:04d}k' for size in SIZES}


def _line(section, quiz, room, rng):
    return (f'Section {section}: the makeup for Quiz {quiz} is held in {room} on {rng.choice(DAYS)} at '
            f'{rng.choice((8, 9, 10, 11, 1, 2, 3, 4))}:{rng.choice(("00", "10", "35", "50"))}.')


def _filler(section, rng):
    topic, other = rng.sample(TOPICS, 2)
    return (f'Section {section}, week {rng.randint(1, 15)}: class covered {topic} with {rng.randint(2, 6)} worked examples; '
            f'homework {rng.randint(1, 30)} is due {rng.choice(DAYS)} and reviews {other}. '
            f'Pages {rng.randint(10, 480)} to {rng.randint(481, 620)} of the notes go with it.')


def notes(size, seed, depth=None):
    """(the notes as a list of lines, the section and quiz asked about, the room or None). `depth` places the one line that
    answers, as a share of the way through; None leaves it out. No other line names that section with that quiz."""
    rng = random.Random(seed)
    section, quiz = rng.randint(501, 560), rng.randint(1, 12)
    room = rng.choice(ROOMS)
    lines, length = [], 0
    while length < size:
        other_section, other_quiz = rng.randint(501, 560), rng.randint(1, 12)
        if rng.random() < .3 and (other_section, other_quiz) != (section, quiz):
            line = _line(other_section, other_quiz, rng.choice(ROOMS), rng)
        else:
            line = _filler(other_section, rng)
        lines.append(line)
        length += len(line) + 1
    if depth is not None:
        lines.insert(int(len(lines) * depth), _line(section, quiz, room, rng))
    return lines, section, quiz, room if depth is not None else None


def questions(section, quiz):
    which = f'the makeup for Quiz {quiz} of section {section}'
    return {'stated': noul(f'Do the notes say in which room {which} is held?',
                           {'true': 'One line names that section and that quiz with a room.',
                            'false': 'No line names both that section and that quiz.'}),
            **both('room', f'In which room is {which} held, according to the notes?',
                   {**{room: None for room in ROOMS}, ABSENT: 'The notes give no room for that section\'s makeup of that quiz.'})}


def _run(size, seed, depth):
    def run(service):
        lines, section, quiz, _ = notes(size, seed, depth)
        asked = questions(section, quiz)
        receipt = service.ask('sandbox-long-context', f'{size}:{seed}', {'notes': lines}, asked, RULE)
        outcome = {'status': status(receipt)}
        if receipt.get('status') == 'complete':
            pick, confidence = agreed(receipt['answers'], 'room')
            outcome.update(room=pick or 'orders differ', room_orders_agree=pick is not None)
            return {'outcome': outcome, 'p': {'stated': noul_p(receipt, 'stated'), 'room_confidence': confidence,
                                              'room_top': choice_view(receipt, 'room')[1]}}
        return {'outcome': outcome, 'p': {}}
    return run


def invented():
    built = []
    for size in SIZES:
        for number, depth in enumerate((*DEPTHS, None, None, None)):
            seed = size + number
            _, _, _, room = notes(1000, seed, depth)  # the asked section, quiz and room depend on the seed alone
            built.append(Case(CHECKS[size], f'long-{size // 1000}k-{number}', 'invented', _run(size, seed, depth),
                              {'stated': label(int(depth is not None)), 'room': label(room or ABSENT)}, {'depth': depth}))
    return built


def real(ctx):
    return []
