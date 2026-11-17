# Product

<!-- impeccable:product-schema 1 -->

Context for design tools (Impeccable and the other design skills). It summarises the repository's own rules and never
overrides them: `AGENTS.md` and the module contracts in `docs/decisions/` are binding, and where this file and they
disagree, they win. The visual system is `docs/decisions/design-language.md` (DESIGN-LANGUAGE), summarised in `DESIGN.md`.

## Platform

web

## Users

One user: the instructor, Chris Sinkule, who teaches several sections of MATH 140 at Texas A&M and runs every part of
the course from Faculty, alone, at a desktop browser (1440 and 1280 px wide windows). Students and TAs never sign in;
students meet Faculty only through what the instructor sends (email, Response Cards, Canvas and Gradescope posts).

## Product Purpose

Faculty is the instructor's single-user teaching workspace: course definitions, terms and sections; rosters,
accommodations, absences and student notes; printed papers, quizzes, exams, rooms, seating and exam day; scanned
submissions through the Inbox; automated grading with rubrics and the instructor's certification; the gradebook;
Gradescope, Canvas and iClicker; Gmail, communications and Google Calendar; follow-ups with students; and an in-app
Assistant. Success is the whole teaching pipeline running with as little of the instructor's time as possible while
every decision that matters stays theirs.

## Positioning

The pipeline is automated end to end (every unflagged paper proceeds), but the instructor's judgement is never
automated away: certification, sending, publishing and any outward write are explicit instructor actions, and anything
uncertain is flagged rather than guessed.

## Operating Context

A teaching day: Home's needs-you list, then the work it points to: scan review, grading review and certification,
student follow-ups and replies, office hours, printing and seating before an exam. Long sessions at a desk, many
students (hundreds per term), real deadlines. Pages are goal-first: each leads with the next step. Pages refresh quietly
in place while the instructor works.

## Capabilities and Constraints

- Stack: Python (Flask app served by waitress), a SQLite catalog, plain ES-module JavaScript in `static/`, hand-written
  CSS on shared tokens (`static/design-tokens.css`, `components.css`, `button-theme.css`), bundled fonts, a strict CSP
  (no inline styles or scripts). No frontend framework, build step or CSS framework.
- Desktop only, one appearance: minimum width 1180 px, no dark mode, no phone or tablet layout (DESIGN-LANGUAGE).
- Behaviour, routes, counts and APIs are governed by FRONTEND (`FRONTEND.md`, `docs/decisions/frontend.md`) and the
  module contracts; a visual change never changes them. Home and Exams are read-only projections.
- No login; the network boundary, same-origin writes and explicit Send actions are security invariants (ACCESS).
- Checks run against synthetic fixtures (`tests/`, `manage_checks.py`), never the live catalog.

## Brand Commitments

- The name is "faculty." (lower case, with the orange full stop) in the black rail's masthead.
- The look is "Ledger" (DESIGN-LANGUAGE, 2026-10-04, chosen for usability over the Magazine edition): an off-white page,
  black ink and 2px frames, a hard shadow on what you press, orange only for "needs you", heavy sans headings.
- Words: plain, specific, in the instructor's terms; mail to students sounds like the instructor, never like a policy
  recital (ASSISTANT REPLY VOICE).

## Evidence on Hand

- Synthetic data only for design work: the browser tests' seeds (`tests/browser_seating.py`, `tests/browser_gradebook.py`)
  and the demo server (`tests/tools/serve_port.sh PORT`). Live data are real students' records (FERPA): never put a live
  student's name, ID, email, grade or message into a mockup, a screenshot shared outside this machine, Figma or any
  other outside service.
- The design session's reference boards: `docs/design-language/mockups/`; open design requests: `docs/design-language/inbox.md`.
- No imagery: the Magazine edition's generated photograph plates (and their code) and the paintings were removed on 2026-10-04;
  none may be invented. Icons are the plain ink pictograms in `icons.js`.

## Product Principles

1. The instructor decides; Faculty prepares. Every outward or irreversible action is a visible, explicit control.
2. Goal first: a page leads with what needs the instructor next, and the rest steps back.
3. Calm under load: hundreds of students, quiet incremental refresh, nothing that moves or shouts without a reason.
4. One system: shared tokens and components everywhere; a page never invents its own look.
5. Nothing is hidden or silently lost: flags hold back only their own item; deleted or archived work stays a view.

## Accessibility & Inclusion

Text tokens meet 4.5:1 on the page; focus is a slate ring; keyboard shortcuts go through the central registry;
`prefers-reduced-motion` and forced colours are honoured. Small capitals are labels only: anything clickable is framed
or written as it reads (DESIGN-LANGUAGE §1.6).
