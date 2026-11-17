## Screen design language: Ledger

Rule ID: **DESIGN-LANGUAGE**. Current binding contract for every screen surface. Read root `AGENTS.md` and
FRONTEND first; this contract decides how the screen looks, FRONTEND still decides how it behaves.

Instructor decisions, 2026-10-04. After a usability audit of their most-used pages (Follow-up, the Assignments pages, Home,
Gradebook, Courses) the instructor wrote of the Magazine edition (2026-10-03): "It is aesthetically pleasing, but it does not
make for a user friendly experience. Important details don't stand out enough, irrelevant things mesh with important things,
pages aren't intuitive." They asked for a "completely new look optimized for usability", then: "I'm willing to give up the
current look, I am not bound to any specific website, but I think I want to lean more toward neo brutalism style site design
language." Three directions were prototyped on the follow-up review and a section gradebook (Ledger, Signal, Grid); the
instructor chose: **"go with Ledger"**. Ledger replaces the Magazine edition entirely: no photographs, no paintings, no
sunset arc, no serif, no paper grain. The Magazine contract is in git history and `docs/history/`.

Carried over from earlier the same day: "slim band everywhere" (the header is a slim band; in Ledger a solid white one),
"remove paintings", "slate focus ring", "show 2 decimals but store all 4" (GRADEBOOK DISPLAY).

**One appearance, desktop only** (requirement, 2026-10-03: "get rid of dark mode and mobile, let's only focus on the desktop and
one design language instead of trying to juggle dark mode and mobile too"). There is no dark appearance, no theme switch or
`data-theme`, and no phone or tablet layout: the app's minimum width is 1180 px (`layout.css`), a narrower window scrolls
sideways, and no stylesheet or script keeps a width condition below that (`tests/test_design_language.py`). Checks and
screenshots are desktop widths (1440 and 1280).

What does **not** change: printed documents, seat charts and exam-version palettes keep their own contracts (DOCUMENTS,
SEATING); every behaviour, route, count and API in FRONTEND and the module contracts; the views table in `app.js` stays the
source of each route's header copy.

### Assistant glass

Requirement, 2026-10-08: "I want the cmd+cmd agent window to have a modern, glass overlay appearance."
The workspace-owned selected-area chat and its selection hint use a light frosted surface, rounded corners,
a fine edge and soft offset shadows. This is the specific exception to Ledger's square frames, opaque surfaces and
hard shadows, including the chat's controls. Material values live in `static/design-tokens.css` as
`--assistant-glass-*`; `ledger.css` applies them to the Assistant's surfaces. Keep the existing typography,
signal colours, visible focus, disabled states and explicit actions. Opaque fallbacks apply when backdrop filtering
is unavailable or reduced transparency is requested; forced colours retain visible boundaries. Other surfaces keep
Ledger. No change to selection, conversation handling, approval or sending is authorized.
The instructor then asked to "turn up the glassy appearance": increase translucency and backdrop blur, add a soft
diagonal reflection and bright inner edges, and deepen the floating shadow. Secondary text uses a darker local ink
to retain contrast on the more transparent surface; opaque accessibility fallbacks remain.
Requirement, 2026-10-08: "make the main assistant window glass as well". The main Assistant drawer shares this material,
including its fields and controls, while keeping its docked position and full height. Its exposed left corners
are rounded; the right edge stays flush with the window. Chat, Changes, Code, Saved and Automation keep their existing
content and actions, with one backdrop filter on the outer drawer rather than stacked blur layers.
Requirement, 2026-10-09 (ASSISTANT "One conversation, real windows"): keep the five tabs and make the drawer resizable. Its
left edge drags its width, which is never under 480 px (the five tabs stay in view) and always leaves 420 px of the page; the
floating chat's edges drag its size, and put away it is its own title bar at the lower right: the same surface, so still one
backdrop filter. The composer's "/" and "@" list is a glass control (the glass's line and control corners, opaque paper, the
neutral chosen tint for its chosen row). The launcher's answer-ready mark is a small slate square, never orange. These surfaces
enter by FRONTEND's "Press and open motion" (a click fades them in over 160 ms; the keyboard and Command gestures show them at
once).
Requirement, 2026-10-10 (ASSISTANT "CHANGES BESIDE THE CHAT"): the floating chat shows its conversation's changes "in a separate
panel next to the chat". The panel is part of the same window: the same glass and its one backdrop filter, set off from the
conversation by the glass's own line, with the shared cards, chips and buttons unchanged. The window widens for it at once (no
motion of its own), and in the conversation a card is one line: its state chip and its name.

Requirement, 2026-10-10: "the agent window is not easy to read or distinguish my text from the agent's"; improve its
readability and aesthetics. Both chat surfaces label speakers. The instructor's messages sit at the right on a soft,
opaque slate tint without a frame; answers stay left-aligned, with 16 px text, generous leading and a bounded reading
width. The reading area has a denser paper layer so the page underneath does not compete with the words, while the
window's surround stays glass. Speaker labels, timestamps, Edit, Copy and the closed step review are smaller than the conversation text.
This extends Assistant glass only; the rest of Ledger keeps its typography and frame rules.

### 1. Principles

**Binding: approved by the instructor, 2026-10-04 ("approve the principles"; decision 6 of that day, "not decided on number 6
yet" until then).** They are the usability audit's findings written as rules, restated for Ledger. Every page change follows
them; a change that would break one needs the instructor's word and an edit here in the same change.

1. **The work starts on the first screen.** Each page's main object (the list, table, conversation or decision) begins in
   the first screen: its first row by about y = 450 at 1440 × 900. The header band is 90 px or less.
2. **Orange means "needs you", and only that.** A needs-you count, row or lead is an orange (`--needs-fill`) block with
   black type. The current page, the chosen tab, links on hover and the focus ring are never orange (ink, ink, ink, slate).
   Inventory counts (Rooms 55, Courses 1) are muted figures, never orange.
3. **A frame means you can act.** Anything clickable that is not running text wears the 2 px ink frame (buttons, tabs,
   fields, chips you can press, a record the whole card opens); what you act on next also casts the hard shadow. Text that
   cannot be clicked is never framed. Page regions are ruled (a 2 px ink rule above), not boxed.
4. **One decision per page, with its action beside it.** The main button is the next step, chosen by state, or there is
   none; never a setup action. Outward actions (send, publish, push) look different from imports and settings: they carry
   the outward tint (`--outward-fill`) and say where they go.
5. **State before metadata.** Missing, late, failing, flagged and uncertain are always marked (failing is the red fill,
   needs-you the orange fill). Sources, registrar fields, import details and repeated names are hidden by default.
6. **Weight follows relevance.** What waits on you is strong; what waits on others is muted; routine items collapse; no
   row of more than four equal links.
7. **Fold by relevance, never fold a decision.** Settings, admin, finished work and quoted email fold; anything to confirm
   stays open.
8. **One navigation layer, tabs that show state and don't move.** Sibling views are tabs; lifecycle tabs show done, needs
   you and current; a page mounted inside another drops its own link bar, stepper and pickers.
9. **Few headings.** One page title and at most three heavy headings per screen. Figures that are read down a column are
   tabular; controls, folds and settings are never headings.
10. **Notices follow consequence.** Anything that records zeros, publishes or regrades is a warning whose title states the
    consequence.
11. **One status vocabulary per object, with dates.** The same words for an assignment's state on every page; dates on every
    assessment surface.
12. **Small capitals are labels only** (requirement, 2026-10-04: "the clickable options aren't very well distinguished from
    regular text", then "make it the rule everywhere"; SENTENCE-CASE ACTIONS). Anything that can be clicked is either framed
    or written as it reads: text actions, go-links, back links, breadcrumbs, tabs and folds are sentence case. A label
    inside a clickable record (a card's eyebrow, a fold's count hint, a keyboard key) stays a label; a table's column
    headings stay labels even where they sort; the seat chart's drawn table titles and the room's stat tiles keep their
    record type.
    A stat tile's label is a few words; anything longer is a plain note under it (critique 2026-10-05: Certify's "Still flagged"
    tile had wrapped a sentence of capitals over four lines).
13. **No walls of text** (requirement, 2026-10-05, of the makeup draft window: "this is not user friendly at all, walls of
    text are never helpful"). Long prose, above all what a model wrote, is broken up by what it is about (a problem, a
    student, a step) and shows beside that thing as one sentence; its full wording folds. What needs the instructor is a
    short marked line, never a sentence inside a paragraph. Nothing is dropped: text of no known shape stays visible.
    Built as `static/review-notes.js` (rows styled in `components.css`, READABLE REVIEW) for the makeup draft window and, on the
    instructor's "do the quiz draft window next" (2026-10-05), the quiz draft page; the question bank's COMPACT ROWS follow it too.
    Certify's student drawer follows it (2026-10-06, "not very user friendly … a wall of text"): an open question
    leads with the grade and the lines applied, then the paper, and folds the other lines, the reading and the models' flags.

### 2. Tokens (`static/design-tokens.css`, the one appearance)

`:root` carries the values below; there is no other appearance and no `[data-theme]` block. The semantic token names of
earlier editions are kept so rules elsewhere keep working (`--font-serif` and `--font-display` now resolve to the heading
sans). Screen rules carry no hex colour or radius of their own (FRONTEND), except `ledger.css`, which states the Ledger
components once (§4).

| Token | Value | Use |
| --- | --- | --- |
| `--canvas` | `#F3F2EC` | the page (flat; no texture) |
| `--paper`, `--surface` | `#FFFFFF` | fields, framed records, dialogs, pop-overs, the header band |
| `--surface-hover` | `#F7F5EE` | row and control hover |
| `--ink`, `--heading` | `#101010` | headings, frames, interactive text, the chosen tab and page |
| `--text` | `#1E1E1E` | body copy |
| `--muted`, `--label` | `#5A5A55` | metadata, labels (6.5:1 on the canvas) |
| `--faint` | `#8C8A82` | disabled and decorative only (never text that must be read) |
| `--line` / `--nested-line` | `#CFCCC2` / `#E4E2DA` | hairlines between groups / between rows |
| `--frame` | `2px solid var(--ink)` | every frame (§3) |
| `--hard-shadow` / `-hover` / `-strong` | `3px 3px 0` / `4px 4px 0` / `6px 6px 0` ink | actionable controls / their hover / dialogs and pop-overs |
| `--needs-fill` (`--accent-fill`) / `--needs-ink` | `#FF6A1A` / `#101010` | needs you: the filled block and its type |
| `--accent` | `#C2410C` | needs-you when it must be text on the canvas (4.9:1) |
| `--danger-fill` / `--danger-fill-ink` | `#D7263D` / `#FFFFFF` | failing, blocked, destructive: the filled block |
| `--error` | `#C21F35` | failing as text, always with the word |
| `--success` / `--warning` | `#2E7D32` / `#9A4E00` | complete / started (text inks) |
| `--blue` (`--focus`) | `#3E5A66` | slate: the focus ring, finished steps, info |
| `--outward-fill` | `#DCE8FF` | outward actions (send, publish, push) |
| `--button-fill` / `--button-ink` | `#101010` / `#FFFFFF` | the primary button |
| `--sidebar-bg` / `--sidebar-ink` / `--sidebar-muted` | `#101010` / `#F3F2EC` / `#A8A79F` | the navigation rail |
| `--header-background` | `#FFFFFF` | the header band |
| `--radius*` | `0` | square everywhere |

Grading role colours (`--grading-role-*`) keep distinct hues at ≥ 4.5:1 on the canvas (primary `#2F5E6E`, second `#6A4A7A`,
verifier `#4E5B78`, arbiter `#8A2F6A`, flag reviewer `#A24B2A`, reader `#2A4A9A`, Jev `#8A6414`); apply/do-not-apply stay
`--success` / `--error`. Toast tone inks on its ink block: `--toast-danger` `#FF8A8A`, `--toast-warning` `#FFB27A`,
`--toast-success` `#8FD18F`, `--toast-info` `#9CC3D6`.

### 3. Typography

All families are already bundled under `static/fonts/` with README entries (source, version, SHA-256, licence); the app never
fetches fonts from another site.

| Family | Weights | Role |
| --- | --- | --- |
| Manrope | 500, 600, 700, 800 | everything: headings (800), controls (700), body (500), labels (700 small capitals) |
| Geist Mono | 500, 700 | meta and figures that are not read down a column: the clock, counts in the rail, badges, IDs, dates in meta lines |
| Berkeley Mono | 400, 700 | code, keyboard keys, raw records, seat-frame box drawing |

Tokens: `--font-head` and `--font-body` are Manrope (`--font-serif`, `--font-display` alias it); `--font-num` is Geist Mono;
`--head-weight` is 800; `--font-code` unchanged.

Scale (px / line-height): page title 22–28 (800, `-0.02em`, one line; `page-title-fit.js` steps it down before it wraps); region
heading 21–24 (800); item heading 18 (800); body 15 (1.5); lead line under the band 17 (600); controls 14 (700); metadata
13; labels 11–12 (700 uppercase `0.08–0.12em`). Big counts 30 (800). Every number that changes is tabular
(`font-variant-numeric: tabular-nums`); a column of figures read down (a gradebook's Overall and category percentages) is
Manrope tabular, never mono. No italics except quoted email. No common ligatures (2026-10-05): Manrope's standard
ligatures draw "(c)" as ©, "->" and "<-" as arrows and " <3 " as a heart, which here are answer choices, parts and limits;
`typography.css` turns them off on every element, `!important` because a `font` shorthand resets them (test_design_language).

### 4. Components (`static/ledger.css`, linked last; `components.css`, `button-theme.css`, `form-theme.css`, `outline-theme.css`)

`ledger.css` states each component once, at the specificity it needs to win over the older sheets, and loads after them.

- **Buttons** (`.button`): white, `--frame`, square, 14 px 700 sentence case, `--hard-shadow`; hover lifts 1 px with
  `--hard-shadow-hover`; press sinks 2 px with no shadow (`--ease-out`, 80 ms). **Primary** (`.button.primary`, at most one
  per page header): the ink block with white type. **Danger** (`.danger-button`): red type in the frame. **Outward**
  (`.button[data-outward]`): `--outward-fill` behind the frame.
- **Text action** (`.text-button`, header secondary links): no box, 14 px 700 ink, 2 px underline at 3 px offset.
  **Go-link** (`.link-arrow`) and back link: 14 px 700 ink with the arrow, no underline. Hover stays ink (never orange,
  §1.2): a text action's underline thickens, a go-link gains one.
- **Tabs** (every shared tab group, `.assignment-nav`, `.support-tabs`, filter chips): framed words, 8 px apart; the chosen one
  an ink block with white type. They never move when chosen.
- **Chips** (`.chip`): 2 px frame in the tone's ink, 11 px 800 uppercase. The warning (needs-you) chip is the orange fill;
  the danger chip the red fill. Badges and counts (`.badge`, `.count-badge`, …) are chips with Geist Mono figures.
- **Callouts** (`.callout`): a framed box; warning, danger and success take a light tint of their tone; the title in the
  tone's ink, 16 px 800.
- **Regions and records**: a page region (`outline-theme.css` lists them) is ruled, not boxed: a 2 px ink rule above, no
  fill. A record in a grid of records (a tool, a room, a seating chart, an assessment, a scan) is a white box in the
  frame; one the whole card opens takes the hard shadow on hover. Rows inside a region are separated by `--nested-line`.
- **Needs-you chip**: a page's attention count (`header.set({attention: true})`) is the orange chip on the title's line in the
  band, the rest of that sentence muted after it (`#page-status`); requirement, 2026-10-04: "change all of those".
- **Tables**: the heading row on the canvas over a 2 px ink rule, 13 px 700 sentence case; cells 15 px; numeric columns
  right-aligned tabular; row hover `--surface-hover`; wide tables scroll inside their own box.
- **Fields**: label above in 11–12 px 700 uppercase; the input a white box in the frame, 15 px text; focus a 3 px slate ring
  at 1 px offset. Checkboxes use `accent-color: var(--ink)`.
- **Sub-tabs** (`.sub-tabs`; inside an assignment, Return's `.support-tabs`): the views inside one tab or page (instructor,
  2026-10-05: "Keep, as a sub-tabs component"): words over a hairline, the chosen one ink over a 2 px ink underline, never framed,
  so they never compete with the framed tabs above them; a count that waits on the instructor is the needs-you block. Stated once
  in `ledger.css`; a page adds the class, never its own override.
- **Folds** (`details.disclosure`): a 700 summary with a framed +/− after the words.
- **Dialogs, pop-overs, the command palette**: white in the frame with `--hard-shadow-strong`. **Toast**: the ink block,
  framed, with the hard shadow.
- **Step graphs and progress bars**: done steps slate, failures red, later steps `--faint`. The marked step on an overview card
  or tile is the next task, which waits on you: orange. In the workflow bar the marked step is the one on screen (where you
  are): ink, upright. Bars are `--nested-line` track, ink fill, orange for flagged work.

### 5. Shell (`layout.css`, `page-header.js`, `header-band.css`, `page-title-fit.js`, `workspace-navigation.css`, `ledger.css`)

- **Navigation rail**: black (`--sidebar-bg`) for the full height, the wordmark in white with an orange full stop, the
  "Search and go to" trigger a dark framed field, section labels in muted small capitals, items 14.5 px. The current page is
  an inverted block (canvas on black, 800); an attention count is an orange block with black mono figures; inventory counts
  are muted mono. The footer (Workspace settings, server status) sits on the same black above a rule. No arc, no clone of
  the menu, no texture.
- **Header band** (`.page-plate`): white, the full width of the page column, a 2 px ink rule under it, no photograph and
  no grain. First line: the breadcrumb (13 px 500 muted, current crumb ink), the clock (Geist Mono, muted; "Sun, Oct 4 · 2:31 PM", no seconds, year or time zone, moving once a
  minute), the area button (Ask about an area, a framed 34 px square) and the
  Assistant button (framed, hard shadow, 34 px tall). Second line: the page title (§3) with its label beside it on the baseline (11.5 px
  700 small capitals, muted; a view's own `label`, else the route's context line, less every part the breadcrumb already
  says, and no date, which the clock shows; never a student name). A lead under the band that only describes the page
  reads muted at 15 px; only a needs-you lead is strong. The band is 90 px or less and the same on every route. (The class names
  `.page-plate`, `.plate-type` and `.plate-label` remain from the Magazine edition; its photograph machinery is removed, §7.)
- Below the band, on the canvas: the page's actions, and on the left a lead line that only describes the page (muted, 15 px)
  when there is no needs-you chip; a page has no "Back to …" link where its trail already goes back (course pages: the trail's
  Courses crumb).

### 6. Motion

Presses and opens follow FRONTEND "Press and open motion" (reviewed against Emil Kowalski's standards, 2026-10-04): a framed
control lifts 1px under the pointer (its shadow 3 → 4px) and sinks 2px onto its shadow while pressed, in `--lift` (80ms
`--ease-out`, transform and box-shadow only, the shadow never animating through `none`); a still `::before` hit area keeps
edge clicks; the keyboard's state is the slate ring, never a lift, and a focused control keeps its face and its hard shadow
(critique 2026-10-05: the shared focus rule had dropped the shadow, so focused buttons looked pressed); text actions and tabs
never move, so the shared press scale is for framed controls only; hover lifts apply only where a pointer hovers
(`@media (hover: hover) and (pointer: fine)`); colour changes use `--snappy` (0.1s `ease`), never `--lift`; a toast comes in over
200ms. `prefers-reduced-motion`: no lift, sink or slide; colour and opacity
fades stay.

### 7. Withdrawn

- **Magazine edition** (2026-10-03 – 2026-10-04): the paper palette, Instrument Serif and Hedvig Letters Serif, the printed
  photograph plates and their generator's contrast gate, the dusk sidebar arc, the paper and film grain. Replaced by Ledger;
  the code is removed too (requirement, 2026-10-04: "remove the old plate and arc code too"): `plates.js`, `plates.css`
  (its band rules live on in `header-band.css`), `sidebar-arc.js`, `static/plates/`, `tools/plates/`, the `--plate-*` and
  `--arc-*` tokens and the views table's plate keys. `tests/test_design_language.py` keeps them out.
- **Paintings** (requirement, 2026-10-04: "remove paintings"): no page shows a painting.
- **Dark appearance** (requirement, 2026-10-03): none.

### 8. Checks

`tests/test_design_language.py` (static, in the full suite): no stylesheet or script references a removed Lambda token, a
theme switch, a painting or a width condition below 1180 px; the bundled fonts match their README. `tests/browser_theme.py`
and `tests/shell_checks.py`: one appearance on an OS that prefers dark; the black rail and the white header band on every
route; no photograph or arc drawn; text contrast; frames on clickables; seats and version palettes; print as plain ink;
forced colours. `tests/browser_shell_routes.py`: every reviewed route and dialog holds the shell and SENTENCE-CASE ACTIONS.
Screenshots for a review: every route and dialog at 1440 px (`/mnt/nvme/Teaching/tmp/ledger/all_shoot.py`, NVMe scratch).
