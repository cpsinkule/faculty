---
name: Faculty
description: The instructor's teaching workspace, kept like a ledger.
colors:
  canvas: "#F3F2EC"
  surface: "#FFFFFF"
  ink: "#101010"
  text: "#1E1E1E"
  muted: "#5A5A55"
  faint: "#8C8A82"
  line: "#CFCCC2"
  nested-line: "#E4E2DA"
  needs-fill: "#FF6A1A"
  needs-ink: "#101010"
  accent: "#C2410C"
  danger-fill: "#D7263D"
  danger-fill-ink: "#FFFFFF"
  outward-fill: "#DCE8FF"
  success: "#2E7D32"
  warning: "#9A4E00"
  error: "#C21F35"
  slate: "#3E5A66"
  sidebar: "#101010"
  sidebar-ink: "#F3F2EC"
  sidebar-muted: "#A8A79F"
typography:
  title:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  heading:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "21px"
    fontWeight: 800
    lineHeight: 1.15
  item:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 800
    lineHeight: 1.2
  count:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 800
    lineHeight: 1
    fontFeature: "\"tnum\""
  body:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.5
  control:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1.2
  label:
    fontFamily: "Manrope, Helvetica Neue, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0.1em"
  meta:
    fontFamily: "Geist Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "12.5px"
    fontWeight: 500
  code:
    fontFamily: "Berkeley Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
rounded:
  control: "0px"
  panel: "0px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "8": "32px"
  "10": "40px"
  "12": "48px"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    border: "2px solid {colors.ink}"
    shadow: "3px 3px 0 {colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "#FFFFFF"
  button-outward:
    backgroundColor: "{colors.outward-fill}"
    textColor: "{colors.ink}"
  tab-chosen:
    backgroundColor: "{colors.ink}"
    textColor: "#FFFFFF"
  needs-you:
    backgroundColor: "{colors.needs-fill}"
    textColor: "{colors.needs-ink}"
  failing:
    backgroundColor: "{colors.danger-fill}"
    textColor: "{colors.danger-fill-ink}"
  field:
    backgroundColor: "{colors.surface}"
    border: "2px solid {colors.ink}"
    typography: "{typography.body}"
  dialog:
    backgroundColor: "{colors.surface}"
    border: "2px solid {colors.ink}"
    shadow: "6px 6px 0 {colors.ink}"
  toast:
    backgroundColor: "{colors.ink}"
    textColor: "#FFFFFF"
---

# Design System: Faculty

This file summarises the binding screen contract, `docs/decisions/design-language.md` (DESIGN-LANGUAGE, "Ledger",
2026-10-04), and the shared tokens in `static/design-tokens.css`, for design tools. It is not a second authority: when it and
the contract (or FRONTEND, `FRONTEND.md`) disagree, the contract wins and this file is the one to fix. A change to the visual
system is made in the contract first, with the instructor's decision, then here.

## Overview

**Creative North Star: "A ledger for the teaching day"**

Chosen by the instructor on 2026-10-04 ("go with Ledger") after a usability audit found the Magazine edition "aesthetically
pleasing, but it does not make for a user friendly experience": important details did not stand out and irrelevant things
meshed with important ones. Ledger is neo-brutalist and plain on purpose: one off-white page, black ink, 2 px outlines, and
one loud colour that means "this waits on you". It is an Operate surface: dense, scannable work for one person over long
sessions. Nothing is decoration; a frame, a shadow or a fill always says something.

**Key Characteristics:**
- Off-white page, white things you work in, black ink, square corners.
- A frame means you can act; the hard shadow marks what you act on.
- Orange fill means "needs you", and nothing else is orange. Red fill means failing.
- Heavy sans headings (Manrope 800), Geist Mono for meta, tabular figures for every number that changes.
- A black navigation rail and a slim white header band; no photographs, no paintings, no texture.
- Small capitals are labels only: anything clickable is framed or written as it reads.
- One appearance, desktop only (minimum 1180 px).

## Colors

High contrast and low count: ink on off-white, one signal colour, one failure colour, one outward tint.

### Signals
- **Needs-you Orange** (#FF6A1A fill, black type): needs-you counts, rows, the chip beside a page's title, the next task on an overview card.
  As text on the page it is the darker **Orange Ink** (#C2410C).
- **Failing Red** (#D7263D fill, white type): failing, blocked, destructive. As text, #C21F35 with the word.
- **Outward Blue** (#DCE8FF): behind actions that leave Faculty (send, publish, push).
- **Slate** (#3E5A66): the focus ring, finished steps, information. Never orange for focus.

### Neutral
- **Page** (#F3F2EC): the canvas, flat.
- **Surface** (#FFFFFF): fields, framed records, dialogs, the header band.
- **Ink** (#101010): headings, frames, interactive text, the chosen tab, the primary button.
- **Body** (#1E1E1E), **Muted** (#5A5A55) for metadata and labels, **Faint** (#8C8A82) for disabled and decoration only.
- **Rule** (#CFCCC2) and **Nested Rule** (#E4E2DA): hairlines between groups and rows.
- **Rail** (#101010) with **Rail Ink** (#F3F2EC) and **Rail Muted** (#A8A79F).

### Status inks
- **Green** (#2E7D32) complete; **Amber** (#9A4E00) started; **Red** (#C21F35) failed, always with the word.

### Named Rules
**The One Signal Rule.** Orange means "needs you". Not the current page, not the chosen tab, not hover, not focus.
**The No-Hex Rule.** Page sheets carry no colour or radius of their own; they use the tokens. `ledger.css` states the
components once.

## Typography

**Heading and Body Font:** Manrope (with Helvetica Neue, system-ui): headings at 800, controls at 700, body at 500.
**Meta Font:** Geist Mono: the clock, rail counts, badges, IDs and dates in meta lines.
**Code Font:** Berkeley Mono: code, keyboard keys, raw records.

All families are bundled under `static/fonts/`; the app never fetches fonts from another site.

### Hierarchy
- **Page title** (800, 22–28 px, −0.02em): one line in the header band; it steps down before it wraps.
- **Heading** (800, 21–24 px): the heading that leads a page region; at most three per screen.
- **Item** (800, 18 px): record, card and list-item headings.
- **Count** (800, 30 px, tabular): big counts.
- **Body** (500, 15 px, 1.5); the chip beside the title 15 px 800; controls 14 px 700; metadata 13 px.
- **Label** (700, 11–12 px, uppercase, 0.08–0.12em): field labels, region labels, chips. Labels only.

### Named Rules
**The Small-Capitals Rule** (DESIGN-LANGUAGE §1.12). Small capitals are labels only. Text actions, go-links, back links,
breadcrumbs, tabs and folds are written as they read.
**The Tabular Rule.** Every number that changes uses tabular figures; a column of figures read down is Manrope tabular,
never mono.

## Layout

Desktop only: minimum width 1180 px; checks and screenshots at 1440 and 1280 px. A fixed black rail on the left (the
wordmark, the search, fourteen pages in lifecycle order) beside the page. Each page opens on a white header band (90 px or
less) with the breadcrumb, clock and Assistant on its first line and the title on its second; below it, on the page, the lead
line and the page's actions in one row, then the work, starting on the first screen. A 4 px spacing scale (4–48 px). Reading
text stays near 60–78 ch.

## Elevation & Depth

The floating Assistant chat (double-tap Command), the selected-area chat (hold Command), the selection hint and the docked
Assistant drawer are the instructor-approved **Assistant glass** exception
(DESIGN-LANGUAGE, 2026-10-08): light translucent material with backdrop blur, rounded edges, fine separators and soft
offset shadows. Its controls use the same material tokens; text, focus and action meanings stay shared. It falls back
to an opaque surface without backdrop-filter support, for reduced transparency and in forced colours. The drawer
keeps its docked position and full height and shares this material (requirement, 2026-10-08, DESIGN-LANGUAGE "Assistant
glass"); since 2026-10-09 its left edge drags its width (never under 480 px) and the floating chat resizes and can be put
away as its title bar; since 2026-10-10 the floating chat widens for a panel of its conversation's changes at its right, part
of the same glass. Every other surface keeps Ledger.

Within Assistant glass, messages name their speaker: You on a soft slate tint at the right, Assistant over left-aligned
answers on a denser reading surface. Conversation text is 16 px with open leading and a 70ch reading limit; metadata, actions and step review are smaller
(DESIGN-LANGUAGE, 2026-10-10, "the agent window is not easy to read or distinguish my text from the agent's").

Flat colour, hard shadows. There are no blurred shadows, glows or gradients. A solid ink shadow offset down-right says
"you can press this": 3 px on controls, 4 px on hover, none when pressed, 6 px on dialogs and pop-overs, which float above a
50% ink backdrop.

### Named Rules
**The Frame Rule.** A 2 px ink frame means you can act (or type). Regions are ruled with a 2 px ink line above, not boxed;
records in a grid are framed; text you cannot click is never framed.

## Shapes

Square everywhere: no radius on controls, panels, chips or dialogs.

## Components

### Buttons
- **Button** (`.button`): white in the frame, 14 px 700 sentence case, the 3 px hard shadow; hover lifts 1 px, press sinks.
- **Primary** (`.button.primary`, at most one per page header, panel or dialog): the ink block with white type.
- **Outward** (`.button[data-outward]`): the outward blue behind the frame.
- **Danger** (`.danger-button`): red type in the frame.
- **Text action** (`.text-button`): no box, 14 px 700 ink with a 2 px underline; hover thickens it.
- **Go-link** (`.link-arrow`) and back link: 14 px 700 ink with an arrow; hover underlines. Never orange.

### Chips and badges
11 px 800 uppercase in a 2 px frame of the tone's ink. The needs-you chip is the orange fill, the failing chip the red fill.
Counts are chips with Geist Mono figures.

### Tabs and filters
Framed words 8 px apart; the chosen one the ink block. They never move when chosen.

### Callouts
A framed box; warning, danger and success take a light tint of their tone; the title 16 px 800 in the tone's ink.

### Lists, records and tables
Rows on nested rules; regions under a 2 px ink rule. Tables: a 13 px 700 sentence-case header row on the page colour over a
2 px ink rule, 15 px cells, numbers right-aligned and tabular, row hover the surface tint.

### Fields
A label above in small capitals; the input a white box in the frame, 15 px text; focus the 3 px slate ring.

### Navigation
The rail: black, entries 14.5 px, the current page an inverted (page-colour) block, needs-you counts orange blocks with black
mono figures, inventory counts muted mono. The header band: white under a 2 px ink rule.

## Do's and Don'ts

### Do:
- **Do** use the tokens in `static/design-tokens.css` and the components in `ledger.css` before writing a page rule.
- **Do** lead each page with the decision that waits on the instructor, and make the primary button that next step.
- **Do** frame an action or write it as it reads; keep small capitals for labels.
- **Do** keep motion short and mechanical (`--ease-out`, 80–200 ms, transform and shadow only) and honour reduced motion.
- **Do** refresh in place without moving what the instructor is reading (FRONTEND: quiet incremental refresh).
- **Do** check changes at 1440 and 1280 px against synthetic data (`tests/tools/serve_port.sh`, the browser checks).

### Don't:
- **Don't** add a dark mode, a theme switch, or a phone or tablet layout; no width condition below 1180 px.
- **Don't** use orange for anything but "needs you", or red for anything but failing.
- **Don't** frame text that cannot be clicked, or put a hard shadow on something you cannot press.
- **Don't** add blurred shadows, glows, gradients, rounded corners, pills, photographs, paintings or textures.
- **Don't** load fonts, scripts or styles from another site, use inline styles (the CSP refuses them), or bring in a
  CSS framework or component library.
- **Don't** put a student's name, identifier or message on Home, in a header label, or in any mockup that leaves this
  machine.
