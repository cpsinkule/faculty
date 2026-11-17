# Design inbox

How changes made in the instructor's design session (the Design canvas "Teaching App Design Language") reach the
app. The design session writes here; the repo agent implements. Rule ID: **DESIGN-LANGUAGE** (this file is part of
that contract's process, not a separate rule).

## How it works

1. **Design session** (writes only `docs/decisions/design-language.md`, `docs/design-language/`, `tools/plates/`):
   updates the contract first, refreshes the matching files in `mockups/`, then adds an entry under **Open** below.
   It never edits app code, tests or other contracts, and never commits.
2. **Instructor** tells the repo agent: *"apply the design inbox"*.
3. **Repo agent**, for each Open entry, oldest first:
   - Treat the contract sections the entry names as authoritative; they are already updated. Read the entry's
     mockups for structure, spacing and copy. Mockups are reference only: their `/_blob/...` images are canvas
     uploads (use `static/plates/` and the manifest instead), their inline styles are not code to copy, and their
     student names and numbers are placeholders.
   - Commit the design session's uncommitted contract/mockup/inbox files first (its own commit, as before), then
     implement on a branch per the usual rules (AGENTS.md, CHECKS, restart after frontend edits), merge, and move
     the entry to **Done** with the commit hash and one line on what changed.
   - If an entry contradicts another contract, FRONTEND behaviour, or itself, do not guess: move it to **Blocked**
     with the question, and carry on with the next entry.
4. The design session reads **Done** and **Blocked** before its next change, so the canvas and the app stay in step.

Entry format:

```
### DL-YYYY-MM-DD-n · short title
Asked: the instructor's words (quoted).
Contract: DESIGN-LANGUAGE §…
Mockups: mockups/<file>.html (board name)
Scope: which routes / components; what must not change.
Done when: the visible result, in one or two lines.
```

## Open

(none)

## Blocked

(none)

## Done

### DL-2026-10-03-2 · Renaissance paintings back, to fill dead space — 92b1b71 (landed with the Assignment workspace fixes)
The three paintings moved to `static/plates/paintings/` with a README (work, artist and date, source, licence — rawpixel's
public-domain collection is CC0 1.0 — size and SHA-256); `tests/test_design_language.py` checks each file against it.
Shared `static/paintings.js` / `paintings.css` (`.painting`, `.painting-print` with `.inked` or `.painting-ink`,
`.painting-credit`); forced colours and print drop them; `tests/painting_checks.py` measures contrast behind the printed
words on screen. Choices made in the build, for the design session to confirm or change:
- Home: "MATH 140" sits at 22% height, not 12 px from the top (4.0:1 at a 1180 px window there; 4.74–6.45:1 now).
- Student follow-up had no Office hours column: a narrow one was added beside the list (the next week's office hours
  from the calendar feed — the app keeps no recurring schedule — and an "All office hours" link). The painting prints
  today's next block, e.g. "Today, 3–5 PM" (with AM/PM); with nothing left today it prints nothing. Never made up.
- The Ermine's time stays on one line and scales to the column (two lines put the upper one on the ermine, 2.7:1).
- The same day the Follow-up page became a conversation queue beside one selected conversation (TEACHING-WORKFLOWS,
  "FOLLOW-UP REVIEW LAYOUT", instructor: "way too complicated, spread out, confusing"). The Office hours column stands
  beside both, which leaves the conversation about 450 px wide at 1440: worth a look on the canvas (e.g. the Ermine and
  the hours under the queue instead).
- Certify: the painting stands beside a new summary block (band, details, cards); the distribution runs full width
  under both, as in the mockup. "The harvest is in." sits at 46% height over the wheat (top-left measured under 3:1),
  is shown before every grade is in, and the painting is hidden while a student drawer is open.

### DL-2026-10-03-1 · Plate stops at the sidebar; the arc comes back — 04a8b06 (landed with fc8131e)
The photograph sits in `.page-plate` again, from the rail's edge; the rail keeps its paper and one continuous right rule;
`sidebar-arc.js` is back with the fixed-pixel ellipse and the dusk `--arc-*` stops (manifest and `plates.json` carry
`arc`/`halo` again); `.plate-backdrop`, `.plate-rail`, `sidebar-plate.js`, the text halo, `--plate-menu-*` and the
generator's sidebar-strip check are gone. Scrolling writes nothing to the root (`browser_theme`).

### DL-2026-10-03-3 · Every screen on the canvas: the shell holds on all of them — 04a8b06 (landed with fc8131e)
`tests/browser_shell_routes.py` visits the 38 routes and opens the 12 dialogs at 1440 × 900 and 1440 × 1100 with the
checks in `tests/shell_checks.py` (arc box and ellipse, rail paper and rule, reprint entry rectangles, plate edge). The
reprint's page carries the rail's classes. One difference from §8's wording, recorded there: the sticky footer
(Workspace settings, the server status) is drawn above the arc on its own paper, out to the rail's left edge, instead of
being reprinted — a reprinted status button duplicated the app's own (every test that finds it broke), and drawn above
the arc it is never covered.

Before this inbox existed, the design session's contract changes on 2026-10-03 were picked up directly; for the
record: plates under the sidebar with no arc and a text halo (9e184c2), the plate as the only picture on a page
(7080770), the single dusk palette for every page.

## Mockups

`mockups/` holds the current canvas boards as standalone HTML (open in a browser; images will be missing):
`MagazineHome`, `MagazineFollowup`, `MagazineGrading` (Review step), `GradingOverview`, `GradingRubrics` (step 1),
`GradingAnswerAreas` (step 2), `GradingKey` (step 3), `GradingGrade` (step 4), `GradingCertify` (step 6).
