## Picture search

Rule ID: **PICTURE-SEARCH**. Current binding module contract. Read root `AGENTS.md` first, then EMBEDDING and INBOX.

- The instructor (2026-10-10), once the embedding worker was running: **"Can we have the embedding service also have the image
  capabilities and use it for the memory as well as retrieving things like scans/images/etc"**. Asked what to find, they chose
  scanned student papers, documents students send, course materials and files attached in chat; asked where, **"Assistant and
  a search box"**. Part one was scanned pages. After it was live they searched "inequalities": **"it brought up some papers
  that weren't really inequalities"**; then **"proceed with part 2 of the plan"**: documents students send and course
  materials, with Faculty's own words about a picture matched first. Shown what that returned and offered one result per
  printed page for its scans: **"do the scans and then part three"**. Of part three, **searching by a picture** is built (BY A
  PICTURE below), and with it a file attached in chat can be searched by; **remembering a picture** and **a picture finding
  memories** are ASSISTANT-MEMORY "PICTURES", whose kept pictures are a source here.
  The same day, of an empty page of the notes: **"this "front matter" page keeps showing up as the top hit for many different
  searches"** (A LABEL IS NOT WORDS below).
  `picture_search/` (index, sources, service, api), `static/picture-search.js`, `deploy/faculty-inbox.service.d/embedding.conf`,
  `tests/test_picture_search.py`, `tests/test_picture_search_sources.py`, `tests/test_picture_search.mjs`,
  `tests/browser_picture_search.py`.
- **WHY WORDS COME FIRST.** How a picture looks ranks it only loosely. The model tells a graph from a table from a bubble
  sheet, and it does take in what a page prints (measured 2026-10-11: a page's own question text found that page first
  among 70 blank pages for 38 pages of 41, and a student's transcribed answer found its own page first among 40 answers to
  the same question one time in ten, where chance is one in forty), but right and wrong pages score within a hundredth or
  two of each other, so among thousands of scans the nearest is often not it: on the live index "inequalities"
  ranked a demand-function page level with the page that prints a system of inequalities (requirement, 2026-10-10: pages
  that "weren't really inequalities"). So where Faculty already stores
  words about a picture, those words are embedded too and matched before any likeness. Since 2026-10-11 a picture is seen
  at 560 pieces, a Letter page at about 1,000 by 1,290 pixels (EMBEDDING "PICTURES AT 560 PIECES", the instructor's
  "switch to 560"; 280 pieces and 670 by 960 before): look tells pages apart better, and words still come first.
- **A LABEL IS NOT WORDS, AND AN EMPTY PAGE IS NOT A PICTURE.** The page the instructor kept getting was the fourth page of
  the notes' front matter: nothing is on it, so the only words Faculty had for it were its label, "Course notes Front
  matter". A label says what kind of thing a picture is and where it sits, which every page of its kind shares; alone it
  is near any vague description. Measured on the live index (2026-10-10, 448 worded things, 60 descriptions): 15 had a
  label and nothing else (that page, and 14 Exam 1 pages of the two versions uploaded as pictures, which hold no text and
  have no stored question). Each of the 15 was within `WORDS_NEAR` of 19 or 20 of 40 descriptions of other things
  ("syllabus", "a doctor's note", "student handwriting"); nothing else was within it of more than 6. Of the 33 of those
  descriptions that name nothing in the course, 16 reached the bar; with the 15 left out, 3 do ("an empty page", "a blank
  page", "page": one printed page's own short text), and each of 20 course topics keeps its best match (.718 to .825).
  By its look the empty page was no better: the nearest of 6,835 pictures to 6 of the 60 descriptions and
  among the nearest ten for 14, because an empty page looks like nothing in particular. So:
  - **Words are embedded only when Faculty stores something about that page beyond its label** (`sources.worded`): a question
    printed on it, a topic that starts on it, its own text, a caption. A page with a label alone has no words and is found
    by how it looks, as anything else Faculty has no words about; so are its scans, unless another version of the page has
    words.
  - **A picture with nothing on it is never indexed**, whatever its kind (`sources.blank`: the measure that calls a scanned
    side blank, fewer than one pixel in 10,000 darker than 230). The other pages of its file are still read. On the live
    course material the rule leaves out that one page; the next emptiest page has 40 times the bar's ink.
  - Both are true of the file or the words until they change, so neither is tried again in a week or counted as unreadable
    (`index.SETTLED`). `sources.WORDING` is part of the words' mark: what was worded the old way is read again, once.
- **SOURCES** (`sources.py`; each lists from the catalog alone, reads one file a turn, and applies its owner's own rule for
  what is gone, both when it lists and when it shows):

  | Kind (`source`) | What | Words | Left out |
  |---|---|---|---|
  | Scanned page (`scan`) | a scan's retained page picture | those of the page it was printed from, when its page is identified and has a blank copy | a scan deleted in the Inbox (INBOX `PRESENT`), a blank side, a page ignored, duplicate or rejected in review, an archived scan |
  | Printed page (`template`) | each page of the blank copies kept for grading (`assignment_templates`) | the assignment, the questions printed on the page (each question's latest revision) and the page's own text | nothing has a removed state; the same page of another version is one result |
  | Question bank (`bank`) | pictures kept with questions | the question's sections, the picture's caption, the question | a removed picture, a retired question's pictures |
  | Course notes (`notes`) | the pages of each course's current notes, drawn in the document sandbox | the section, its chapter and topics, and the page's own text | a removed edition, an edition that is not the newest standing |
  | Absence documentation (`absence`) | a picture, or the first four pages of a PDF | none | a document removed from its absence, a deleted term, an upload whose section is not on the working drive |
  | From a student (`mail`) | pictures and PDFs (first four pages) attached to a message a student wrote, in the communications record: every one, once, where it was first sent | none | a file an absence already holds as documentation (in every copy), a later copy of a file already sent, what the instructor sent first, a picture under 8 KB (an icon), anything not kept |
  | Remembered picture (`memory`) | a picture kept with one of the Assistant's memories (ASSISTANT-MEMORY "PICTURES") | what the instructor said about it, their own typed sentence | a memory that no longer stands; an erased picture leaves the index at the next tidy |

  **EVERY ATTACHMENT A STUDENT SENT, ONCE** (requirement, 2026-10-11: "I want all attachments from students to be indexed,
  regardless of student ID presence", "and doctor's notes"). Until then a picture on more than one message was taken for a
  signature's logo and left out. Read on the live record that day: 14 pictures were on several messages, each within one
  thread, because a reply carries the pictures of the message it answers; none was a logo (108 KB to 4.9 MB photographs and
  screenshots), and 19 of the 104 attachments students had sent were missing for it. So the same bytes are one attachment,
  one result, shown where they were first sent; a file whose first copy is on a message the instructor wrote is the
  instructor's, whoever's reply carries it back; and a file an absence holds is that absence's in every copy (it was
  listed twice when a student sent a PDF two times). Nothing is left out for what it shows or whose it is: no picture is
  read to choose, so a name, an ID number or a card decides nothing. Doctor's notes kept as an absence's documentation were
  all in already (37 standing documents, 40 pages). What the record does not keep cannot be indexed: mail Faculty cannot
  tie to a student is not kept (COMMUNICATIONS), and a video is not a picture. A picture or PDF a student linked from
  Google Drive is one of these attachments once Faculty has fetched it (COMMUNICATIONS "LINKED DRIVE FILES", 2026-10-11).

  Not sources: student ID photos (PHOTOS: never embedded), the Mail page (MAIL keeps nothing it reads), the Inbox's other
  submissions (the instructor's own files), a bank PDF's page pictures (the GET that makes them writes), assignment versions
  and quiz drafts (their pages are the printed pages once a template exists), files attached in chat (scratch for a day:
  searched by, never indexed), and recordings (not pictures: what was said is found by RECORDINGS' own search, by its words
  and by meaning, in an index of its own).
- **THIS AUTHORIZES** one automatic pass of the local embedding model, with no click, over every picture the table names and
  over the words Faculty already stores about course materials. It is not OCR: the words are the catalog's own (question
  text, captions, section names) and the text a PDF itself holds; no picture is read for text, and a student's file gets no
  words at all. It produces 768 numbers a picture, no transcript, no identity, no grade and no flag, and nothing in the
  pipeline waits for it or reads it (PIPELINE is unchanged). OCR's own limit ("does not authorize ... general automatic
  OCR") stands. Nothing leaves the machine (EMBEDDING).
- **OWNER.** The workspace, as rebuildable computing: one SQLite file under `<nvme>/runtime/picture-index/` (mode 0600 in a
  0700 folder; `FACULTY_PICTURE_INDEX_DIR` moves it; STORAGE: never the root disk). It is not teaching data, is in no backup,
  and owns no catalog table, migration or record. An entry is a reference, never a copy: the source, the row's id (and a
  page number), the SHA-256 of the file it was made from, the model and revision with how finely it saw the picture, and the vector; for words, their SHA-256
  and their vector. No picture, no text, no name and no student is in it. Deleting the file loses nothing; the worker fills
  it again. Its `cache_generations` counters are rebuildable workspace cache
  metadata, incremented on every picture/text row mutation. A held matrix reads
  its counter and rows in one snapshot; row counts and maximum row IDs alone do
  not detect a deleted row replaced with the same ID (2026-10-24).
- **WHAT IS READ.** Each file through its owner's own read (lifecycle lock, checksum): `InboxService.read_artifact`,
  `AssignmentTemplateService.read`, `QuestionBankService.read_picture`, `CourseNotesService.read`,
  `AbsenceService.read_document`, `CommunicationService.attachment`. Course storage that is archived or moving is not read
  for an index (an archived owner is checked whole on the backup drive per read; a moving one is reconciled by a read). The
  communications record is the exception by its own design: its files live on the backup drive in small bundles and are read
  there, one a turn. A PDF's pages are drawn and its text read in the document sandbox (no network, nothing kept); a
  picture of another kind (HEIC, WebP, GIF) is made a PNG in memory. A file that is neither a picture nor a PDF is set aside
  at once.
- **ONE UNIT A TURN.** The Inbox worker gives the index one bounded turn in its rotation (`PictureSearch.process_one`): one
  picture, or at most four pages of one PDF, and their words; no lock held while the model works. A request to the embedding
  worker carries one picture or one text of at most 1,500 characters, so the Assistant's 1.5-second memory lookups never wait
  behind a batch. Nothing waiting costs nothing for 30 seconds. It runs only when `FACULTY_EMBEDDING_SOCKET` names the worker
  (both services' drop-ins).
- **TROUBLE.** A picture the worker refuses because of the picture itself (the reply's `picture` mark) is set aside with the
  reason. A file that cannot be read, or no answer from the worker, is a wait of 60 seconds that sets nothing aside; only
  after three in a row on the same unit is it set aside (for a silent worker, only while it still answers a status request),
  so one bad file cannot hold every later one behind it and a worker that is down costs nothing. A worker that has lent the
  GPU (EMBEDDING "LENDS THE GPU": the reply's `lent` mark) is the same wait and counts against no picture, though it answers
  a status request all the while. What was set aside is tried
  again after seven days (an empty page and a page with no words are not faults: they wait for the file or the words to
  change). A page of a PDF that is refused or empty is left out alone; the file's other pages are still read. A file drawn
  again under the same id has another hash and is embedded again; words that changed (a question revised) are embedded
  again. An idle turn drops entries of rows the catalog no longer has. A new model revision embeds everything again; the
  other revision's vectors are never searched. Another picture setting does the same for pictures alone (EMBEDDING
  "PICTURES AT 560 PIECES"): each waits again and is searched once it is in again, its words keep their vectors, and once
  nothing waits an idle turn forgets what the other setting or revision made. A worker still on the other setting (started
  before the change) is the same wait as a lent card and counts against no picture; a search by a picture says picture
  search is not answering.
- **SEARCH** is `GET /api/picture-search?q=&limit=&sources=` (read only; `q` up to 500 characters, `limit` 1 to 24, `sources`
  an optional comma list of the table's names) and `GET /api/picture-search/status`. The description is embedded as a query
  by the same worker and compared in the web process with the kept vectors (at most 40,000 of each held, read again only when
  the index changed: PERFORMANCE). **What Faculty has words about is matched by those words, and is not guessed at by its
  look** (requirement, 2026-10-10: "do the scans"): a word match is `matched: "words"`, counted only when the words are
  near the description (`WORDS_NEAR`, 0.70 for this model revision: on the live course text the best match of each of 8
  course topics scored .716 to .794, and of 8 descriptions of other things only one reached the bar). Printed pages come
  first, then the rest by nearness, the nearest of each kind before any kind's third. **A scanned page is found through the
  page it was printed from**: a printed page and its scanned copies are one result, which says how many scans there are
  and links to the newest (`scans`); asked for scans alone, the nearest scan stands for its page. What Faculty has no words
  about (a scan with no identified page, a page with no blank copy, a student's file) is matched by how it looks
  (`matched: "look"`) and fills the places left, only when it is near (`LOOK_NEAR`, 0.70: after "supply and demand" matched
  four notes pages the nearest look-alikes were students' attachments at .65 to .69; after "a doctor's note" matched one
  notes page by chance, absence documents followed at .70 to .74). Only when no words match at all is everything matched by
  look, with no bar: it is then the only answer there is, and nearest is not the same as near. A new model revision is
  measured again before either bar is kept. So is another picture setting, for `LOOK_NEAR` (words against words do not
  change with it): read again at 560 pieces on the filled live index (2026-10-11), among students' files alone, the eight
  nearest "supply and demand" scored .62 to .67 (.63 to .69 at 280 pieces) and the eight nearest "a doctor's note" .70 to
  .74 (the same at 280), so the bar stays at 0.70. **Who may see a picture
  is decided at search time from the catalog, never from the index.** A result is where a picture is, never its bytes: a
  route Faculty already serves (for a PDF, the file and the page), a small picture of it (the owner's own preview, or the
  file viewer's drawing), a label, where it is, and the page that opens it. A result about a student's file or page never
  says whose it is, never carries a file name or a subject, and links only to the Absences or Mail page; the file itself,
  opened, says the rest. Without the worker the route answers 503 with the reason; nothing falls back to a hosted service.
- **NEAREST IS NEVER A FINDING.** A score orders the results and nothing else. It is not evidence of what a picture shows,
  of what a student wrote or sent, or of anything about a student; no grade, flag, count, decision or attention item may
  read it, and it is not shown as a number.
- **THE INBOX SEARCH BOX** (INBOX's one search, `data-page-search`) also asks when the words describe something (not a job ID
  or a filename): once per search, never per poll. What is found is a strip of small pictures above the list ("Pages and
  pictures for “…”", up to eight, every kind; a printed page's caption is followed by "N scans", which opens the newest
  scan holding one), open when no submission matched and folded to one line when some did; the
  instructor's own open or closed choice is remembered. A picture opens in the file viewer (a PDF at its page); its caption
  opens the page it belongs to, where there is one. No strip is drawn while it is asked, when nothing was found, or when
  picture search is not set up.
- **BY A PICTURE.** `GET /api/picture-search/like?route=&page=&limit=&sources=` searches by a file Faculty already serves
  (read the way a page reads it, `AppFiles`: a scanned page, a bank picture, a file attached in chat; a PDF by one page), and
  `POST /api/picture-search/like` by a picture or PDF the instructor hands over (`file`, 25 MB at most; the instructor's page
  only, `never` for the model). The picture is embedded on this machine, compared with the kept picture vectors, and kept
  nowhere: no vector, no bytes, no name. Every result is `matched: "look"`, a printed page with its scanned copies still
  one result. Picture against picture is a far surer likeness than words against a picture: on the live index
  (2026-10-10) the nearest scan of each of the 70 blank printed pages was a copy of that page, and 541 of their 560
  nearest eight were. At 560 pieces it also tells versions apart: of 80 Quiz 4 scans, 66 were nearest their own version's
  blank page among the quiz's 18, where 280 pieces put 26 there (2026-10-11). A student ID photo's route is refused (PHOTOS: never embedded). A file attached in chat is scratch
  for a day (ASSISTANT "ATTACH IN CHAT"): it can be searched by, and is never indexed. The Inbox search box has a "Search
  by a picture" button beside Search: the file's name heads the strip ("Pages and pictures like “…”"), the typed words are
  cleared, and Clear search takes the strip away.
- **THE ASSISTANT** has the read tool `find_pictures(query or like, page, limit, kinds)` over the same routes (`like` is a
  file's route, such as the one the chat's attachment note gives). It shows nothing: a found picture reaches the instructor
  or the model only through `fetch_files` (ASSISTANT "Files"), and the Assistant looks before it says what one shows.
- **CHECKS.** A stand-in embedder throughout; no model is loaded and the live worker is never reached (`manage_checks`
  strips `FACULTY_EMBEDDING_SOCKET`). `test_picture_search_sources` covers every attachment a student sent being found
  once, where it was first sent: the copies replies carry, the instructor's own picture carried back, an icon, a clip, a
  file that was not kept, and a file an absence holds in every copy.
