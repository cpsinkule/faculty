"""Recordings, step 6: A MEMO (docs/decisions/recordings.md, RECORDINGS "Memos, training, search by meaning (step 6)";
ASSISTANT "A DICTATED REQUEST"). A memo is the instructor dictating alone. It is the one recording whose words ask for
something, so it is held to two things: what it asks is found by a READING whose every item rests on the exact words, and
each request then goes to the ASSISTANT as a background preparation, under the Assistant's own policy and narrower: it works
through the app's own routes, and EVERY change it wants waits as an Approve card, the kinds that are automatic in chat
included. Nothing follows from a memo by itself.

HOW FACULTY KNOWS THE WORDS ARE THE INSTRUCTOR'S (`voice`; code, never a model, and never the sound of a voice, which
Faculty does not judge):
  1. the recording is filed as a MEMO, which only the instructor does (the calendar never files one; the Assistant's filing
     is an Approve card);
  2. Plaud heard ONE voice in it;
  3. that voice is not named as somebody else: Plaud left it unnamed, or EVERY word of its name is a word of the
     workspace's own name ("Chris" for Chris Example; never "Chris Wong", and never a title the workspace's name lacks).
When all three hold, each request is put to the Assistant once, on its own. When one does not, Faculty is NOT SURE: the memo
is still read and its requests shown with their words, nothing is put to the Assistant, the page says why, and a request
goes only by the instructor's own press of Ask the Assistant on it.

This module is what the reading and the page share: the voice rule, and how a request's run by the Assistant stands (read
from the Assistant's own retained preparations, by reference: nothing of a run is copied here).
"""
import json
import re
import sqlite3

KIND = "memo"            # the Assistant's preparation kind of a dictated request; its target is the recording item
ASKS = 6                 # requests of one memo put to the Assistant on their own (the rest wait for the instructor's press)
ITEMS = 12               # the most items a memo's reading keeps (its instructions ask for at most six requests and four rules)
SAID = 4000              # characters of the memo's own lines a request's run is given as context
GENERIC = re.compile(r"(?:speaker|unknown|unnamed speaker)\s*\d*", re.I)   # how Plaud labels a voice nobody named
WORKING = ("queued", "running")
OFF = "The Assistant is off (Compute settings name no model for it), so this waits until it is on."
AUTO = "memo:auto:"      # the request ID of a run Faculty queued by itself; the instructor's press is `memo:<their request ID>`
NOT_SURE = "RECORDINGS: Faculty is no longer sure the words of this memo are yours, so what it asked the Assistant by itself is withdrawn."
MORE = f"Faculty puts a memo's first {ASKS} requests to the Assistant by itself. Press Ask the Assistant for this one."
NOT_YET = "Not put to the Assistant yet."
UNAVAILABLE = "The Assistant is not available here, so this request was not put to it."


def words(name):
    """The words of a name, small letters, each of two letters or more."""
    return {word for word in "".join(c if c.isalnum() else " " for c in str(name or "").casefold()).split() if len(word) > 1}


def voice(shown, instructor):
    """(whether Faculty is sure a memo's words are the instructor's, why it is not). `shown` is {a speaker's key: the name
    they go by} for the transcript's voices; `instructor` the workspace's own name. One voice, and that voice unnamed or
    named by words that are ALL the instructor's: a name that only shares a word with theirs is somebody else's."""
    if len(shown) != 1:
        return False, (f"Plaud heard {len(shown)} voices in this memo, so Faculty cannot tell that every word is yours. "
                       "Nothing was put to the Assistant: press Ask the Assistant on a request that is yours.")
    name = str(next(iter(shown.values())) or "").strip()
    if not name or GENERIC.fullmatch(name):
        return True, ""
    said, own = words(name), words(instructor)
    if said and said <= own:
        return True, ""
    if not own:
        return False, (f"The one voice in this memo is named “{name}”, and Faculty does not know your name (Workspace settings), so it "
                       "cannot tell that it is you. Nothing was put to the Assistant: press Ask the Assistant on a request that is yours.")
    return False, (f"The one voice in this memo is named “{name}”, which is not your name. Nothing was put to the Assistant: "
                   "name yourself among the speakers, or press Ask the Assistant on a request that is yours.")


def runs(db, items):
    """{recording item: how its latest run by the Assistant stands} for the requests of a memo: {id, state, detail,
    asked_at}. `state` is the preparation's own (`queued`, `running`, `ready`, `needs_input`, `failed`, `interrupted`,
    `stale`). Read from the Assistant's retained preparations; a catalog with no Assistant has none."""
    items = list(items)
    if not items:
        return {}
    try:
        rows = db.execute(f"""SELECT r.id, r.target, r.created_at, r.request_id, e.state, e.detail_json FROM assistant_preparations r
            JOIN assistant_preparation_events e ON e.id=(SELECT MAX(id) FROM assistant_preparation_events WHERE run_id=r.id)
            WHERE r.kind=? AND r.target IN ({','.join('?' * len(items))}) ORDER BY r.rowid""", (KIND, *items)).fetchall()
    except sqlite3.Error:
        return {}
    found = {}
    for row in rows:   # oldest first, so the latest run of each request stands
        found[row["target"]] = {"id": row["id"], "state": row["state"], "detail": json.loads(row["detail_json"] or "{}"), "asked_at": row["created_at"],
                                "auto": str(row["request_id"]).startswith(AUTO)}
    return found


def standing(run, sure, why, place, available=True):
    """How one request stands with the Assistant, as the page shows it: {state, why, run_id, asked_at, answer, question}.
    `unsent` when no run was made: Faculty was not sure of the voice (`why`), the request is past the memo's first ASKS, or
    the Assistant is not available here."""
    if run is None:
        reason = UNAVAILABLE if not available else why if not sure else MORE if place >= ASKS else NOT_YET
        return {"state": "unsent", "why": reason, "run_id": "", "asked_at": "", "answer": "", "question": None}
    detail = run["detail"]
    return {"state": run["state"], "why": "", "run_id": run["id"], "asked_at": run["asked_at"],
            "answer": str(detail.get("explanation") or detail.get("error") or "")[:8000], "question": detail.get("question") or None}


def needs_you(item):
    """Whether a memo's item needs the instructor now: a rule they said that is still open (Remember, Done or Dismiss), and a
    request that is open and that the Assistant is not working on (it waits on their press, or it was answered)."""
    if item["state"] != "open":
        return False
    return item["kind"] == "policy" or (item.get("ask") or {}).get("state") not in WORKING
