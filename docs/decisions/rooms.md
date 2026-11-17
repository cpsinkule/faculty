## Rooms and seating charts

Rule ID: **ROOMS**. Current binding module contract. Read root `AGENTS.md` first.

- `rooms` is a workspace-level physical resource owner in the shared catalog. It
  belongs to no course code, term, or section. Seating features (exam seating
  charts exist: seating.md) and any future attendance feature must be owned at their own
  course level and reference `rooms.id` and `room_seats.id`; never add course columns
  to rooms or copy chart data into course records. Build new ones only when requested.
- Seat identity within a room is its label (case-insensitive). Replacing a chart
  keeps `room_seats.id` and the broken/left-handed conditions for matching labels,
  moves them to their new positions, and deletes missing seats. Never recycle a
  seat ID for a different label.
- HECC 105's original workbook mislabels row H as L. The instructor's correction
  on 2026-09-18 relabeled L1–L16 to H1–H16 and its row heading, preserving the same
  physical seat IDs, conditions, occupants and exam versions. The correction is
  retained in `room_imports` and `seating_events`; obsolete response cards use the
  existing seat-update flow. Keep this correction when working from the original
  spreadsheet; its source hash and previously issued documents remain historical.
- `room_import.parse_seating_chart` is the only workbook reader and encodes the
  department chart format: a row-letter column (mirrored copy ignored), numbered
  seats labelled `K5`, `N Broken`/bare `Broken` annotations, `Table (N seats)` or
  `TABLE` + `N Chairs` groups labelled `K Table 1 · i`, merged captions read from
  the sheet XML, and note-only rows inside lettered bands collapsed. Charts
  without row letters use cell text as the label. Formulas are rejected; bounds
  are a 200×100 filled span and 2,000 seats. `tests/fixtures/rooms/` holds the
  three real charts; keep them passing when changing the parser. The uploaded
  workbook is not retained; the catalog holds the parsed chart and a
  `room_imports` log with the file's SHA-256, chart-marked count and warnings.
- Seat conditions are `broken`, `left_handed`, and `blocked` (usable furniture
  the instructor will not assign). `usable_count` excludes broken and blocked
  seats. Chart marks only add conditions on import: `Broken`/`Left`, and an
  accessible seat or wheelchair space (`H`, `H (moveable chair)`, `Accessible`, `ADA`,
  `Wheelchair Only`), which is kept free: blocked, with the chart's wording in the
  seat's notes, until the instructor unblocks it for a student who needs it. Every
  other blocked seat is the instructor's, and a re-upload never clears
  instructor-set conditions.
- Room writes are catalog `BEGIN IMMEDIATE` transactions with per-room version
  checks (409 when stale). They touch no drive folders or storage locks; the
  nightly catalog backup and the JSON export include rooms.
- The page's Content Security Policy forbids inline `style` attributes. Apply
  dynamic layout (seat grid placement) through the CSSOM after rendering markup.
- Room floor-plan geometry (`rooms.layout_json`), review reasons, seat notes and
  extracted issue-document provenance are workspace-owned catalog data, with the
  existing nightly backup. `room_pdf_import.py` recognizes the ten reviewed ILCB
  CAD PDFs by exact SHA-256; changed PDFs require a new verified source profile.
  Native source extraction must use the bounded document Bubblewrap environment.
  `static/seat-grid.js` renders both room and exam views, retaining spreadsheet
  geometry or the PDF's physical coordinates with architectural outlines.
  Chart-local Ctrl+wheel/trackpad pinch and Safari gesture events zoom around
  the pointer (5–200%); ordinary scrolling and the fallback buttons remain.
  Gesture listeners are removed when a grid is replaced/rebound, and screen
  zoom updates are suspended in print media. View state is browser memory only;
  zoom changes no room, course, term, section, or seating data.
- `manage_rooms.py` previews a received directory and `--apply` adds all validated
  rooms atomically through `RoomService.import_many`. Same-name/same-source rooms
  remain untouched. Administrative `allow_ambiguous` parsing qualifies every
  repeated seat number by its source cell and adds `review_required`; never infer
  corrected numbers. Unlocated shortages, unresolved corrections and unknown seat
  annotations also require review. Generation, moves and printing refuse these
  rooms until an explicit version-checked Room settings confirmation, retained in
  `room_imports`. Ordinary chart replacement retains prior conditions and review
  reasons. Word issue notes apply broken conditions only to exact seat labels;
  retained paragraphs and hashes record the other statements without guessing.
- REVIEWED CORRECTIONS (requirement, 2026-10-07; first made for the standalone
  Seating Charts app, ported 2026-10-13: "port the corrections and the reader change
  to Faculty"). The readers still never infer a number. What the department's files
  cannot express is listed room by room in `room_corrections.py`, each entry naming
  its source cell or DR note: accessible seats and wheelchair spaces kept free; a seat
  number printed twice never assigned (both copies blocked, with a note); a typo
  settled by the sequence around it (HECC 200 and 203 row G, BSBE 115's front row);
  ILSB 1105's unlettered rows lettered A–N from the front (`C-L5`, `C-R5`); ILCB 111's
  row A renumbered from the DR notes; ILCB 226's missing chair marked broken; and
  shortages or notes without a location kept as room notes, which every seating
  chart of the room shows (not in print), instead of a review that refuses seating.
  `manage_rooms.py --corrections` shows what they would change and `--apply` saves
  them in one transaction (`RoomService.apply_corrections`). A relabel names the same
  physical seat and keeps its `room_seats.id`, as HECC 105's did. Each room is
  corrected once: its `room_imports` row "Reviewed room corrections" is the receipt,
  so a seat the instructor later unblocks stays unblocked. A room that a seating
  chart uses, or that is not as its source file imports, is left alone and named with
  the reason. Keep these corrections when working from the original files, and add
  one only on the instructor's word, with its source.
