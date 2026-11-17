# Canvas

Faculty talks to the instructor's Canvas courses with a kept NetID session (`web.py`, `signin.py`; AGENTS.md "Canvas"). Each
section links its Canvas course once on its section tab (`service.py`, section-owned). This file covers what is built on that.

## Modules and pages

The instructor asked on 2026-09-28: "I want to be able to edit the modules from faculty and push the changes etc to canvas because
it is very difficult to do it for 4 different sections through canvas", then "build it, start with modules and pages".

**Where.** `#course/<group>/home/canvas` (`static/canvas-content.js`), reached from the sidebar's **Canvas** page, the course Home's
Canvas row, each section tab's Canvas line ("All sections' Canvas content") and the palette (see "Finding it" below).

**Owners.**

| Record | Owner | Why |
| --- | --- | --- |
| Faculty's copy of the modules and pages (`canvas_content_revisions`) | the TERM | one syllabus for the term's sections |
| Which Canvas module, item or page is which of Faculty's, and what it held when Faculty last wrote or paired it (`canvas_content_links`) | the TERM and one Canvas course | a course several sections link is one course here |
| A push: the approved plan and its progress (`canvas_content_pushes`) | the TERM | the receipt of one approval |

Revisions are immutable (the latest counts), links append-only (the latest per object counts), pushes retained; only a running
push's status and progress change. Migration `canvas/migrations/002_canvas_content.sql`.

**Faculty's copy** (`content.py`, `normalize`) holds modules (name, published, opening time, sequential progress, sections) with
their items, and pages (title, body, published, front page, sections). IDs are Faculty's. An item of type Page names one of the
copy's pages; an item pointing at other Canvas content names it by what is the same in every course made from one template: an
assignment's, quiz's or discussion's name, a file's folder and name, an external tool's ID. Modules, items and pages may be
limited to some sections; they then go only to the Canvas courses of those sections. Items can be added as text headers, pages,
web links, the copy's assignments and files from the instructor's computer ("Files" below); items pointing at quizzes,
discussions, tools and Canvas's own files come from the course the copy was started from.

**Starting.** The copy is made from one section's Canvas course as it is (`start`). Every object of that course is recorded as
paired, and so is every object of the other courses that already matches by name and is alike there, so that a later edit on
either side can be told apart. Starting again needs the latest revision as its base and forgets what the earlier copy managed.

**Matching** (`match`), per course: Faculty's record first (an ID that still exists), then by name, only when exactly one on each
side (items first within their module, then anywhere in the course: a moved item). Two Canvas objects with one name are never
guessed between: the push window offers **Same as…** to pair one by hand (`pair`, source `paired`); an empty choice marks it new
there (`unpaired`: never matched by name again). One structural rule settles a page a course holds twice (requirement, 2026-09-30:
508, 514 and 515 each held two copies of five pages): it is the copy that the course's matching module item points at, the item
of the same title in the module matched to the one holding Faculty's item (`_by_module_link`, how `module`), and only when exactly
one copy is found that way; the rest are then matched by name if that leaves one each. Two copies both linked stay unmatched.

**Page links.** A page body is kept as written. Links to any of the term's Canvas courses are rewritten for each course when it is
pushed (`render`): files by folder and name, pages and modules through the matches, assignments, quizzes and discussions by name,
anything else (course home, grades, tools) by swapping the course ID. An embedded course video or audio file
(`/media_attachments_iframe/<file id>`, which names no course) becomes the target course's file of the same folder and name, and
the source's `data-media-id` is dropped with it (requirement, 2026-09-30: each section embeds its own copy of the same videos). A
link that cannot be found in a course holds that page back there, with the reason; links to courses outside the term are
untouched. Titles and names are compared without the spaces at their ends, which Faculty's copy never keeps.

**The preview** (`plan`, POST `.../preview`, writes nothing) says per course what a push does, and per object compares Faculty's
copy with Canvas using the record of the last write (`snapshot`: digests of what Faculty sent, in Faculty's terms, and of what
Canvas held after it). Faculty changed it and Canvas did not: an update. Canvas changed it (someone edited it there), or there is no
record and they differ: listed under **Needs your decision** and written only when the instructor ticks Replace (or Replace all for
that course). **Only in Canvas** lists what nothing of the copy matches; nothing there is removed unless ticked, and only what
Faculty managed and has since removed from its copy is ticked by default, never a page. Removing an item keeps what it points to;
deleting a module keeps its items' assignments and pages. Order is Faculty's: its modules first in its order, the rest after them.

**One section first** (requirement, 2026-09-30, for the first real push: "one real section first"). The preview and the push take
an optional `courses` (Canvas course IDs of the term): the plan then covers those courses alone and its fingerprint names them;
every course is still read and matched, because page links to the others are rewritten through them. `view()` names the courses
a push has written to (`pushed_courses`); while there are none, the push window (`static/canvas-push.js`) starts with ONE course
ticked and says to check it in Canvas before pushing to the rest. A finished push's note links each course's modules in Canvas.

**The push** (POST `.../push` with the preview's `fingerprint`, the same `choices` and `courses`, `request_id` and
`confirmation: "push"`, all checked on the server) reads Canvas again and plans again: anything that changed the plan is a 409. The operations then run in a
thread of the web process, one course after another (pages, modules, items, removals, order), each recorded as Canvas answers,
with its snapshot read from Canvas's answer. A failed operation is reported and the others go on; a signed-out session stops the
push. Pages and assignments that link to a page or assignment the same push makes are written with that link waiting, then again
once it exists (`Push._relink`); one whose link still waits (what it names could not be made) is a failure of the push, and the
next preview writes it again (`content._relinked`). A page or assignment linking to one held back in a course is held back there
too (`content._made_here`). A restart waits for a running push (`canvas/stopping.py`); a push the process no longer runs (it was
killed) is `interrupted`; the next preview reads Canvas as it is, and what a lost answer created is matched by its name.

**Publishing** (2026-10-04 review). Canvas publishes every item of a module with the module (`publish_items!`, unless
`skip_content_tags`) and a page or assignment with its module item (`update_asset_workflow_state!`). So a module's flag is sent
only when the copy changed it, and always as `{published, skip_content_tags: true}`; an item of a page or of an assignment of the
copy is published exactly when that page or assignment is (`normalize` keeps them alike, the page's or assignment's own flag
deciding; the editor's Published box on such an item sets the page's or assignment's), and its own flag is never sent
(`content.follows_content`). Other items send theirs only when the copy changed it. `tests/fake_canvas.py` does both, as Canvas does.

**Files** (requirement, 2026-10-10; docs/decisions/canvas.md FILES; `files.py`, migration `008`). **File…** among a module's new
items opens a window: the file (100 MB at most) and, optionally, its folder in Canvas's Files. POST
`/api/canvas/content/<term>/files` (a form: `file`, and `name` when it replaces a file under that file's Canvas name) keeps it at
once, TERM-owned: `canvas_content_files` (`cf_…`, derived from the term, the content and the name, so the same file again is the
same row) and `canvas-files/<id>/content` in the term's verified storage. The item names it: `content: {name: "course files/<folder>/<name>",
file: "cf_…"}`. `normalize` takes a file only when the term keeps it, under its own name, in a folder of the course's Files (a
save that does not know the kept files keeps an item's file as its base had it). **Replace…** on a file item keeps a corrected
file under the same Canvas name and points the item at it. GET `.../files/<id>` is a download (no-store). `view()` lists the
term's kept files (`files`).

A file uploaded with no folder lands in Canvas's "course files/unfiled", so `files_by_path` lets a file there stand for the top
folder's file of its name while the top folder holds none (2026-10-16: otherwise each push uploaded it again as "…-1.pdf").
`tests/fake_canvas.py` puts such an upload there too. `plan` settles each kept file per course (`_files_plan`): no file of its folder and name there → `upload_file` (verdict `new`;
the item waits for it, an existing item is replaced by one pointing at it); the file Faculty put there (the course's latest
`uploaded` receipt names that Canvas file and that checksum) or one of the same size → nothing; Faculty's own file there and a
different one kept now → `upload_file` with `replace` (verdict `update`, no question); any other file there → Needs your decision
(`differs`, or `edited_in_canvas` when Faculty once put a file there) with both sizes, replaced only when ticked (`file:<id>`
among a course's Replace choices). `Push.upload_file` runs first: Canvas's three-step upload into the course
(`web.Client.upload_course_file`, `on_duplicate` `rename` for a new file, so one that appeared meanwhile is never overwritten,
and `overwrite` for an approved replacement, where Canvas points the old file's module items at the new one), the file read
back from Canvas, and a `canvas_content_file_uploads` row for the answer. A new file whose items are all unpublished is locked
at once; otherwise the item's own Published flag publishes or unpublishes the file with it, as Canvas does. Faculty never
deletes a file from Canvas. `tests/fake_canvas.py` takes the uploads, replaces and renames as Canvas does, and keeps a file's
state with its item's.

**Files from the Assistant** (requirement, 2026-10-10; docs/decisions/canvas.md FILES, FROM THE ASSISTANT). GET
`/api/canvas/content/<term>/outline` answers the copy without page bodies: `revision`, `modules` (`id`, `name`, `published`,
`unlock_at`, `sections`, each item's `type`, `title`, `published`), the kept `files`, `courses` and `pushed_courses`. POST
`.../files/place` (`ContentFiles.place`) takes `request_id`, `base_revision`, `module`, and `source` (the route of a file attached
in the Assistant's chat, read through `AppFiles`; any other route is refused) or `file` (a kept file's ID), with optional `title`,
`name`, `folder`, `published` and `sections`. It keeps the file, appends the File item to that module and records ONE revision whose
request digest is the request's own fields, so the same request answers what it did (`_placed`) and anything else under its ID is
a 409. The assistant's rule is `auto` (Content drafts); the push that follows is its usual Approve card.

**Items found by title elsewhere.** An item matched only by its title in another module of the course, with no record of it, may
be another item of that title: it is listed under Needs your decision ("Found by its title in another module") and is moved and
written only when ticked. An item both moved and pointed at something else is replaced in Faculty's module, never moved too.
Canvas is read and written with no lock held; Faculty's reads and each record take the roster lifecycle lock and one transaction.

**The page editor** shows a body in a sandboxed frame (no scripts run; Canvas's own markup, embedded videos included, is kept) with
a small formatting toolbar, and an HTML view. Images and embedded media come from Canvas and may not show inside Faculty.

**The content pages** (requirement, 2026-10-11: "The canvas menu/pages are a mess and not very user friendly"; docs/decisions/canvas.md
THE CONTENT PAGES). `canvas-content.js` draws the term's page and a section's own page with one `paint`: the bar
(`saveBar` / `directBar`, sticky; its height is written to `--cc-bar` so the lists sit under it), the push where it needs a look
(`pushAlert`; one that went through is the bar's own line, `pushLine`), the notes, the shared sub-tabs and the chosen tab's panel.
The course page's tabs lead to `…/home/canvas` (All sections) and `…/section/<n>/canvas` while a Canvas page is open
(`courses.js`). A module's items are one line each (`targetMarkup` says what an item shows); the rarer moves are in the item's
menu (`ui.js actionMenuMarkup`), Move to another module… in a small window (`openMove`). The module editor's `input`, `change`
and `click` handlers are bound ONCE per host (`boundHosts`) and read `state.draft` when they run: bound at each draw they
multiplied, and one click then acted several times. The pages and the assignments are a list beside its editor: the list is
sticky under the bar and scrolls in its own box (`[data-cc-scroll]`, whose place a redraw keeps), and `reveal` brings a newly
opened editor under the bar. The assignments' find box writes only the list's rows again (`assignmentRows`).

**Assignments** (requirement, 2026-09-28: "Now build the assignments and grade exporting"; migration `003` widens what the links
name) are part of the same copy: name, description (links rewritten like a page's), points, how they are graded, how they are
handed in, group (by name; made in a course that lacks it), due, available from and until, published, whether they count in
Canvas's total, and optionally a due date per section. A course several sections share takes their date only when they agree,
else the assignment is held back there with the reason. An assignment a tool or quiz hands in (WebAssign, a New Quiz) keeps
Canvas's own submission settings; a classic quiz's or a discussion's own assignment is not copied. The readers keep which tool an assignment
hands its work to (`tool`, the tool address's host; `web.graded_by_webassign` says whether it is WebAssign's, for the gradebook's
morning sync's WebAssign import and Sync with Canvas). Module items point at the
copy's assignment itself (renaming it keeps them), and an assignment may name a gradebook column (`gradebook_item`; **From the
gradebook…** makes them from columns), which is where the gradebook's Sync with Canvas sends that column (gradebook/README.md
"Syncing with Canvas"). An assignment only in Canvas is never ticked for deletion, since deleting it deletes its grades.

**WebAssign's due dates follow Canvas** (requirement, 2026-10-05: "extend it to also update the due date in the gradebook and canvas
content page"; 2026-10-08, Canvas quizzes too: `quizzes=True`; docs/decisions/canvas.md WEBASSIGN DUE DATES). WebAssign sends a
changed due date to Canvas itself, so each morning (gradebook/canvas_nightly.py) `CanvasContentService.follow_due_dates` reads each linked course's
assignments and groups (no lock held) and moves a WebAssign assignment's date in the copy for a course when its link has a snapshot
(Faculty synced it there), Canvas's date differs and is not empty, and no revision since that link other than the step's own
(request ID `webassign-due:…`) changed the copy's date for the course's sections (`_date_changed_since`): a date changed in
Faculty and not yet pushed is the instructor's, and the push asks about it as before. The moved courses' sections get Canvas's
date (`_moved`: one date for all is the default alone; otherwise the old default stays while a section still has it and the rest
become section dates). One revision (actor `workspace`); where Canvas changed nothing but that due date since the link and the
copy had not changed, the course's link is recorded as `matched` again with the new snapshot, so the next push neither asks
about it nor treats the instructor's next edit as a conflict. A revision saved while Canvas was read stops the step for the
night. Nothing is written to Canvas.

## Finding it

The instructor asked (2026-09-30: "I want to be able to create assignments and manage the modules in canvas from my app") and
chose to have it findable and guided. The sidebar's **Canvas** entry (`#canvas`, `static/canvas-hub.js`; its own entry beside
Courses, and a course's Canvas pages highlight it) shows every current course from Faculty's records alone (GET
`/api/canvas/overview`, `CanvasContentService.overview`: Canvas is not contacted): the way into its content (**Modules, pages and
assignments**), its sections and their linked courses, and, while any is unfinished, the steps (link each section's course,
start the copy, push to one section first, push to the rest) with ONE next step, derived as `next` (`link_sections`,
`start_copy`, `push_first`, `push_rest`, `up_to_date`). Once every step is done they fold under **Setup** (2026-10-11). Its steps only link (its one action, Message students, is
"Messaging sections" below): `?push=1` on the course's Canvas content
opens the push window once and leaves the address. The course Home has a Canvas row with one line of the same state
(`courses.js canvasStatusText`) and, once a section links a course, **Sync grades** (Sync with Canvas for every section;
gradebook/README.md "Syncing with Canvas"), each section tab's Canvas line links the term's content, and the palette lists
"Canvas content · <course>".

## Putting a Faculty assignment on Canvas

Asked (2026-09-30), the instructor chose that making an assignment in Faculty offers to make its Canvas assignment in every
section and place it in a module. **Put on Canvas…** (`static/canvas-placement.js`, `canvas/placement.py`) is on each assignment
card of Assignments & printing and in each assignment's Create area under Assignment tools (ASSIGNMENT-WORKSPACE), both with where
it stands: "On Canvas", "On Canvas in 1 of 4 courses", "In the Canvas copy, not pushed yet"; in the Create New Assignment window as an optional last step **Canvas** (only when Canvas is set up; also
`#documents?assignment=<id>&step=canvas`), on an approved quiz draft (its total as the points) and in a gradebook column's settings.

It is a CHANGE OF FACULTY'S TERM COPY, never a write to Canvas: `GET .../placement?assignment_id=&item_id=` proposes (Faculty's
records only) and `POST .../placement` saves one revision through the copy's ordinary `save` (`base_revision`, request ID). The
copy's assignment stands for the Faculty assignment through `faculty_assignment` (an `assignments.id`) and for a gradebook column
through `gradebook_item`, both Faculty-side only: they are kept out of `assignment_plan_state`, so they never change what is sent,
a snapshot or a fingerprint. The assignment placed is the one the copy already has for that Faculty assignment or column, else the
one of the same name that stands for nothing else, else a new one. The proposal fills: the name (the Faculty title, or the
column's), the points (the column's, else a column linking the assignment, else one paper of the term's grading,
`gradebook.sources.maximum`), the Assignment Group (the column's category), the due date (the column's due date, else the exam's date from
its seating chart or main sitting) at 11:59 PM, handed in on paper, unpublished. Dates are campus wall clock, converted on the
server (America/Chicago). The module is an existing one of the copy, a new one (named in the form) or none; an Assignment item
pointing at the assignment is added there, or moved there from another module, and every item pointing at it takes the
assignment's name (Canvas names an assignment's item after it). The Assignment Group is REQUIRED (a list of the copy's groups and
the gradebook's categories, or New Assignment Group…; the save refuses a blank one): Canvas puts an assignment made without one in
the course's first group (2026-10-02: a certified Quiz 3 column had no category, its group was saved blank and Canvas put it in
Checks for Understanding; the instructor asked for the field to say "Assignment Group"). It is required everywhere the copy is
saved ("yes make it required there too"): the copy's own save and a section edited directly refuse an assignment without one (the
editor opens it and names it; a course merely READ from Canvas is not refused), and a push holds back, rather than makes, an
assignment an older revision saved without one. Then **Push to Canvas…** opens the
ordinary push window.
`GET .../assignments/status` says per course whether each linked assignment is pushed, changed since, or not yet (from the
links' snapshots; Canvas is not contacted). The assistant may place (a Faculty draft, like saving the copy); pushing stays an
Approve card.

## Every gradebook column's assignment

The instructor's rule (2026-10-06): "a gradebook column has to correspond to an assignment in an assignment category", "unless it is
a derived aggregate column" (a category's percentage, the overall, the letter: computed, never a column). `canvas/column_assignments.py`
(docs/decisions/canvas.md COLUMN ASSIGNMENTS):

- `adopt` (`GradebookService.columns_changed`, after every gradebook write that can add a column, and when a copy starts): each of
  Faculty's columns (not archived, not kept off Canvas, in a category, linked to no Canvas assignment) without an assignment in the
  copy gets one RIGHT AWAY, one revision of the copy through its own save: the copy's assignment that stands for it is linked
  (`gradebook_item`, its `faculty_assignment` kept), else one is added: the column's title, points and sections, its category as the
  Assignment Group, due 11:59 PM campus time on its due date, published, no submission. Canvas is not contacted; a failure is logged
  and the next write tries again. A name another column's assignment already has is left for the instructor.
- Sync with Canvas creates such an assignment as the copy has it (`gradebook/canvas_export.py from_copy`) and records the copy's
  link with the snapshot a push records (`created_link`), so the next push finds it in step; one with a description or per-section
  dates is the push's.
- When Sync with Canvas publishes an assignment of the copy (PUBLISH FROM SYNC, the instructor's tick), `published_by_sync`
  publishes the copy's assignment too (one revision, normalized so its module items follow) and, where the course's link was in
  step, records a new link with the published state, so the next push neither unpublishes it nor asks about it.
- One published in Canvas itself (PUBLISHED IN CANVAS STANDS): the plan does not ask (`_published_since`), and each morning
  `CanvasContentService.follow_published` publishes it in the copy once every course it goes to has, the same way.
- `overview` (GET `/api/canvas/content/<term>` → `columns`, additive): `canvas_only`, the columns whose assignment Canvas holds and
  the copy does not keep (WebAssign's, a Canvas quiz's, one linked to a Canvas assignment the copy has under no name of its own;
  "Exam 1" linked to the assignment the copy calls "Exam I Grade" is the copy's), each listed read-only in its category's fold on
  the Assignments tab ("In Canvas", "Canvas quiz", "WebAssign"); `ungrouped`, the columns without a category, named in a needs-you
  line that links to the gradebook setup.

## Editing one section directly

Asked (2026-09-30), the instructor chose to be able to change a single section's Canvas modules, pages and assignments without
the shared term copy. **Edit this section's Canvas** (a section tab's Canvas line, the sidebar's Canvas page's "Edit only this
section", a section's tab on the Canvas content page, the palette) opens
`#course/<group>/section/<n>/canvas`: the same editor as the term copy, in section mode (`static/canvas-content.js`,
`canvas/section_content.py`).

**Nothing is kept but the receipt.** GET `/api/canvas/sections/<section>/content` reads the section's linked Canvas course and
returns its content in Faculty's terms with keys derived from the Canvas IDs (`content.direct`: `mod_c<id>`, `itm_c<id>`,
`pg_c<id>`, `asg_c<id>`, the same every read), the `base` (per object, what it held as read: `content.object_states`), the items
the editor cannot show (`skipped`, left alone) and which objects the term's copy manages there (`managed`). The edit is browser
memory (a leave guard asks). **Review and push…** POSTs the draft with its `base`: the preview reads the course again, links each
object to itself with the page's `base` as its snapshot and plans with the ordinary rules (`content.plan`) for that one course, so
what the instructor changed is written, what someone changed in Canvas since the page was opened is asked about (Replace), an
item or module removed here is ticked for removal, a page or assignment never is. A draft key Canvas no longer has is a 409 (open
the page again). Items pointing at one of two quizzes or files of one name keep what their Canvas item points to (`content_id`,
set by the server). The push names the preview's fingerprint, the same draft, base and choices, a request ID and
`confirmation: "push"`, re-plans (409 when anything changed) and runs like a term push in a thread (`SectionPush`); a term push
and a direct one never write one course at once (`CanvasContentService.claim`, a 409).

**Owner.** Each push is a `canvas_section_pushes` row (migration `004`), SECTION-owned (`course_sections.id`) with the Canvas course
it wrote: the approved draft, the plan, its progress and each operation's Canvas answer (`progress.receipts`). Only a running
push's progress and status change. A course several sections share is one course: the page says so.

**The term's copy is never changed by it** (a narrower edit never propagates to its parent) and no `canvas_content_links` row is
written. The copy's next preview therefore asks about an object it manages that was changed here (edited in Canvas), redoes its
own order of the modules and items it manages, and makes again an object it manages that was deleted here. The section page
marks those objects "In the term's copy" and says so once.

**Files here.** A file added on a section's page is kept by the term like any other (shared by reference, never copied to the
section) and named by the draft's item; the push uploads it to that one course, and its receipt names the section.

**Not built yet**: quizzes themselves, and the course-export import.

## Messaging sections

The instructor asked (2026-10-01): "In the canvas page, I want an option to send mass mail through canvas where I can select the
sections I want to send it to". **Message students** is the sidebar Canvas page's action (`static/canvas-message.js`,
`canvas/messages.py`; the palette's "Message students through Canvas" opens it through `#canvas?message=1`). The window lists every
current term's sections with their linked Canvas course and current roster count; a term's only course starts with every linked
section ticked, and a section without a Canvas course cannot be ticked. **Send an individual message to each recipient**, Canvas's
own box, is UNTICKED by default (requirement, 2026-10-01: "I usually dont check it because I'd rather send one mail to every student in
the section so my mailbox doesn't get flooded. Can we not have the same thing where it defaults to group send?"). Subject (Canvas's 255
characters) and message (plain text, 20,000 characters), then **Review**. A message the Assistant prepares goes as a group
unless the instructor's own words in the chat ask for individual messages (2026-10-13; ASSISTANT "CANVAS MESSAGE CARD").

**Attachments** (requirement, 2026-10-09; AGENTS.md "Canvas" MESSAGES ATTACHMENTS): up to 10 files, 25 MB together, under the message:
**Attach from your computer** or **Attach from Faculty…** (lecture notes and saved files). The review lists them; Send uploads each
once into the instructor's own Canvas "conversation attachments" folder (never the course's files), then sends every course with
them. A file Canvas does not take sends nothing.

**Who it reaches** (`CanvasMessages.plan`, POST `/api/canvas/messages/preview`, writes nothing). Each ticked section's linked course
is read now (its student enrollments, no lock held) and each student is matched to Faculty by UIN = SIS user ID exactly:

| Canvas student | Gets it? |
| --- | --- |
| enrollment not active (invited, inactive, completed) | no: "Canvas lists the enrollment as …" |
| on a ticked section's current roster | yes |
| on the current roster of a section not ticked (a combined course) | no |
| dropped in Faculty | no |
| on no roster of the term (a late add, no SIS ID) | yes when every section linking that course is ticked, else no (never guessed) |

A ticked section's current students Canvas does not list in its course are named as not reached; a ticked section with no course is
named with its count. A student in two of the courses gets ONE message, in their own section's course. The review shows the count
per course with the names, the students only Canvas lists, everyone left out grouped by reason, and the exact words. Canvas user
IDs and UINs never leave the server. Each course of the preview also carries `roster` (the current students of its ticked
sections) and `roster_reached` (how many of them get the message, in whichever course): the Assistant's card says "All students
in 507" only when the two are equal (2026-10-12; ASSISTANT "CANVAS MESSAGE CARD"). They are not part of the fingerprint.

**Sending** (POST `/api/canvas/messages/send` with the review's `fingerprint`, a `request_id` and `confirmation: "send"`, checked on
the server; the fingerprint covers the words and exactly who gets them, so a roster or Canvas change since the review is a 409 and
a new review; so is whether it goes as a group). One request per Canvas course, as Canvas's own inbox sends it
(`web.Client.send_message`: `recipients[]` the Canvas user IDs, `context_code` the course; its GraphQL createConversation and
conversations_controller, read 2026-10-01):

- **Group (the default)**: `group_conversation` true: ONE conversation per Canvas course that every recipient shares. Students see who
  else got it and may reply to everyone, as with Canvas's box unticked. Canvas refuses a group conversation of more than 100 people,
  so a course with more recipients goes individually and the review says so (`too_many_for_group`). A Canvas that sends instructors'
  messages one by one anyway (its `restrict_student_access` feature) makes one conversation per student; the receipt then says how
  many Canvas made.
- **Individual (ticked)**: `group_conversation` and `bulk_message` true: each student gets their OWN conversation, replies come to the
  instructor alone and nobody sees who else got it. Canvas's own threshold sets the mode: sync up to 100 recipients (201, the
  conversations made), async above (202 with `X-Conversation-Batch-Id`, delivered by Canvas in the background).

**Receipts** (migrations `005` and `006`, WORKSPACE-owned like Mail's sends: the instructor's own correspondence, which may span sections and
terms). `canvas_messages` (the approved words and the sections, by `course_sections.id`) and one `canvas_message_deliveries` row per
course (its recipients: Canvas user ID, Faculty enrollment when known, and the name Canvas showed; `kind` group or individual, every
row before migration 006 individual) are committed BEFORE the first
request; `canvas_message_outcomes` records Canvas's answer per course: `accepted` (2xx), `failed` (a 4xx refusal: nothing was
sent), `uncertain` (no answer, or a 5xx). A delivery with no outcome was interrupted and is shown as uncertain. Canvas refusing the
session mid-send fails that course and the courses after it without trying them. Nothing is ever sent twice: the same request ID
returns its record and asks Canvas nothing; anything else is a new message the instructor writes and approves. All append-only.

**In the background, never cut off by a restart** (requirement, 2026-10-01, after their first send lost two courses to a restart of
live 47 s in, the courses then being sent inside the browser's request: "yes fix that"). Once the receipts are committed, a thread of
the web process sends the courses one after another and the send answers at once with the record; a course is `sending` while this
process is still sending it (`CanvasMessages.busy`), and the window follows `GET /api/canvas/messages/<id>` until every course has
Canvas's answer (it can be closed; the Canvas page re-reads its list while a message is sending). A stop waits: app.py installs
`canvas.stopping.stop_handler` for SIGTERM, which refuses new writes to Canvas (503 "Faculty is restarting") and lets running ones
finish (messages, pushes, grades sent, replies posted from the Mail page; `CanvasService.writes`), at most `STOP_WAIT` (600) seconds,
within systemd's `TimeoutStopSec` of 630 (`deploy/faculty.service.d/stop-wait.conf`; 80 of systemd's 90 until 2026-10-06, when a
restart cut a Sync with Canvas off with 4 of its 20 assignments unsent); then the handler it replaced ends the process (rosters._terminate closes the catalog's idle connections first). The
handler itself only closes the door and returns, so waitress keeps answering (the 503) while a thread waits; `systemctl restart`
simply takes longer while something goes out.
A delivery without an answer that nothing is sending any more (the process was killed) is uncertain, as before.

**The page** lists the latest 20 messages (subject, how many students, which sections, when, Sending… / Sent / Partly sent /
Not sent / Unconfirmed). **Check delivery** (GET `/api/canvas/messages/<id>/delivery`, reads Canvas, records nothing) says per course whether
Canvas is still delivering a batch (`/api/v1/conversations/batches`) and how many recipients hold the message itself: a Sent
conversation of that subject holding a message of its words written since the send (each such conversation's messages are read,
four at a time and never marked read; subject and time alone also matched an earlier message of the same subject, 2026-10-01 audit),
naming those who do not. The messages are Canvas
conversations, so the communications record captures each student's copy on its next Canvas round and folds it as a mass message.
The assistant may preview; sending is an Approve card (`canvas.message_send`).

## Announcements (docs/decisions/canvas.md ANNOUNCEMENTS)

`canvas/announcements.py`: one published announcement in the linked Canvas course of each chosen current section
(`web.Client.post_announcement`, POST `/api/v1/courses/:id/discussion_topics` with `is_announcement`). GET `/api/canvas/announcements`
lists the sections and the latest posts; POST `/preview` (`title`, `message` as plain text, `sections`) names each course, the
unticked sections sharing one (`also`) and the sections with no course (`unlinked`) without contacting Canvas; POST `/post` adds the
preview's `fingerprint`, a `request_id` and `confirmation: "post"`. Receipts (`canvas_announcements`, `canvas_announcement_posts`,
`canvas_announcement_outcomes`, migration `canvas/009`) are committed before any request and never posted again. The assistant may
preview; posting is an Approve card (`canvas.announcement_post`). There is no page window yet: the Assistant's card is the way in.

## Posting and hiding grades (docs/decisions/canvas.md POSTING GRADES)

Canvas posts and hides an assignment's grades only through its GraphQL API, as its own gradebook's Post and Hide do:
`web.Client.post_grades(assignment)` (`postAssignmentGrades`, `gradedOnly: false`: Canvas's "Everyone") and
`hide_grades(assignment)` (`hideAssignmentGrades`) send one mutation each through `graphql()` (POST `/api/graphql` with the kept
session's cookies and CSRF token, like every write) and answer Canvas's Progress `{id, state, message}`; Canvas's `errors` (top
level or the mutation's) are a CanvasError with its words. `assignment_submissions` carries each submission's `posted`. The
gradebook's `canvas_posting.py`, after each Sync with Canvas's send (the instructor's or the morning one), reads first, records,
asks, follows the progress (`progress(id)`) and reads every submission back. `set_post_policy(assignment, manual=True)`
(`setAssignmentPostPolicy`, read from Canvas's schema on 2026-10-08; it answers `{post_manually}` at once) is how a Sync switches a
by-hand column's assignment to post by hand before its grades (`GradebookService._switch_policy`, which reads the assignment back).
Faculty's Canvas copy's due dates of WebAssign's assignments and, since 2026-10-08, Canvas quizzes follow Canvas each morning
(`CanvasContentService.follow_due_dates(quizzes=True)`).
Conflict review now shows the exact current Faculty and Canvas field values, with long values marked shortened. Text can be
resolved in the existing draft (Save/review/Push still apply), or explicitly handed to the assistant for a review-only proposal.
Compared-value hashes are part of the preview fingerprint, including conflicts not selected for replacement.
