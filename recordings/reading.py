"""The reading of a transcript (docs/decisions/recordings.md, RECORDINGS "The reading (step 3)"): what a model is asked, and what
of its answer survives. Pure: no model is called here, nothing is stored (recordings/service.py does both).

A TRANSCRIPT IS EVIDENCE, NOT AN INSTRUCTION. Faculty reads the transcript itself; Plaud's summary is never read for this. The
engine (the Assistant's own, as Compute settings name it) is told `grading/prompts/recording_reading-v3.txt` and given the transcript's lines, each with its number, how far in it is and who spoke, and the FACTS (which speaker is the
instructor, who the coordinator is, the term's sections and class days, the calendar three weeks either side, the assessments
and their given dates). It answers with ITEMS, each one sentence resting on an exact QUOTE at a stated line.

THE QUOTE CHECK is code, never a model: an item is kept only when its quote is in the transcript, word for word, beginning at
the line it names (it may run on into the next lines of the same voice). Who spoke it and when are then taken from the
transcript, never from the model. Anything else is DROPPED and recorded as dropped with the reason. No item is raised from talk
before the meeting begins (the model says where it begins; an item before that line is dropped). What an item says it concerns
must be something the facts named; otherwise that part is left off and the item stands on its quote alone.

A CLASS (step 5, RECORDINGS "Classes") is read by its own instructions for two kinds of item and no other: `covered`, a section
of the course's notes the class worked through, named by its NUMBER, and `announced`, a day the instructor told the class.
Neither is about a person: an item that names no notes section the course has, or no day, is dropped, and so is one whose
sentence holds a student's name.

A MEMO (step 6, RECORDINGS "Memos") is the instructor dictating alone, read by its own instructions for two kinds of item: a
`todo`, which in a memo is a REQUEST (what they asked to have done; it goes to the Assistant, recordings/memos.py), and a
`policy`, a standing rule they laid down (kept only by their own Remember). It has no meeting to begin and no facts to concern:
an item is its sentence and the exact words it rests on.
"""
import datetime as dt
import hashlib
import json
import re

VERSION = "reading-v3"   # v2 (2026-11-13): fewer, merged items; Canvas names from the facts; the gradebook only for drops.
#                          v3 (2026-11-13): an assessment the facts do not list keeps the NAME it was called by
KINDS = ("todo", "answer", "date", "ruling", "policy", "waiting")   # what a meeting is read for; a class has its own two (below)
FOR = ("you", "coordinator", "others", "unclear")
ABOUT = ("assessment", "rubric", "seating", "canvas", "testing_center", "calendar", "gradebook", "none")
SPAN = 4            # a quote may run over this many lines of one voice
QUOTE_WORDS = 3     # a quote of fewer words is nothing to rest an item on
MAX_ITEMS = 120     # the most items one reading keeps (a 70-minute meeting with a rubric discussion had about 40)
MAX_GIVEN = 400     # ... and the most of an answer that is looked at at all (the rest is not even recorded as dropped)
MAX_DROPPED = 100   # ... and the most dropped items a run records (each with its reason)
SENTENCE = 300

PROMPT = "recording_reading"   # grading/prompts/recording_reading-v<N>.txt, N from the providers' VERSIONS: what the engine is told (its checksum is kept with a run)
# Step 4, A STUDENT'S MEETING: its own instructions (what was promised, by whom, and what the student told the instructor),
# the same answer and the same quote check. Never a ruling or a standing rule: a meeting with one student sets neither.
STUDENT_PROMPT = "recording_student_meeting"   # grading/prompts/recording_student_meeting-v<N>.txt
STUDENT_VERSION = "student-meeting-v1"
STUDENT_KINDS = ("todo", "waiting", "date", "answer")
# Step 5, A CLASS: its own instructions and only two kinds of item, neither about a person: `covered` (a notes section the
# class worked through, by its NUMBER, as CLASS SCHEDULE topics name one) and `announced` (a day the instructor told the class).
CLASS_PROMPT = "recording_class"   # grading/prompts/recording_class-v<N>.txt
CLASS_VERSION = "class-v1"
CLASS_KINDS = ("covered", "announced")
CLASS_ABOUT = ("notes", "work", "class", "none")
# Step 6, A MEMO: its own instructions and two kinds: `todo` (in a memo: a request, put to the Assistant) and `policy` (a rule
# the instructor laid down; Remember). Its answer is the items alone: a memo has no talk before it begins.
MEMO_PROMPT = "recording_memo"   # grading/prompts/recording_memo-v<N>.txt
MEMO_VERSION = "memo-v1"
MEMO_KINDS = ("todo", "policy")
EVERY_KIND = KINDS + CLASS_KINDS
NOTES_NUMBER = re.compile(r"\d{1,2}\.\d{1,2}")   # a notes section's number, as scheduling.class_schedule.NUMBER reads one
MAX_NOTES = 8       # the notes sections one `covered` item names (a class day's topics hold as many)
_TEXT = {"type": "string"}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["begins_at_line", "items"], "properties": {
    "begins_at_line": {"type": "integer"},
    "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
              "required": ["kind", "sentence", "quote", "line", "for", "date", "time", "date_words", "happening", "about"],
              "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "sentence": _TEXT, "quote": _TEXT, "line": {"type": "integer"},
                             "for": {"type": "string", "enum": list(FOR)}, "date": _TEXT, "time": _TEXT, "date_words": _TEXT,
                             "happening": {"type": "boolean"},
                             "about": {"type": "object", "additionalProperties": False, "required": ["what", "assessment", "question", "words", "final"],
                                       "properties": {"what": {"type": "string", "enum": list(ABOUT)}, "assessment": _TEXT, "question": _TEXT,
                                                      "words": _TEXT, "final": {"type": "boolean"}}}}}}}}
CLASS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["begins_at_line", "items"], "properties": {
    "begins_at_line": {"type": "integer"},
    "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
              "required": ["kind", "sentence", "quote", "line", "date", "time", "date_words", "happening", "about"],
              "properties": {"kind": {"type": "string", "enum": list(CLASS_KINDS)}, "sentence": _TEXT, "quote": _TEXT, "line": {"type": "integer"},
                             "date": _TEXT, "time": _TEXT, "date_words": _TEXT, "happening": {"type": "boolean"},
                             "about": {"type": "object", "additionalProperties": False, "required": ["what", "notes", "work"],
                                       "properties": {"what": {"type": "string", "enum": list(CLASS_ABOUT)},
                                                      "notes": {"type": "array", "items": _TEXT}, "work": _TEXT}}}}}}}


MEMO_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {
    "items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["kind", "sentence", "quote", "line"],
              "properties": {"kind": {"type": "string", "enum": list(MEMO_KINDS)}, "sentence": _TEXT, "quote": _TEXT, "line": {"type": "integer"}}}}}}


def _plain(value):
    return " ".join(str(value or "").split())


FILLER = frozenset(("the", "a", "an", "of", "for", "to", "on", "in", "and", "our", "this", "that"))   # words that name nothing


NUMBERS = {word: str(number) for number, word in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(), 1)}
NUMBERS.update({"ii": "2", "iii": "3", "iv": "4"})   # "Exam III" is how this course's own Canvas pages write it


def name_words(text, apart=()):
    """The words of a name or a title, small letters, without the words that name nothing and the ones given `apart` (the
    course's own code: every title of the course holds it). A number said as a word is its figure ("exam three" is "exam 3")."""
    return [NUMBERS.get(word, word) for word in re.findall(r"[a-z0-9]+", str(text or "").casefold()) if word not in FILLER and word not in apart]


def _same(word, other):
    """A word and its plural or longer form are one word ("exam", "exams", "examination"); a number is only itself."""
    return word == other or (min(len(word), len(other)) >= 4 and (word.startswith(other) or other.startswith(word)))


def named(name, titles, apart=()):
    """The assessments a meeting meant by `name`, among `titles` ({id: title}), as a list of ids: the ones whose title the name
    holds EVERY word of, where those words are at least half of the name ("final exam" is "Final"; "final exam review" and
    "final answer on question 20" are not), and of those only the longest title ("quiz 2" is "Quiz 2", never also "Quiz").
    Several ids only when their titles are the same words. [] when none is: nothing is guessed, and the item stays held under
    the name it was called by until an assessment of that name exists."""
    said = name_words(name, apart)
    scored = {}
    for identity, title in (titles or {}).items():
        wanted = name_words(title, apart)
        if wanted and said and all(any(_same(word, other) for other in said) for word in wanted) and len(wanted) * 2 >= len(said):
            scored[identity] = len(wanted)
    best = max(scored.values(), default=0)
    return [identity for identity, score in scored.items() if score == best]


def key(kind, quote):
    """An item's identity within its recording: its kind and its quote, so the same item read again is the same item."""
    return hashlib.sha256(f"{kind}\n{_plain(quote).casefold()}".encode("utf-8")).hexdigest()


def clock(ms):
    seconds = max(0, int(ms)) // 1000
    hours, minutes, seconds = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def payload(lines, facts):
    """What one reading sends beside its instructions: the facts, and every line as "[n] m:ss Speaker: words". `lines` are the kept
    transcript's ({line, start_ms, speaker, text}), `facts` what the service read from the owning services."""
    return {"facts": facts,
            "transcript": "\n".join(f"[{line['line']}] {clock(line['start_ms'])} {line['speaker'] or 'Unnamed speaker'}: {line['text']}" for line in lines)}


def checksum(value):
    """The checksum of what was sent (a payload) or of a text."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def answer(text):
    """The JSON object in a model's answer (it may wrap it in a code fence or a sentence), or None."""
    text = str(text or "")
    for candidate in (text, *re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)):
        try:
            found = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(found, dict):
            return found
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            found = json.loads(text[start:end + 1])
        except ValueError:
            return None
        return found if isinstance(found, dict) else None
    return None


def _day(value, around):
    """A day the model gave, if it is a real date within a year of the recording's; else ''."""
    try:
        day = dt.date.fromisoformat(str(value or ""))
    except ValueError:
        return ""
    return day.isoformat() if abs((day - around).days) <= 366 else ""


def _clock(value):
    """A clock time the model gave as HH:MM, else ''."""
    return value if isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) else ""


def _about(given, facts):
    """What an item concerns, kept only where the facts named it."""
    given = given if isinstance(given, dict) else {}
    what = given.get("what") if given.get("what") in ABOUT else "none"
    known = {str(item["id"]) for item in facts.get("assessments") or []}
    found = {"what": what}
    if what in ("assessment", "rubric", "seating", "testing_center"):
        if str(given.get("assessment") or "") not in known:
            # An assessment the facts do not list (or one the reading did not match to its listing) keeps the NAME the meeting
            # called it by: what is said of its grading is offered at the rubric of the assessment that name is, by code
            # (`named`), also one made after the meeting. A seating chart or a booking has no record to look in by a name.
            name = _plain(given.get("words"))[:80]
            if what not in ("assessment", "rubric") or not name:
                return {"what": "none"}
            found["assessment"], found["words"] = "", name
        else:
            found["assessment"] = str(given["assessment"])
        if what == "assessment":
            found["question"] = _plain(given.get("question"))[:40]
        if what == "rubric":
            found["final"] = given.get("final") is True
    elif what == "canvas":
        found["words"] = _plain(given.get("words"))[:80]
        if not found["words"]:
            return {"what": "none"}
    elif what == "calendar":
        found["words"] = _plain(given.get("words"))[:80]   # a short name for the entry a card may propose
    return found


def _class_about(kind, given, facts, dated):
    """(what a class item concerns, '') or (None, why it is dropped). `covered` names notes sections by NUMBER, only ones the
    course's notes have (where Faculty holds an outline); `announced` names a day, and what it is the day of only where the
    facts listed it: a piece of the section's work by its id, or the class itself (it meets, or does not, that day)."""
    given = given if isinstance(given, dict) else {}
    if kind == "covered":
        outline = {str(entry.get("number")) for entry in facts.get("notes") or [] if isinstance(entry, dict)}
        named = [number.strip() for number in given.get("notes") or [] if isinstance(number, str)] if isinstance(given.get("notes"), list) else []
        numbers = list(dict.fromkeys(number for number in named if NOTES_NUMBER.fullmatch(number) and (not outline or number in outline)))[:MAX_NOTES]
        return ({"what": "notes", "notes": numbers}, "") if numbers else (None, "it names no section of the course's notes")
    if not dated:
        return None, "it names no day"
    what = given.get("what") if given.get("what") in ("work", "class") else "none"
    if what == "work":
        known = {str(entry.get("id")) for entry in facts.get("work") or [] if isinstance(entry, dict)}
        return ({"what": "work", "work": str(given["work"])}, "") if str(given.get("work") or "") in known else ({"what": "none"}, "")
    return {"what": what}, ""


def _names_a_student(sentence, names):
    """Whether a sentence holds a current student's WHOLE name or a UIN, as a memory's own roster check reads one
    (assistant/memory.py `roster_check`, strong). A given or family name alone is not judged here: section titles and
    ordinary words are spelled like some ("Power", "Grant"), and the instructions already keep people out of a class's items."""
    from assistant.memory import roster_check
    return roster_check(sentence, list(names))[0] == "strong"


def located(quote, start, lines):
    """(first, last) places in `lines` of a quote that BEGINS in line number `start` and is there word for word (spaces apart),
    running on over at most SPAN lines of the same voice; None when it is not. A quote that is only in a later line is not at
    the line it names: its item would play the wrong moment, and could be talk from before the line the reading gave."""
    index = next((n for n, line in enumerate(lines) if line["line"] == start), None)
    wanted = _plain(quote)
    if index is None or not wanted:
        return None
    first, joined = len(_plain(lines[index]["text"])), ""
    for last in range(index, min(index + SPAN, len(lines))):
        if lines[last]["speaker_key"] != lines[index]["speaker_key"]:
            break
        joined = _plain(f"{joined} {lines[last]['text']}")
        at = joined.find(wanted)
        if at != -1:
            return (index, last) if at < first else None
    return None


def checked(found, lines, facts, day, kinds=KINDS, names=(), most=MAX_ITEMS):
    """(begins, items, dropped) of a model's parsed answer: what survives THE QUOTE CHECK, each item with who spoke and when
    taken from the transcript, and what was dropped with the reason. `day` is the recording's campus date. `kinds`: the kinds
    this meeting is read for (a student's meeting: `STUDENT_KINDS`; a class: `CLASS_KINDS`); any other is dropped and said so.
    `names`: the current students' names, given for A CLASS (step 5): an item whose sentence holds a student's whole name is
    dropped, and so is a day told to the class whose QUOTE holds one (it was said to that student), since a class recording
    raises nothing about one student; neither its sentence nor its quote is recorded with the reason. `most`: the most items
    this reading keeps (a memo's is small: recordings/memos.py)."""
    items, dropped, seen = [], [], set()
    numbers = [line["line"] for line in lines]
    begins = found.get("begins_at_line")
    begins = begins if isinstance(begins, int) and not isinstance(begins, bool) and begins in numbers else (numbers[0] if numbers else 0)
    given = found.get("items") if isinstance(found.get("items"), list) else []
    for place, item in enumerate(given[:MAX_GIVEN]):
        def drop(reason):
            dropped.append({"place": place, "reason": reason, "kind": str(item.get("kind") or "")[:20] if isinstance(item, dict) else "",
                            "sentence": _plain(item.get("sentence"))[:SENTENCE] if isinstance(item, dict) else "",
                            "quote": _plain(item.get("quote"))[:500] if isinstance(item, dict) else ""})

        if not isinstance(item, dict):
            drop("not an item")
            continue
        kind, sentence, quote, line = item.get("kind"), _plain(item.get("sentence")), _plain(item.get("quote")), item.get("line")
        if kind not in EVERY_KIND:
            drop("not a kind of item Faculty reads")
        elif kind not in kinds:
            drop("not a kind of item this meeting is read for")
        elif not sentence:
            drop("no sentence")
        elif len(quote.split()) < QUOTE_WORDS:
            drop("its quote is too short to rest on")
        elif not isinstance(line, int) or isinstance(line, bool) or line not in numbers:
            drop("its line is not in the transcript")
        elif line < begins:
            drop("said before the meeting began")
        elif len(items) >= most:
            drop("more items than one reading keeps")
        else:
            where = located(quote, line, lines)
            identity = key(kind, quote)
            if where is None:
                drop("its quote is not in the transcript at that line")
            elif identity in seen:
                drop("the same quote twice")
            else:
                said, lesson = _day(item.get("date"), day), kind in CLASS_KINDS
                about, why = _class_about(kind, item.get("about"), facts, bool(said)) if lesson else (_about(item.get("about"), facts), "")
                if about is None:
                    drop(why)
                    continue
                if lesson and names and (_names_a_student(sentence, names) or (kind == "announced" and _names_a_student(quote, names))):
                    # Not even its words are kept as dropped: what was left out is shown on the page too.
                    dropped.append({"place": place, "reason": "it names a student: a class recording raises nothing about one student",
                                    "kind": kind, "sentence": "", "quote": ""})
                    continue
                seen.add(identity)
                first, last = lines[where[0]], lines[where[1]]
                dated = said and kind != "covered"   # what a class worked through is of the class day itself
                items.append({"key": identity, "kind": kind, "sentence": sentence[:SENTENCE], "quote": quote[:2000],
                              "line": first["line"], "line_end": last["line"], "start_ms": first["start_ms"],
                              "end_ms": max(first["start_ms"], last["end_ms"]), "speaker_key": str(first["speaker_key"] or "")[:200],
                              "for": "you" if lesson else item.get("for") if item.get("for") in FOR else "unclear",
                              "date": said if dated else "", "time": _clock(item.get("time")) if dated else "",
                              "date_words": _plain(item.get("date_words"))[:80] if dated or not lesson else "",
                              "happening": item.get("happening") is not False or kind not in ("date", "announced"),
                              "about": about})
    return begins, items, dropped[:MAX_DROPPED]
